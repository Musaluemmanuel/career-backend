import json
from unittest.mock import patch

from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import TestCase
from werkzeug.security import generate_password_hash

from .models import User


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

    @patch("api.views.get_course_url", return_value="https://example.edu/nursing")
    @patch("api.views.ask_hybrid_career_advice")
    def test_recommendation_supports_missing_grades(self, ask_advice, get_course_url):
        ask_advice.return_value = {
            "specific_course": "Bachelor of Nursing",
            "universities": [{"name": "Example University"}],
        }
        response = self.client.post(
            "/recommend",
            data=json.dumps({"name": "Amina", "interest": "nursing", "grades": {}}),
            content_type="application/json",
        )
        self.assertEqual(response.status_code, 200)
        self.assertIsNone(response.json()["validated_points"])
        self.assertTrue(response.json()["universities"][0]["verified_offering"])
        self.assertEqual(ask_advice.call_args.kwargs["profile_context"], "")
        get_course_url.assert_called_once()

    def test_reset_endpoint_is_not_exposed(self):
        response = self.client.get("/reset-db-now")
        self.assertEqual(response.status_code, 404)
