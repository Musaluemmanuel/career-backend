import base64
import json
from unittest.mock import MagicMock, patch

from django.conf import settings
from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import TestCase, override_settings
from itsdangerous import URLSafeTimedSerializer
from werkzeug.security import generate_password_hash

from utils.ai_engines import (
    ask_hybrid_career_advice,
    fetch_from_groq,
    is_course_in_catalog,
    validate_ai_response,
)
from utils.web_scraper import get_course_url

from .models import RecommendationUsage, StudentLog, User


class ApiViewsTests(TestCase):
    def test_home_and_cors(self):
        response = self.client.get("/", HTTP_ORIGIN="https://student.example")
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["message"], "CareerPath AI Backend is running beautifully!")
        self.assertEqual(
            response["Access-Control-Allow-Origin"],
            "https://student.example",
        )

    def test_register_and_verify_account(self):
        with patch("api.views._send_verification_email") as send_email:
            response = self.client.post(
                "/register",
                data=json.dumps({
                    "name": "Amina",
                    "email": "AMINA@example.com",
                    "password": "secure password",
                }),
                content_type="application/json",
            )
        self.assertEqual(response.status_code, 201)
        user = User.objects.get(email="amina@example.com")
        send_email.assert_called_once()
        with patch("api.views._send_email"):
            response = self.client.post(
                "/verify",
                data=json.dumps({"email": user.email, "code": user.verification_code}),
                content_type="application/json",
            )
        self.assertEqual(response.status_code, 200)
        self.assertTrue(response.json()["user"]["email"] == user.email)
        user.refresh_from_db()
        self.assertTrue(user.is_verified)
        self.assertIsNone(user.verification_code)

    def test_login_accepts_email_and_username(self):
        user = User.objects.create(
            name="Amina",
            email="amina@example.com",
            password_hash=generate_password_hash("correct horse"),
            is_verified=True,
            history=[],
        )
        for field in ("email", "username"):
            response = self.client.post(
                "/login",
                data=json.dumps({field: user.email if field == "email" else user.name, "password": "correct horse"}),
                content_type="application/json",
            )
            self.assertEqual(response.status_code, 200)
            self.assertEqual(response.json()["user"]["email"], user.email)

    def test_unverified_account_cannot_log_in(self):
        User.objects.create(
            name="Amina",
            email="amina@example.com",
            password_hash=generate_password_hash("correct horse"),
            is_verified=False,
            history=[],
        )
        response = self.client.post(
            "/login",
            data=json.dumps({"email": "amina@example.com", "password": "correct horse"}),
            content_type="application/json",
        )
        self.assertEqual(response.status_code, 403)

    def test_history_can_be_saved_and_loaded(self):
        user = User.objects.create(
            name="Amina",
            email="amina@example.com",
            password_hash="unused",
            history=[],
        )
        report = {"specific_course": "Bachelor of Nursing"}
        response = self.client.post(
            "/save-history",
            data=json.dumps({"username": user.name, "email": user.email, "report": report}),
            content_type="application/json",
        )
        self.assertEqual(response.status_code, 200)
        response = self.client.get("/history", {"username": "amina"})
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json(), [report])

    @override_settings(AUTH_TOKEN_SECRET="unit-test-secret", AUTH_TOKEN_TTL_SECONDS=3600)
    def test_data_consent_is_account_scoped_and_revocable(self):
        user = User.objects.create(
            name="Amina",
            email="amina@example.com",
            password_hash="unused",
            is_verified=True,
        )
        token = URLSafeTimedSerializer(
            settings.AUTH_TOKEN_SECRET,
            salt="career-backend-auth-v1",
        ).dumps({"sub": user.pk})
        headers = {"HTTP_AUTHORIZATION": f"Bearer {token}"}

        response = self.client.get("/data-consent")
        self.assertEqual(response.status_code, 401)

        response = self.client.get("/data-consent", **headers)
        self.assertEqual(response.status_code, 200)
        self.assertFalse(response.json()["personalization"])
        self.assertFalse(response.json()["improvement"])
        self.assertFalse(response.json()["recorded"])
        self.assertFalse(response.json()["model_training_active"])

        response = self.client.post(
            "/data-consent",
            data=json.dumps({"personalization": True, "improvement": True}),
            content_type="application/json",
            **headers,
        )
        self.assertEqual(response.status_code, 200)
        self.assertTrue(response.json()["recorded"])
        self.assertEqual(response.json()["policy_version"], "2026-11-v1")

        response = self.client.post(
            "/data-consent",
            data=json.dumps({"personalization": False, "improvement": False}),
            content_type="application/json",
            **headers,
        )
        self.assertEqual(response.status_code, 200)
        self.assertFalse(response.json()["personalization"])
        self.assertFalse(response.json()["improvement"])
        user.refresh_from_db()
        self.assertIsNotNone(user.consent_updated_at)

    @override_settings(AUTH_TOKEN_SECRET="unit-test-secret", AUTH_TOKEN_TTL_SECONDS=3600)
    @patch("api.views.get_course_url", return_value="https://example.edu/nursing")
    @patch("api.views.ask_hybrid_career_advice")
    def test_improvement_contribution_requires_consent_and_excludes_profile_data(
        self,
        ask_advice,
        get_course_url,
    ):
        user = User.objects.create(
            name="Amina",
            email="amina@example.com",
            password_hash="unused",
            is_verified=True,
            improvement_consent=True,
        )
        ask_advice.return_value = {
            "specific_course": "Bachelor of Nursing",
            "ai_role": "Nurse",
            "universities": [{"name": "Example University"}],
        }
        token = URLSafeTimedSerializer(
            settings.AUTH_TOKEN_SECRET,
            salt="career-backend-auth-v1",
        ).dumps({"sub": user.pk})
        headers = {"HTTP_AUTHORIZATION": f"Bearer {token}"}
        body = {
            "name": "Amina",
            "interest": "nursing",
            "grades": {"Biology": "A"},
            "improvement_consent": False,
        }

        response = self.client.post(
            "/recommend",
            data=json.dumps(body),
            content_type="application/json",
            **headers,
        )
        self.assertEqual(response.status_code, 200)
        self.assertFalse(StudentLog.objects.exists())

        body["improvement_consent"] = True
        response = self.client.post(
            "/recommend",
            data=json.dumps(body),
            content_type="application/json",
            **headers,
        )
        self.assertEqual(response.status_code, 200)
        contribution = StudentLog.objects.get()
        self.assertTrue(contribution.contributed_to_improvement)
        self.assertIsNone(contribution.student_name)
        self.assertEqual(contribution.interest, "Nurse")
        self.assertIsNone(contribution.grades)
        self.assertIsNone(contribution.points)
        self.assertEqual(contribution.ai_response, {"ai_role": "Nurse"})
        self.assertEqual(contribution.history, [])

    @patch("api.views.generate_chat_reply")
    def test_valid_image_is_forwarded_to_chat_model(self, generate_reply):
        generate_reply.return_value = {
            "reply": "I can read this image.",
            "suggestions": [],
            "assistant_name": "CareerPath AI",
        }
        image = SimpleUploadedFile(
            "grades.png",
            b"\x89PNG\r\n\x1a\nvalid-image-content",
            content_type="image/png",
        )
        response = self.client.post(
            "/chat",
            {
                "messages": json.dumps([{"role": "user", "content": "What grades are shown?"}]),
                "assistant_name": "CareerPath AI",
                "attachments": image,
            },
        )
        self.assertEqual(response.status_code, 200)
        attachments = generate_reply.call_args.args[1]
        self.assertEqual(len(attachments), 1)
        self.assertEqual(attachments[0]["mime_type"], "image/png")
        self.assertTrue(attachments[0]["data"].startswith(b"\x89PNG\r\n\x1a\n"))

    @patch("api.views.generate_chat_reply")
    def test_image_with_invalid_signature_is_rejected(self, generate_reply):
        image = SimpleUploadedFile(
            "grades.png",
            b"not a png image",
            content_type="image/png",
        )
        response = self.client.post(
            "/chat",
            {
                "messages": json.dumps([{"role": "user", "content": "Read this"}]),
                "attachments": image,
            },
        )
        self.assertEqual(response.status_code, 400)
        generate_reply.assert_not_called()

    @patch("api.views.generate_chat_reply")
    def test_chat_rejects_more_than_three_attachments(self, generate_reply):
        files = [
            SimpleUploadedFile(f"{index}.png", b"\x89PNG\r\n\x1a\n", content_type="image/png")
            for index in range(4)
        ]
        response = self.client.post(
            "/chat",
            {
                "messages": json.dumps([{"role": "user", "content": "Read these"}]),
                "attachments": files,
            },
        )
        self.assertEqual(response.status_code, 400)
        generate_reply.assert_not_called()

    @patch("api.views.generate_chat_reply", side_effect=RuntimeError("AI is unavailable"))
    def test_chat_reports_unavailable_ai_service(self, generate_reply):
        response = self.client.post(
            "/chat",
            data=json.dumps({"messages": [{"role": "user", "content": "Hello"}]}),
            content_type="application/json",
        )
        self.assertEqual(response.status_code, 503)
        self.assertEqual(response.json()["error"], "AI is unavailable")

    @override_settings(AUTH_TOKEN_SECRET="unit-test-secret", AUTH_TOKEN_TTL_SECONDS=3600)
    @patch("api.views.get_course_url", return_value="https://example.edu/nursing")
    @patch("api.views.ask_hybrid_career_advice")
    def test_recommendation_supports_missing_grades(self, ask_advice, get_course_url):
        user = User.objects.create(
            name="Amina",
            email="amina@example.com",
            password_hash="unused",
            is_verified=True,
            history=[],
        )
        ask_advice.return_value = {
            "specific_course": "Bachelor of Nursing",
            "universities": [{"name": "Example University"}],
        }
        token = URLSafeTimedSerializer(
            settings.AUTH_TOKEN_SECRET,
            salt="career-backend-auth-v1",
        ).dumps({"sub": user.pk})
        response = self.client.post(
            "/recommend",
            data=json.dumps({"name": "Amina", "interest": "nursing", "grades": {}}),
            content_type="application/json",
            HTTP_AUTHORIZATION=f"Bearer {token}",
        )
        self.assertEqual(response.status_code, 200)
        self.assertIsNone(response.json()["validated_points"])
        self.assertTrue(response.json()["universities"][0]["verified_offering"])
        self.assertEqual(ask_advice.call_args.kwargs["profile_context"], "")
        get_course_url.assert_called_once()
        self.assertEqual(RecommendationUsage.objects.get(user=user).attempts, 1)

    @override_settings(AUTH_TOKEN_SECRET="unit-test-secret", AUTH_TOKEN_TTL_SECONDS=3600)
    @patch("api.views.get_course_url", return_value=None)
    @patch("api.views.ask_hybrid_career_advice")
    def test_recommendation_does_not_present_unverified_institutions(
        self,
        ask_advice,
        get_course_url,
    ):
        user = User.objects.create(
            name="Amina",
            email="amina-no-matches@example.com",
            password_hash="unused",
            is_verified=True,
            history=[],
        )
        ask_advice.return_value = {
            "specific_course": "Bachelor of Nursing",
            "universities": [{"name": "Example University"}],
        }
        token = URLSafeTimedSerializer(
            settings.AUTH_TOKEN_SECRET,
            salt="career-backend-auth-v1",
        ).dumps({"sub": user.pk})
        response = self.client.post(
            "/recommend",
            data=json.dumps({"interest": "nursing", "grades": {}}),
            content_type="application/json",
            HTTP_AUTHORIZATION=f"Bearer {token}",
        )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["universities"], [])
        self.assertEqual(response.json()["institution_match_status"], "not_verified")
        self.assertIn("no official institution", response.json()["institution_match_message"])
        get_course_url.assert_called_once()

    @override_settings(AUTH_TOKEN_SECRET="unit-test-secret", AUTH_TOKEN_TTL_SECONDS=3600)
    def test_login_returns_bearer_token_for_verified_account(self):
        user = User.objects.create(
            name="Amina",
            email="amina@example.com",
            password_hash=generate_password_hash("correct horse"),
            is_verified=True,
            history=[],
        )
        response = self.client.post(
            "/login",
            data=json.dumps({"email": user.email, "password": "correct horse"}),
            content_type="application/json",
        )
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["token_type"], "Bearer")
        self.assertEqual(response.json()["expires_in"], 3600)
        claims = URLSafeTimedSerializer(
            settings.AUTH_TOKEN_SECRET,
            salt="career-backend-auth-v1",
        ).loads(response.json()["access_token"])
        self.assertEqual(claims["sub"], user.pk)

    @override_settings(AUTH_TOKEN_SECRET="unit-test-secret", AUTH_TOKEN_TTL_SECONDS=3600)
    @patch("api.views.ask_hybrid_career_advice")
    def test_recommendation_requires_token_and_enforces_account_quota(self, ask_advice):
        user = User.objects.create(
            name="Amina",
            email="amina@example.com",
            password_hash="unused",
            is_verified=True,
            history=[],
        )
        token = URLSafeTimedSerializer(
            settings.AUTH_TOKEN_SECRET,
            salt="career-backend-auth-v1",
        ).dumps({"sub": user.pk})
        body = json.dumps({"interest": "nursing", "grades": {}})
        response = self.client.post("/recommend", data=body, content_type="application/json")
        self.assertEqual(response.status_code, 401)
        ask_advice.assert_not_called()

        RecommendationUsage.objects.create(user=user, attempts=3)
        response = self.client.post(
            "/recommend",
            data=body,
            content_type="application/json",
            HTTP_AUTHORIZATION=f"Bearer {token}",
        )
        self.assertEqual(response.status_code, 402)
        self.assertEqual(response.json()["code"], "funding_required")
        ask_advice.assert_not_called()

    @patch("utils.ai_engines.fetch_from_groq")
    def test_recommendation_ai_accepts_interview_context_without_grades(self, fetch):
        fetch.return_value = {
            "specific_course": "Bachelor of Nursing",
            "level": "Degree",
            "universities": [{
                "requirements_met": [{
                    "subject": "Biology",
                    "required": "C+",
                    "attained": "Not provided",
                    "status": "Not checked",
                }],
            }],
        }
        result = ask_hybrid_career_advice(
            "Amina",
            "nursing",
            {},
            0,
            "Degree",
            profile_context="Prefers practical learning.",
        )
        self.assertIsNotNone(result)
        prompt = fetch.call_args.args[1]
        self.assertIn("Prefers practical learning.", prompt)
        self.assertIn("fewer than seven valid grades supplied", prompt)

    def test_ai_validation_does_not_treat_missing_grades_as_failing(self):
        recommendation = {
            "specific_course": "Bachelor of Nursing",
            "level": "Degree",
            "universities": [{
                "requirements_met": [{
                    "subject": "Biology",
                    "required": "C+",
                    "attained": "Not provided",
                    "status": "Not checked",
                }],
            }],
        }
        self.assertEqual(validate_ai_response(recommendation, {}, "Degree"), [])
        self.assertEqual(
            recommendation["universities"][0]["requirements_met"][0]["status"],
            "Not checked",
        )

    def test_course_catalog_matches_titles_without_cutoff_metadata(self):
        self.assertTrue(is_course_in_catalog("Bachelor of Science (Nursing)"))
        self.assertTrue(is_course_in_catalog("Nursing"))
        self.assertFalse(is_course_in_catalog("Bachelor of Science"))
        self.assertFalse(is_course_in_catalog("Interplanetary Tourism"))

    def test_ai_can_return_a_catalog_course_without_unverified_institutions(self):
        with patch("utils.ai_engines.fetch_from_groq") as fetch:
            fetch.return_value = {
                "specific_course": "Bachelor of Science (Nursing)",
                "universities": [],
            }
            result = ask_hybrid_career_advice(
                "Amina",
                "nursing",
                {},
                0,
                "Undetermined",
            )

        self.assertIsNotNone(result)
        self.assertEqual(result["universities"], [])

    def test_ai_candidate_reaches_official_verifier_without_trusting_requirements(self):
        response = MagicMock()
        response.choices[0].message.content = json.dumps({
            "specific_course": "Bachelor of Science (Nursing)",
            "universities": [{
                "name": "Kenyatta University",
                "specific_course": "Bachelor of Science (Nursing)",
                "website_url": "https://invented.example/course",
                "verified_offering": True,
                "requirements_met": [{"subject": "Biology", "required": "A+"}],
            }],
        })
        client = MagicMock()
        client.chat.completions.create.return_value = response
        with patch("utils.ai_engines.client_groq", client):
            result = fetch_from_groq("system", "prompt", {}, "Degree")

        self.assertEqual(len(result["universities"]), 1)
        candidate = result["universities"][0]
        self.assertEqual(candidate["requirements_met"], [])
        self.assertFalse(candidate["verified_offering"])
        self.assertEqual(candidate["website_url"], "")

    def test_course_url_uses_local_official_course_mapping_first(self):
        url = "https://www.ku.ac.ke/programmes/nursing"
        with (
            patch("utils.web_scraper.OFFICIAL_DOMAINS", {"kenyatta university": "ku.ac.ke"}),
            patch(
                "utils.web_scraper.load_course_url_database",
                return_value={
                    "Kenyatta University": {
                        "Bachelor of Science (Nursing)": url,
                    },
                },
            ),
            patch("utils.web_scraper.requests.get") as get_page,
        ):
            result = get_course_url(
                "Kenyatta University",
                "Bachelor of Science (Nursing)",
            )

        self.assertEqual(result, url)
        get_page.assert_not_called()

    def test_course_url_search_requires_an_official_course_page(self):
        url = "https://www.ku.ac.ke/programmes/nursing"
        tracked_url = (
            "https://www.bing.com/ck/a?u=a1"
            + base64.urlsafe_b64encode(url.encode("utf-8")).decode("ascii").rstrip("=")
        )
        search_page = MagicMock(
            status_code=200,
            text=(
                '<ol><li class="b_algo"><h2><a href="'
                f'{tracked_url}">Bachelor of Science in Nursing - Kenyatta University</a></h2>'
                "<p>Programme overview and course details.</p></li></ol>"
            ),
        )
        page = MagicMock(
            status_code=200,
            url=url,
            text="<title>Bachelor of Science in Nursing</title><h1>Bachelor of Science in Nursing</h1>",
        )
        with (
            patch("utils.web_scraper.OFFICIAL_DOMAINS", {"kenyatta university": "ku.ac.ke"}),
            patch("utils.web_scraper.load_course_url_database", return_value={}),
            patch("utils.web_scraper.requests.get", side_effect=[search_page, page]) as get_page,
        ):
            result = get_course_url(
                "Kenyatta University",
                "Bachelor of Science (Nursing)",
            )

        self.assertEqual(result, url)
        self.assertEqual(get_page.call_count, 2)

    def test_course_url_rejects_unrelated_or_non_official_search_results(self):
        search_page = MagicMock(
            status_code=200,
            text=(
                '<ol><li class="b_algo"><h2><a href="https://www.ku.ac.ke/">'
                "Kenyatta University homepage</a></h2><p>Welcome to the official site.</p></li>"
                '<li class="b_algo"><h2><a href="https://example.com/nursing">'
                "Bachelor of Science in Nursing</a></h2><p>Course details.</p></li></ol>"
            ),
        )
        with (
            patch("utils.web_scraper.OFFICIAL_DOMAINS", {"kenyatta university": "ku.ac.ke"}),
            patch("utils.web_scraper.load_course_url_database", return_value={}),
            patch("utils.web_scraper.requests.get", return_value=search_page) as get_page,
        ):
            result = get_course_url(
                "Kenyatta University",
                "Bachelor of Science (Nursing)",
            )

        self.assertIsNone(result)
        get_page.assert_called_once()

    def test_course_url_rejects_invalid_institution_domain_mapping(self):
        with (
            patch(
                "utils.web_scraper.OFFICIAL_DOMAINS",
                {"Technical and Vocational Education and Training Authority": "Not a domain"},
            ),
            patch("utils.web_scraper.load_course_url_database", return_value={}),
            patch("utils.web_scraper.requests.get") as get_page,
        ):
            result = get_course_url(
                "Technical and Vocational Education and Training Authority",
                "Nursing",
            )

        self.assertIsNone(result)
        get_page.assert_not_called()

    def test_reset_endpoint_is_not_exposed(self):
        response = self.client.get("/reset-db-now")
        self.assertEqual(response.status_code, 404)
