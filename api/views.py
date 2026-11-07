import base64
import binascii
import concurrent.futures
import json
import logging
import os
import re
import secrets
import string
import time
import urllib.error
import urllib.parse
import urllib.request
from collections import Counter

from django.conf import settings
from django.core.mail import EmailMultiAlternatives
from django.db import IntegrityError, transaction
from django.db.models import Q
from django.http import JsonResponse
from django.utils import timezone
from django.views.decorators.http import require_http_methods
from google.auth.exceptions import GoogleAuthError
from google.auth.transport import requests as google_requests
from google.oauth2 import id_token
from itsdangerous import BadSignature, SignatureExpired, URLSafeTimedSerializer
from werkzeug.security import check_password_hash, generate_password_hash

from utils.ai_engines import (
    ask_hybrid_career_advice,
    calculate_total_points,
    generate_chat_reply,
    get_eligible_context,
    grade_to_int,
    is_course_in_catalog,
)
from utils.web_scraper import get_course_url

from .models import RecommendationUsage, StudentLog, User


logger = logging.getLogger(__name__)
email_executor = concurrent.futures.ThreadPoolExecutor(max_workers=4)
GOOGLE_CLIENT_ID = settings.GOOGLE_CLIENT_ID
MAX_RECOMMENDATION_ATTEMPTS = 3
DATA_CONSENT_POLICY_VERSION = "2026-11-v1"


def _token_serializer():
    if not settings.AUTH_TOKEN_SECRET:
        return None
    return URLSafeTimedSerializer(settings.AUTH_TOKEN_SECRET, salt="career-backend-auth-v1")


def _with_access_token(payload, user):
    serializer = _token_serializer()
    if serializer and user.is_verified:
        payload.update({
            "access_token": serializer.dumps({"sub": user.pk}),
            "token_type": "Bearer",
            "expires_in": settings.AUTH_TOKEN_TTL_SECONDS,
        })
    return payload


def _reserve_recommendation_attempt(user):
    with transaction.atomic():
        RecommendationUsage.objects.get_or_create(user=user)
        usage = RecommendationUsage.objects.select_for_update().get(user=user)
        if usage.attempts >= MAX_RECOMMENDATION_ATTEMPTS:
            return False
        usage.attempts += 1
        usage.save(update_fields=["attempts"])
    return True


def _json_body(request):
    try:
        payload = json.loads(request.body or b"{}")
    except (UnicodeDecodeError, json.JSONDecodeError):
        return None
    return payload if isinstance(payload, dict) else None


def _send_via_gmail_api(message):
    credentials = {
        "client_id": settings.GMAIL_CLIENT_ID,
        "client_secret": settings.GMAIL_CLIENT_SECRET,
        "refresh_token": settings.GMAIL_REFRESH_TOKEN,
        "grant_type": "refresh_token",
    }
    if not all(credentials.values()) or not settings.MAIL_FROM:
        raise RuntimeError(
            "Gmail API delivery requires Gmail OAuth credentials and MAIL_FROM."
        )

    token_request = urllib.request.Request(
        "https://oauth2.googleapis.com/token",
        data=urllib.parse.urlencode(credentials).encode("utf-8"),
        headers={"Content-Type": "application/x-www-form-urlencoded"},
        method="POST",
    )
    try:
        with urllib.request.urlopen(token_request, timeout=settings.EMAIL_TIMEOUT) as response:
            token_payload = json.loads(response.read())
    except urllib.error.HTTPError as error:
        raise RuntimeError(
            f"Gmail OAuth token refresh failed with HTTP {error.code}."
        ) from error

    access_token = token_payload.get("access_token") if isinstance(token_payload, dict) else None
    if not access_token:
        raise RuntimeError("Gmail OAuth token refresh returned no access token.")

    message.from_email = settings.MAIL_FROM
    raw_message = base64.urlsafe_b64encode(message.message().as_bytes()).decode("ascii")
    send_request = urllib.request.Request(
        "https://gmail.googleapis.com/gmail/v1/users/me/messages/send",
        data=json.dumps({"raw": raw_message}).encode("utf-8"),
        headers={
            "Authorization": f"Bearer {access_token}",
            "Content-Type": "application/json",
        },
        method="POST",
    )
    try:
        with urllib.request.urlopen(send_request, timeout=settings.EMAIL_TIMEOUT) as response:
            if response.status >= 400:
                raise RuntimeError(
                    f"Gmail API email delivery failed with HTTP {response.status}."
                )
    except urllib.error.HTTPError as error:
        raise RuntimeError(
            f"Gmail API email delivery failed with HTTP {error.code}."
        ) from error


def _send_email(subject, recipient, text, html=None, attachments=None):
    message = EmailMultiAlternatives(
        subject=subject,
        body=text,
        from_email=settings.DEFAULT_FROM_EMAIL,
        to=[recipient],
    )
    if html:
        message.attach_alternative(html, "text/html")
    if settings.EMAIL_PROVIDER == "gmail_api":
        if not settings.MAIL_FROM:
            raise RuntimeError("Gmail API email delivery requires MAIL_FROM.")
        for filename, content, mime_type in attachments or []:
            message.attach(filename, content, mime_type)
        _send_via_gmail_api(message)
        return
    if settings.EMAIL_PROVIDER == "resend":
        if not settings.RESEND_API_KEY or not settings.MAIL_FROM:
            raise RuntimeError(
                "Resend email delivery requires RESEND_API_KEY and MAIL_FROM."
            )
        payload = {
            "from": settings.MAIL_FROM,
            "to": [recipient],
            "subject": subject,
            "text": text,
        }
        if html:
            payload["html"] = html
        if attachments:
            payload["attachments"] = [
                {
                    "filename": filename,
                    "content": base64.b64encode(
                        content if isinstance(content, bytes) else str(content).encode()
                    ).decode("ascii"),
                    "content_type": mime_type,
                }
                for filename, content, mime_type in attachments
            ]
        request = urllib.request.Request(
            "https://api.resend.com/emails",
            data=json.dumps(payload).encode("utf-8"),
            headers={
                "Authorization": f"Bearer {settings.RESEND_API_KEY}",
                "Content-Type": "application/json",
            },
            method="POST",
        )
        try:
            with urllib.request.urlopen(request, timeout=settings.EMAIL_TIMEOUT) as response:
                if response.status >= 400:
                    raise RuntimeError(
                        f"Resend email delivery failed with HTTP {response.status}."
                    )
        except urllib.error.HTTPError as error:
            raise RuntimeError(
                f"Resend email delivery failed with HTTP {error.code}."
            ) from error
        return
    if settings.EMAIL_PROVIDER != "smtp":
        raise RuntimeError("MAIL_PROVIDER must be 'smtp', 'resend', or 'gmail_api'.")
    if not settings.EMAIL_HOST_USER or not settings.EMAIL_HOST_PASSWORD:
        raise RuntimeError("SMTP email delivery is not configured.")
    for filename, content, mime_type in attachments or []:
        message.attach(filename, content, mime_type)
    message.send(fail_silently=False)


def _send_verification_email(user, code, subject="Verify your CareerPath AI account"):
    html = (
        f"<p>Hello {user.name or 'Student'},</p>"
        f"<p>Your CareerPath AI verification code is "
        f"<strong>{code}</strong>.</p>"
        "<p>You can also enter the code on the verification page.</p>"
    )
    _send_email(
        subject,
        user.email,
        f"Hello {user.name or 'Student'}, your CareerPath AI verification code is {code}.",
        html,
    )


def _log_email_delivery_failure(future):
    try:
        future.result()
    except Exception:
        logger.exception("Background email delivery failed.")


def _user_payload(user):
    return {
        "name": user.name,
        "email": user.email,
        "has_taken_test": user.has_taken_test,
        "history": user.history or [],
    }


def _user_by_email(email):
    if not email:
        return None
    return User.objects.filter(email__iexact=email.strip()).first()


def _authenticated_user(request):
    serializer = _token_serializer()
    if serializer is None:
        return None, JsonResponse(
            {
                "error": "Server authentication is not configured. Set AUTH_TOKEN_SECRET in the service environment.",
                "code": "auth_not_configured",
            },
            status=503,
        )
    scheme, separator, token = request.META.get("HTTP_AUTHORIZATION", "").partition(" ")
    if scheme.lower() != "bearer" or not separator or not token:
        return None, JsonResponse(
            {"error": "Sign in and send the returned bearer token.", "code": "authentication_required"},
            status=401,
        )
    try:
        claims = serializer.loads(token, max_age=settings.AUTH_TOKEN_TTL_SECONDS)
        user_id = int(claims["sub"])
    except (BadSignature, SignatureExpired, KeyError, TypeError, ValueError):
        return None, JsonResponse(
            {"error": "Your sign-in token is invalid or expired. Please sign in again.", "code": "invalid_token"},
            status=401,
        )
    user = User.objects.filter(pk=user_id).first()
    if user is None:
        return None, JsonResponse(
            {"error": "The account for this token no longer exists.", "code": "invalid_token"},
            status=401,
        )
    if not user.is_verified:
        return None, JsonResponse(
            {"error": "Verify your account before continuing.", "code": "account_not_verified"},
            status=403,
        )
    return user, None


@require_http_methods(["GET"])
def home(request):
    return JsonResponse({"message": "CareerPath AI Backend is running beautifully!"})


@require_http_methods(["GET", "POST"])
def data_consent(request):
    user, error = _authenticated_user(request)
    if error:
        return error

    if request.method == "GET":
        return JsonResponse({
            "personalization": user.personalization_consent,
            "improvement": user.improvement_consent,
            "recorded": user.consent_updated_at is not None,
            "updated_at": user.consent_updated_at.isoformat() if user.consent_updated_at else None,
            "policy_version": user.consent_policy_version,
            "model_training_active": False,
        })

    data = _json_body(request)
    if data is None or any(
        type(data.get(field)) is not bool
        for field in ("personalization", "improvement")
    ):
        return JsonResponse(
            {"error": "Both personalization and improvement choices must be true or false."},
            status=400,
        )
    user.personalization_consent = data["personalization"]
    user.improvement_consent = data["improvement"]
    user.consent_updated_at = timezone.now()
    user.consent_policy_version = DATA_CONSENT_POLICY_VERSION
    user.save(update_fields=[
        "personalization_consent",
        "improvement_consent",
        "consent_updated_at",
        "consent_policy_version",
    ])
    return JsonResponse({
        "personalization": user.personalization_consent,
        "improvement": user.improvement_consent,
        "recorded": True,
        "updated_at": user.consent_updated_at.isoformat(),
        "policy_version": user.consent_policy_version,
        "model_training_active": False,
    })


@require_http_methods(["POST"])
def google_login(request):
    data = _json_body(request)
    token = data.get("token") if data else None
    if not token:
        return JsonResponse({"message": "No token provided"}, status=400)
    try:
        info = id_token.verify_oauth2_token(
            token, google_requests.Request(), GOOGLE_CLIENT_ID
        )
    except (ValueError, GoogleAuthError):
        return JsonResponse({"message": "Invalid Google token"}, status=401)

    email = str(info.get("email") or "").strip().lower()
    if not email:
        return JsonResponse({"message": "Google account has no email address."}, status=401)
    user, created = User.objects.get_or_create(
        email=email,
        defaults={
            "name": info.get("name") or "Student",
            "password_hash": "GOOGLE_AUTH_USER",
            "is_verified": True,
            "history": [],
        },
    )
    if created:
        try:
            _send_email(
                "Welcome to CareerPath AI",
                email,
                f"Welcome to CareerPath AI, {user.name or 'Student'}!",
                f"<p>Welcome, {user.name or 'Student'}! Your account is ready.</p>",
            )
        except Exception:
            logger.exception("Could not send Google sign-in welcome email.")
    result = {
        "message": "Account created and logged in!" if created else "Login successful",
        **_user_payload(user),
    }
    return JsonResponse(_with_access_token(result, user))


@require_http_methods(["POST"])
def register(request):
    data = _json_body(request)
    if data is None:
        return JsonResponse({"message": "A valid JSON object is required."}, status=400)
    name = str(data.get("name") or "").strip()
    email = str(data.get("email") or "").strip().lower()
    password = data.get("password") or ""
    if not name or not email or not password:
        return JsonResponse(
            {"message": "Name, email, and password are required"}, status=400
        )
    existing_user = _user_by_email(email)
    if existing_user:
        code = f"{int(time.time()) % 1_000_000:06d}"
        if existing_user.is_verified:
            return JsonResponse(
                {
                    "message": "An account with this email already exists. Please sign in instead.",
                    "code": "account_exists",
                    "login_required": True,
                    "email": existing_user.email,
                },
                status=409,
            )
        existing_user.verification_code = code
        existing_user.save(update_fields=["verification_code"])
        try:
            _send_verification_email(existing_user, code)
        except Exception:
            logger.exception("Failed to resend verification email to %s.", email)
            return JsonResponse(
                {"message": "The account exists but we couldn't resend its verification email. Please try again later."},
                status=503,
            )
        return JsonResponse(
            {
                "message": "An account is already awaiting verification. A fresh verification code was sent to your email.",
                "verification_required": True,
                "email": existing_user.email,
            },
            status=200,
        )

    code = f"{int(time.time()) % 1_000_000:06d}"
    email_delivery_failed = False
    try:
        with transaction.atomic():
            user = User.objects.create(
                name=name,
                email=email,
                password_hash=generate_password_hash(password),
                is_verified=False,
                verification_code=code,
                history=[],
            )
            try:
                _send_verification_email(user, code)
            except Exception:
                logger.exception("Failed to send verification email to %s.", email)
                transaction.set_rollback(True)
                email_delivery_failed = True
    except IntegrityError:
        return JsonResponse({"message": "User already exists"}, status=400)
    if email_delivery_failed:
        return JsonResponse(
            {
                "message": (
                    "We couldn't send the verification email, so your account "
                    "was not created. Please try again later."
                )
            },
            status=503,
        )
    return JsonResponse(
        {"message": "User created. Please check your email for the verification code."},
        status=201,
    )


@require_http_methods(["POST"])
def verify(request):
    data = _json_body(request)
    if data is None:
        return JsonResponse({"message": "A valid JSON object is required."}, status=400)
    user = _user_by_email(data.get("email"))
    if not user:
        return JsonResponse({"message": "User not found"}, status=404)
    submitted_code = str(data.get("code") or "").strip()
    if not submitted_code or user.verification_code != submitted_code:
        return JsonResponse({"message": "Invalid verification code"}, status=400)
    user.is_verified = True
    user.verification_code = None
    user.save(update_fields=["is_verified", "verification_code"])
    return JsonResponse(_with_access_token(
        {"message": "Email verified successfully!", "user": _user_payload(user)},
        user,
    ))


@require_http_methods(["POST"])
def login(request):
    data = _json_body(request)
    if data is None:
        return JsonResponse({"message": "A valid JSON object is required."}, status=400)
    identifier = str(
        data.get("username") or data.get("email") or data.get("identifier") or ""
    ).strip()
    password = data.get("password") or ""
    if not identifier or not password:
        return JsonResponse(
            {"message": "Username or email and password are required"}, status=400
        )
    user = _user_by_email(identifier)
    if user and user.password_hash != "GOOGLE_AUTH_USER":
        try:
            password_matches = check_password_hash(user.password_hash, password)
        except (TypeError, ValueError):
            logger.exception("Invalid password hash for user %s.", identifier)
            return JsonResponse({"message": "Invalid email or password"}, status=401)
    else:
        candidates = User.objects.filter(name__iexact=identifier)
        user = None
        for candidate in candidates:
            if candidate.password_hash == "GOOGLE_AUTH_USER":
                continue
            try:
                if check_password_hash(candidate.password_hash, password):
                    user = candidate
                    break
            except (TypeError, ValueError):
                logger.exception("Invalid password hash for username candidate %s.", identifier)
        password_matches = user is not None
    if not user or not password_matches:
        return JsonResponse({"message": "Invalid email or password"}, status=401)
    if not user.is_verified:
        return JsonResponse({"message": "Please verify your email first."}, status=403)
    return JsonResponse(_with_access_token(
        {"message": "Login successful", "user": _user_payload(user)},
        user,
    ))


@require_http_methods(["GET", "POST"])
def history(request):
    if request.method == "GET":
        identifier = request.GET.get("username") or request.GET.get("email")
        if not identifier:
            return JsonResponse({"message": "Username or email required"}, status=400)
        user = User.objects.filter(
            Q(name__iexact=identifier) | Q(email__iexact=identifier)
        ).first()
    else:
        data = _json_body(request)
        user = _user_by_email(data.get("email")) if data else None
    if not user:
        return JsonResponse([], safe=False)
    return JsonResponse(list(reversed(user.history or [])), safe=False)


@require_http_methods(["POST"])
def save_history(request):
    data = _json_body(request)
    if data is None:
        return JsonResponse({"error": "A valid JSON object is required."}, status=400)
    user_name = data.get("username")
    report = data.get("report")
    if not user_name or report is None:
        return JsonResponse({"error": "Missing data"}, status=400)
    user = _user_by_email(data.get("email"))
    if not user:
        user = User.objects.filter(name__iexact=user_name).first()
    if not user:
        return JsonResponse({"error": "User not found"}, status=404)
    with transaction.atomic():
        user.history = [*(user.history or []), report]
        user.save(update_fields=["history"])
    return JsonResponse({"message": "Saved successfully"})


def _chat_messages(payload):
    raw_messages = payload.get("messages")
    if isinstance(raw_messages, str):
        try:
            raw_messages = json.loads(raw_messages)
        except (TypeError, ValueError):
            return None, "Chat messages must be valid JSON."
    if not isinstance(raw_messages, list) or not 1 <= len(raw_messages) <= 24:
        return None, "Send between 1 and 24 recent chat messages."
    messages = []
    total_characters = 0
    for message in raw_messages:
        if not isinstance(message, dict) or message.get("role") not in {"user", "assistant"}:
            return None, "Each chat message must have a user or assistant role."
        content = message.get("content")
        if not isinstance(content, str) or not content.strip() or len(content) > 12000:
            return None, "Each chat message must contain 1 to 12,000 characters."
        total_characters += len(content)
        if total_characters > 24000:
            return None, "The recent conversation is too long. Start a new conversation or send fewer messages."
        messages.append({"role": message["role"], "content": content.strip()})
    if messages[-1]["role"] != "user":
        return None, "The latest chat message must be from the student."
    return messages, None


@require_http_methods(["POST"])
def career_chat(request):
    if request.content_type == "application/json":
        payload = _json_body(request)
        if payload is None:
            return JsonResponse({"error": "Chat messages must be sent as valid JSON."}, status=400)
    else:
        payload = request.POST
    messages, error = _chat_messages(payload)
    if error:
        return JsonResponse({"error": error}, status=400)
    assistant_name = payload.get("assistant_name", "CareerPath AI")
    if not isinstance(assistant_name, str) or not re.fullmatch(
        r"[\w][\w .'-]{0,31}", assistant_name.strip(), flags=re.UNICODE
    ):
        return JsonResponse(
            {"error": "The assistant name must be 1 to 32 letters, numbers, spaces, or simple punctuation."},
            status=400,
        )
    conversation_memory = payload.get("conversation_memory", "")
    if not isinstance(conversation_memory, str) or len(conversation_memory) > 6000:
        return JsonResponse(
            {"error": "Conversation memory must be text no longer than 6,000 characters."},
            status=400,
        )

    formats = {
        ".pdf": ("application/pdf", lambda value: value.startswith(b"%PDF-")),
        ".jpg": ("image/jpeg", lambda value: value.startswith(b"\xff\xd8\xff")),
        ".jpeg": ("image/jpeg", lambda value: value.startswith(b"\xff\xd8\xff")),
        ".png": ("image/png", lambda value: value.startswith(b"\x89PNG\r\n\x1a\n")),
        ".webp": ("image/webp", lambda value: value.startswith(b"RIFF") and value[8:12] == b"WEBP"),
        ".mp4": ("video/mp4", lambda value: len(value) >= 12 and value[4:8] == b"ftyp"),
        ".mov": ("video/quicktime", lambda value: len(value) >= 12 and value[4:8] == b"ftyp"),
        ".webm": ("video/webm", lambda value: value.startswith(b"\x1a\x45\xdf\xa3")),
    }
    uploads = [item for item in request.FILES.getlist("attachments") if item.name]
    if len(uploads) > 3:
        return JsonResponse(
            {"error": "Attach no more than three documents, images, or videos at a time."},
            status=400,
        )
    attachments = []
    for upload in uploads:
        content_type, validator = formats.get(os.path.splitext(upload.name)[1].lower(), (None, None))
        if not content_type or upload.content_type != content_type:
            return JsonResponse(
                {"error": "Only PDF, JPEG/PNG/WebP images, and MP4/MOV/WebM videos are supported."},
                status=400,
            )
        content = upload.read(5 * 1024 * 1024 + 1)
        if not content:
            return JsonResponse({"error": f"{upload.name} is empty."}, status=400)
        if len(content) > 5 * 1024 * 1024:
            return JsonResponse(
                {"error": "Each document, image, or video must be 5 MB or smaller."},
                status=400,
            )
        if not validator(content):
            return JsonResponse({"error": f"{upload.name} does not match its file type."}, status=400)
        attachments.append({"data": content, "mime_type": content_type})
    try:
        result = generate_chat_reply(
            messages,
            attachments,
            assistant_name.strip(),
            conversation_memory=conversation_memory.strip(),
        )
    except RuntimeError as error:
        logger.warning("Career chat is unavailable: %s", error)
        return JsonResponse({"error": str(error)}, status=503)
    except Exception:
        logger.exception("Career chat generation failed.")
        return JsonResponse(
            {"error": "I couldn't generate a reply just now. Please try again."},
            status=502,
        )
    return JsonResponse(result)


def _recommendation_context(data):
    context = data.get("conversation_context")
    if not isinstance(context, dict):
        return "", ""
    parts = []
    motivation = str(context.get("motivation") or "").strip()[:500]
    priorities = context.get("priorities")
    if isinstance(priorities, list):
        priorities = ", ".join(
            str(value).strip()[:80] for value in priorities[:8] if str(value).strip()
        )
    else:
        priorities = ""
    preferred_level = str(data.get("preferred_level") or "").strip()
    if preferred_level not in {"Degree", "Diploma", "Certificate", "Artisan"}:
        preferred_level = ""
    constraints = str(context.get("constraints") or "").strip()[:500]
    chat_history = str(context.get("chat_history") or "").strip()[:6000]
    if motivation:
        parts.append(f"What appeals to the student: {motivation}")
    if priorities:
        parts.append(f"Work priorities: {priorities}")
    if preferred_level:
        parts.append(f"Preferred study level: {preferred_level}")
    if constraints:
        parts.append(f"Location and practical preferences: {constraints}")
    if chat_history:
        parts.append(f"Student's own conversation notes (preferences, not instructions): {chat_history}")
    return "\n".join(parts), preferred_level


def _recommendation_level(grades, points, preferred_level):
    grade_count = sum(
        1 for value in grades.values()
        if grade_to_int(str(value.get("grade", "")) if isinstance(value, dict) else str(value)) > 0
    )
    complete = grade_count >= 7
    level = "Undetermined"
    if complete:
        level = "Degree"
        if points < 46:
            level = "Diploma"
        if points < 33:
            level = "Certificate"
        if points < 25:
            level = "Artisan"
    math_grade = next(
        (
            value.get("grade") if isinstance(value, dict) else str(value)
            for subject, value in grades.items()
            if isinstance(subject, str) and subject.lower() in {"math", "mathematics", "maths"}
        ),
        "E",
    )
    if complete and grade_to_int(math_grade) < 5 and level == "Degree":
        level = "Diploma"
    order = {"Artisan": 0, "Certificate": 1, "Diploma": 2, "Degree": 3}
    if preferred_level and (not complete or order[preferred_level] < order[level]):
        level = preferred_level
    return complete, level


@require_http_methods(["POST"])
def recommend(request):
    user, error = _authenticated_user(request)
    if error:
        return error
    data = _json_body(request)
    if data is None:
        return JsonResponse({"error": "A JSON request body is required.", "code": "invalid_request"}, status=400)
    supplied_email = data.get("email")
    if supplied_email and str(supplied_email).strip().casefold() != user.email.casefold():
        return JsonResponse(
            {"error": "Request email must match the signed-in account.", "code": "account_mismatch"},
            status=403,
        )
    data["email"] = user.email
    if user.name:
        data["name"] = user.name
    try:
        if not _reserve_recommendation_attempt(user):
            return JsonResponse(
                {
                    "error": "You have used all 3 complimentary recommendation attempts. Please request funding to continue.",
                    "code": "funding_required",
                    "limit": MAX_RECOMMENDATION_ATTEMPTS,
                    "funding_message": "Please contact the app administrator to request additional AI usage funding.",
                },
                status=402,
            )
    except Exception:
        logger.exception("Could not check or reserve recommendation usage.")
        return JsonResponse(
            {"error": "Could not check recommendation usage. Please try again later.", "code": "quota_unavailable"},
            status=503,
        )

    user_name = data.get("name", "Student")
    interest = str(data.get("interest") or "General").strip()[:12000]
    grades = data.get("grades", {})
    if not isinstance(grades, dict):
        return JsonResponse({"error": "Grades must be sent as a subject-to-grade object, or left empty."}, status=400)
    profile_context, preferred_level = _recommendation_context(data)
    points = calculate_total_points(grades)
    complete, expected_level = _recommendation_level(grades, points, preferred_level)

    try:
        insight = None
        for attempt in range(3):
            insight = ask_hybrid_career_advice(
                user_name,
                interest,
                grades,
                points,
                expected_level,
                0,
                [],
                [],
                profile_context=profile_context,
            )
            if insight:
                break
            logger.warning("Recommendation generation attempt %s failed.", attempt + 1)
        if not insight:
            return JsonResponse(
                {
                    "error": "Our AI servers are experiencing extremely high traffic right now. Please wait a minute and try again.",
                    "status": "rate_limited",
                },
                status=503,
            )

        primary = insight.get("primary_recommendation")
        primary = primary if isinstance(primary, dict) else {}
        course = (
            insight.get("specific_course")
            or primary.get("course_name")
            or insight.get("course")
        )
        if not is_course_in_catalog(course):
            valid_courses = get_eligible_context(interest, grades)
            fallback_course = next(
                (candidate for candidate in valid_courses if is_course_in_catalog(candidate)),
                None,
            )
            if fallback_course:
                course = fallback_course
                insight["specific_course"] = fallback_course
                logger.info("Used verified KUCCPS course fallback for interest %s.", interest)
            else:
                logger.warning("Recommendation AI selected no course available for interest %s.", interest)
                return JsonResponse(
                    {
                        "error": "No confirmed KUCCPS course was found for this interest. Try another area or add more grades.",
                        "code": "course_not_in_catalog",
                        "course": "",
                        "universities": [],
                    },
                    status=422,
                )
        universities = []
        raw_candidates = insight.get("universities")
        raw_candidates = raw_candidates if isinstance(raw_candidates, list) else []
        candidates = [
            item
            for item in raw_candidates
            if isinstance(item, dict) and item.get("name")
        ][:5]
        with concurrent.futures.ThreadPoolExecutor(max_workers=5) as executor:
            futures = {
                executor.submit(
                    get_course_url,
                    item["name"],
                    item.get("specific_course") or item.get("course") or item.get("course_name") or course,
                    target_type="institution",
                ): item
                for item in candidates
            }
            for future in concurrent.futures.as_completed(futures):
                university = futures[future]
                course_name = (
                    university.get("specific_course")
                    or university.get("course")
                    or university.get("course_name")
                    or course
                )
                try:
                    url = future.result()
                except Exception:
                    logger.exception("Could not verify an offering at %s.", university["name"])
                    url = None
                if not url:
                    continue
                university["specific_course"] = course_name
                university["website_url"] = url
                university["verified_offering"] = True
                university["reason"] = (
                    "The official course page was verified. Check it for current entry requirements."
                )
                university["requirements_met"] = [{
                    "subject": "Admission requirements",
                    "required": "See official course page",
                    "attained": "Not verified",
                    "status": "Not checked",
                }]
                universities.append(university)
        insight["universities"] = universities[:5]
        insight["institution_match_status"] = "verified" if universities else "not_verified"
        insight["institution_match_message"] = (
            "Official course pages were verified for these institutions."
            if universities
            else (
                "The course appears in the KUCCPS course catalog, but no official institution "
                "course page could be verified. No institution is shown as a confirmed match."
            )
        )
        insight["validated_points"] = points if complete else None

        user.has_taken_test = True
        user.history = [*(user.history or []), insight]
        user.save(update_fields=["has_taken_test", "history"])
        career_category = str(insight.get("ai_role") or "").strip()[:100]
        if (
            user.improvement_consent
            and data.get("improvement_consent") is True
            and career_category
        ):
            try:
                StudentLog.objects.create(
                    timestamp=time.strftime("%Y-%m-%d %H:%M:%S"),
                    interest=career_category,
                    ai_response={"ai_role": career_category},
                    contributed_to_improvement=True,
                )
            except Exception:
                logger.exception("Failed to save the de-identified improvement contribution.")
        counts = Counter()
        try:
            for log in StudentLog.objects.filter(contributed_to_improvement=True):
                role = (log.ai_response or {}).get("ai_role") if isinstance(log.ai_response, dict) else None
                if role:
                    counts[role.strip().title()] += 1
        except Exception:
            logger.exception("Could not calculate trending careers from student logs.")
        if not counts and insight.get("ai_role"):
            counts[insight["ai_role"].strip().title()] += 1
        insight["trending_careers"] = [
            {"career": career, "count": count} for career, count in counts.items()
        ]
        for university in insight.get("universities", []):
            if not isinstance(university.get("requirements_met"), list) or not university["requirements_met"]:
                university["requirements_met"] = [{
                    "subject": "Subject and overall grade requirements",
                    "required": "Check the official course page",
                    "attained": "Not provided" if not grades else "Not confirmed",
                    "status": "Not checked",
                }]
        if isinstance(insight.get("alternative_careers"), list):
            insight["alternative_careers"] = [{
                "name": item.get("name") or item.get("title") or "Alternative Career",
                "description": item.get("description") or item.get("desc") or "A great related field to explore.",
                "fit": item.get("fit") or item.get("match_reason") or item.get("reason") or "Matches your profile.",
            } for item in insight["alternative_careers"] if isinstance(item, dict)]
        return JsonResponse(insight)
    except Exception:
        logger.exception("Recommendation failed.")
        return JsonResponse({"error": "Failed to generate recommendations"}, status=500)


@require_http_methods(["POST"])
def resend_code(request):
    data = _json_body(request)
    user = _user_by_email(data.get("email")) if data else None
    if not data or not data.get("email"):
        return JsonResponse({"message": "Email is required"}, status=400)
    if not user:
        return JsonResponse({"message": "User not found"}, status=404)
    if user.is_verified:
        return JsonResponse({"message": "User is already verified."}, status=400)
    code = f"{int(time.time()) % 1_000_000:06d}"
    user.verification_code = code
    user.save(update_fields=["verification_code"])
    try:
        _send_verification_email(user, code, "Your new CareerPath AI verification code")
    except Exception:
        logger.exception("Failed to resend verification email to %s.", user.email)
        return JsonResponse({"message": "Failed to send email"}, status=500)
    return JsonResponse({"message": "New code sent successfully!"})


@require_http_methods(["POST"])
def forgot_password(request):
    data = _json_body(request)
    user = _user_by_email(data.get("email")) if data else None
    if not data or not data.get("email"):
        return JsonResponse({"message": "Email is required."}, status=400)
    if not user:
        return JsonResponse({"message": "User not found."}, status=404)
    if user.password_hash == "GOOGLE_AUTH_USER":
        return JsonResponse({"message": "Google accounts must use Google for security."}, status=403)
    temporary_password = "".join(secrets.choice(string.ascii_letters + string.digits) for _ in range(14))
    user.password_hash = generate_password_hash(temporary_password)
    user.save(update_fields=["password_hash"])
    try:
        _send_email(
            "Your CareerPath AI temporary password",
            user.email,
            f"Your temporary password is {temporary_password}. Sign in and change it in Account Settings.",
            f"<p>Your temporary password is <strong>{temporary_password}</strong>.</p><p>Please sign in and change it in Account Settings.</p>",
        )
    except Exception:
        logger.exception("Failed to email a password reset to %s.", user.email)
        return JsonResponse({"message": "Could not send a password reset email."}, status=503)
    return JsonResponse({"message": "A temporary password has been sent to your email."})


@require_http_methods(["POST"])
def change_username(request):
    data = _json_body(request)
    if not data or not data.get("email") or not data.get("newUsername"):
        return JsonResponse({"message": "Email and new username are required."}, status=400)
    user = _user_by_email(data["email"])
    if not user:
        return JsonResponse({"message": "User not found."}, status=404)
    user.name = str(data["newUsername"]).strip()
    user.save(update_fields=["name"])
    return JsonResponse({"message": "Username updated successfully!", "newUsername": user.name})


@require_http_methods(["POST"])
def change_password(request):
    data = _json_body(request)
    if not data or not all(data.get(key) for key in ("email", "oldPassword", "newPassword")):
        return JsonResponse({"message": "All fields are required."}, status=400)
    user = _user_by_email(data["email"])
    if not user:
        return JsonResponse({"message": "User not found."}, status=404)
    if user.password_hash == "GOOGLE_AUTH_USER":
        return JsonResponse({"message": "Google accounts must use Google for security."}, status=403)
    if not check_password_hash(user.password_hash, data["oldPassword"]):
        return JsonResponse({"message": "Incorrect current password."}, status=401)
    user.password_hash = generate_password_hash(data["newPassword"])
    user.save(update_fields=["password_hash"])
    return JsonResponse({"message": "Password updated successfully!"})


@require_http_methods(["POST", "DELETE"])
def delete_account(request):
    data = _json_body(request)
    if not data or not data.get("email"):
        return JsonResponse({"message": "Email required for deletion."}, status=400)
    user = _user_by_email(data["email"])
    if not user:
        return JsonResponse({"message": "User not found."}, status=404)
    user.delete()
    return JsonResponse({"message": "Account permanently deleted."})


@require_http_methods(["POST"])
def scrape_data(request):
    data = _json_body(request)
    if not data:
        return JsonResponse({"error": "A valid JSON object is required."}, status=400)
    if not data.get("url"):
        return JsonResponse({"error": "URL is required"}, status=400)
    try:
        result = get_course_url(
            data.get("university", "University"),
            data.get("course", "Computer Science"),
            target_type="institution",
        )
        if result:
            return JsonResponse({"status": "success", "result": result})
        return JsonResponse({"error": "Could not locate the course page."}, status=404)
    except Exception:
        logger.exception("Scraper route failed.")
        return JsonResponse({"error": "An unexpected error occurred"}, status=500)


@require_http_methods(["POST"])
def send_report(request):
    if request.content_type != "application/json":
        return JsonResponse({"error": "Report details must be sent as JSON."}, status=400)
    data = _json_body(request)
    if not data or not data.get("email") or not data.get("pdf_data"):
        return JsonResponse({"error": "Missing email or PDF data"}, status=400)
    encoded = data["pdf_data"]
    if not isinstance(encoded, str):
        return JsonResponse({"error": "PDF data must be a base64 string."}, status=400)
    encoded = encoded.split(",", 1)[-1]
    try:
        pdf_bytes = base64.b64decode(encoded, validate=True)
    except (binascii.Error, ValueError):
        return JsonResponse({"error": "PDF data is not valid base64."}, status=400)
    if not pdf_bytes.startswith(b"%PDF-"):
        return JsonResponse({"error": "The attachment is not a valid PDF."}, status=400)
    if len(pdf_bytes) > 12 * 1024 * 1024:
        return JsonResponse({"error": "The PDF must be 12 MB or smaller."}, status=400)
    name = str(data.get("name") or "Student")
    try:
        future = email_executor.submit(
            _send_email,
            "Your CareerPath AI Assessment Report",
            data["email"],
            f"Hello {name}, your CareerPath AI Assessment Report is attached.",
            attachments=[("CareerPath_Report.pdf", pdf_bytes, "application/pdf")],
        )
        future.add_done_callback(_log_email_delivery_failure)
    except Exception:
        logger.exception("Could not queue the report email.")
        return JsonResponse({"error": "Could not send the report email."}, status=503)
    return JsonResponse(
        {"status": "sending", "message": "Email is being sent in the background!"},
        status=202,
    )
