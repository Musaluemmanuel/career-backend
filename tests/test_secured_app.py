import os
import unittest

# Set isolated configuration before importing the application.
os.environ["DATABASE_URL"] = "sqlite:///:memory:"
os.environ["AUTH_TOKEN_SECRET"] = "unit-test-only-secret-not-for-production"
os.environ["GROQ_API_KEY"] = "unit-test-key"
os.environ["GROQ_MODEL"] = "openai/gpt-oss-20b"

from flask import jsonify, request

import secured_app
from app import User, app, db


class SecuredRecommendationTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        app.config.update(TESTING=True)
        cls.original_recommend_view = app.view_functions["recommend"]
        app.view_functions["recommend"] = lambda: jsonify(request.get_json())

    @classmethod
    def tearDownClass(cls):
        app.view_functions["recommend"] = cls.original_recommend_view

    def setUp(self):
        with app.app_context():
            db.drop_all()
            db.create_all()
            user = User(
                email="student@example.test",
                password_hash="test-hash",
                name="Test Student",
                is_verified=True,
            )
            db.session.add(user)
            db.session.commit()
            self.user_id = user.id
        self.client = app.test_client()
        self.token = secured_app.TOKEN_SERIALIZER.dumps({"sub": self.user_id})

    def tearDown(self):
        with app.app_context():
            db.session.remove()
            db.drop_all()

    def post_recommend(self, payload=None):
        return self.client.post(
            "/recommend",
            headers={"Authorization": f"Bearer {self.token}"},
            json=payload or {"interest": "science", "grades": {}},
        )

    def test_missing_bearer_token_is_rejected(self):
        response = self.client.post("/recommend", json={"interest": "science", "grades": {}})
        self.assertEqual(response.status_code, 401)
        self.assertEqual(response.get_json()["code"], "authentication_required")

    def test_mismatched_email_does_not_consume_quota(self):
        response = self.post_recommend({"email": "other@example.test", "interest": "science", "grades": {}})
        self.assertEqual(response.status_code, 403)
        with app.app_context():
            self.assertIsNone(db.session.get(secured_app.RecommendationUsage, self.user_id))

    def test_authenticated_user_is_injected(self):
        response = self.post_recommend()
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.get_json()["email"], "student@example.test")
        self.assertEqual(response.get_json()["name"], "Test Student")

    def test_three_requests_allowed_and_fourth_requires_funding(self):
        for _ in range(3):
            self.assertEqual(self.post_recommend().status_code, 200)
        response = self.post_recommend()
        self.assertEqual(response.status_code, 402)
        self.assertEqual(response.get_json()["code"], "funding_required")
        self.assertEqual(response.get_json()["limit"], 3)
        with app.app_context():
            usage = db.session.get(secured_app.RecommendationUsage, self.user_id)
            self.assertEqual(usage.attempts, 3)

    def test_unverified_account_is_rejected(self):
        with app.app_context():
            user = db.session.get(User, self.user_id)
            user.is_verified = False
            db.session.commit()
        response = self.post_recommend()
        self.assertEqual(response.status_code, 403)
        self.assertEqual(response.get_json()["code"], "account_not_verified")

    def test_successful_login_response_receives_signed_token(self):
        with app.test_request_context("/login", method="POST"):
            response = jsonify({"user": {"email": "student@example.test"}})
            response.status_code = 200
            hooked = secured_app.issue_sign_in_token(response)
        self.assertIn("access_token", hooked.get_json())

    def test_options_preflight_does_not_consume_quota(self):
        self.client.options("/recommend")
        with app.app_context():
            self.assertIsNone(db.session.get(secured_app.RecommendationUsage, self.user_id))


if __name__ == "__main__":
    unittest.main()
