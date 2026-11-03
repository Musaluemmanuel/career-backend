"""Authenticated recommendation entry point.

This variant removes the recommendation quota entirely while keeping the
account-bound bearer-token requirement in place.
"""

import json
import logging
import os

from flask import g, jsonify, request
from itsdangerous import BadSignature, SignatureExpired, URLSafeTimedSerializer

from app import User, app

TOKEN_TTL_SECONDS = int(os.environ.get("AUTH_TOKEN_TTL_SECONDS", str(7 * 24 * 60 * 60)))
AUTH_TOKEN_SECRET = os.environ.get("AUTH_TOKEN_SECRET") or os.environ.get("SECRET_KEY")
TOKEN_SERIALIZER = (
    URLSafeTimedSerializer(AUTH_TOKEN_SECRET, salt="career-backend-auth-v1")
    if AUTH_TOKEN_SECRET
    else None
)

GROQ_MODEL = os.environ.get("GROQ_MODEL", "openai/gpt-oss-20b")


class _CompletionsWithSelectedModel:
    def __init__(self, completions):
        self._completions = completions

    def create(self, *args, **kwargs):
        kwargs["model"] = GROQ_MODEL
        return self._completions.create(*args, **kwargs)

    def __getattr__(self, name):
        return getattr(self._completions, name)


class _ChatWithSelectedModel:
    def __init__(self, chat):
        self._chat = chat

    @property
    def completions(self):
        return _CompletionsWithSelectedModel(self._chat.completions)

    def __getattr__(self, name):
        return getattr(self._chat, name)


class _GroqClientWithSelectedModel:
    def __init__(self, client):
        self._client = client

    @property
    def chat(self):
        return _ChatWithSelectedModel(self._client.chat)

    def __getattr__(self, name):
        return getattr(self._client, name)


from utils import ai_engines, web_scraper  # noqa: E402

for _module in (ai_engines, web_scraper):
    if getattr(_module, "client_groq", None) is not None:
        _module.client_groq = _GroqClientWithSelectedModel(_module.client_groq)


def _json_error(message, code, status):
    return jsonify({"error": message, "code": code}), status


@app.before_request
def require_authenticated_recommendation():
    if request.path != "/recommend" or request.method == "OPTIONS":
        return None

    if TOKEN_SERIALIZER is None:
        return _json_error(
            "Server authentication is not configured. Set AUTH_TOKEN_SECRET in the service environment.",
            "auth_not_configured",
            503,
        )

    authorization = request.headers.get("Authorization", "")
    scheme, _, token = authorization.partition(" ")
    if scheme.lower() != "bearer" or not token:
        return _json_error("Sign in and send the returned bearer token.", "authentication_required", 401)

    try:
        claims = TOKEN_SERIALIZER.loads(token, max_age=TOKEN_TTL_SECONDS)
        user_id = int(claims["sub"])
    except (BadSignature, SignatureExpired, KeyError, TypeError, ValueError):
        return _json_error("Your sign-in token is invalid or expired. Please sign in again.", "invalid_token", 401)

    user = User.query.get(user_id)
    if user is None:
        return _json_error("The account for this token no longer exists.", "invalid_token", 401)
    if not user.is_verified:
        return _json_error("Verify your account before requesting recommendations.", "account_not_verified", 403)

    payload = request.get_json(silent=True)
    if not isinstance(payload, dict):
        return _json_error("A JSON request body is required.", "invalid_request", 400)

    supplied_email = payload.get("email")
    if supplied_email and str(supplied_email).strip().casefold() != user.email.casefold():
        return _json_error("Request email must match the signed-in account.", "account_mismatch", 403)

    payload["email"] = user.email
    if user.name:
        payload["name"] = user.name

    g.authenticated_user = user
    request._cached_json = payload
    return None


@app.after_request
def issue_sign_in_token(response):
    if request.path not in ("/login", "/google-login") or response.status_code != 200:
        return response
    if TOKEN_SERIALIZER is None:
        return response

    try:
        payload = response.get_json()
        if not isinstance(payload, dict):
            return response

        account = payload.get("user") if isinstance(payload.get("user"), dict) else payload
        email = account.get("email") if isinstance(account, dict) else None
        if not email:
            return response

        user = User.query.filter_by(email=email).first()
        if user is None or not user.is_verified:
            return response

        payload["access_token"] = TOKEN_SERIALIZER.dumps({"sub": user.id})
        payload["token_type"] = "Bearer"
        payload["expires_in"] = TOKEN_TTL_SECONDS
        response.set_data(json.dumps(payload))
        response.mimetype = "application/json"
    except Exception:
        logging.exception("Could not issue authentication token")
    return response
