import unittest
import json
from types import SimpleNamespace
from unittest.mock import mock_open, patch

from utils import ai_engines


class CareerAdviceContextTests(unittest.TestCase):
    def test_total_points_uses_best_seven_grades(self):
        grades = {
            "Math": "A",
            "English": "A-",
            "Kiswahili": "B+",
            "Biology": "B",
            "Physics": "B-",
            "Chemistry": "C+",
            "History": "C",
            "Geography": "E",
        }

        self.assertEqual(ai_engines.calculate_total_points(grades), 63)

    def test_requirements_remain_available_when_no_grades_are_shared(self):
        response = {
            "specific_course": "Bachelor of Medicine",
            "level": "Degree",
            "universities": [{
                "name": "Example University",
                "requirements_met": [
                    {"subject": "Biology", "required": "B"},
                    {"subject": "Chemistry", "required": "B"}
                ]
            }]
        }

        errors = ai_engines.validate_ai_response(response, {}, "Undetermined")

        self.assertEqual(errors, [])
        self.assertEqual(len(response["universities"]), 1)
        self.assertEqual(
            [requirement["status"] for requirement in response["universities"][0]["requirements_met"]],
            ["Not checked", "Not checked"],
        )
        self.assertTrue(all(
            requirement["attained"] == "Not provided"
            for requirement in response["universities"][0]["requirements_met"]
        ))

    def test_missing_subject_grades_are_not_treated_as_failing(self):
        response = {
            "specific_course": "Bachelor of Medicine",
            "level": "Degree",
            "universities": [{
                "name": "Example University",
                "requirements_met": [
                    {"subject": "Biology", "required": "B"},
                    {"subject": "Chemistry", "required": "B"}
                ]
            }]
        }

        errors = ai_engines.validate_ai_response(response, {"Biology": "A"}, "Undetermined")

        self.assertEqual(errors, [])
        requirements = response["universities"][0]["requirements_met"]
        self.assertEqual(requirements[0]["status"], "Met")
        self.assertEqual(requirements[0]["attained"], "A")
        self.assertEqual(requirements[1]["status"], "Not checked")

    def test_course_lookup_does_not_exclude_courses_when_grades_are_missing(self):
        course_database = ["Bachelor of Medicine 1.1-Biology:C+ Chemistry:C+"]

        with patch.object(ai_engines.os.path, "exists", return_value=True), patch(
            "builtins.open",
            mock_open(read_data=json.dumps(course_database)),
        ):
            matches = ai_engines.get_eligible_context("medicine", {})

        self.assertIn("Bachelor of Medicine", matches)

    @patch.object(ai_engines, "get_eligible_context", return_value=[
        "Bachelor of Science in Computer Science"
    ])
    @patch.object(ai_engines, "fetch_from_groq")
    def test_interview_preferences_are_included_in_model_prompt(
        self,
        fetch_from_groq,
        _get_eligible_context,
    ):
        fetch_from_groq.return_value = {"universities": [{"name": "Example University"}]}

        ai_engines.ask_hybrid_career_advice(
            "Test Student",
            "software design",
            {"Math": "B"},
            60,
            "Diploma",
            profile_context="Motivation: building useful apps\nLocation: Nairobi",
        )

        prompt = fetch_from_groq.call_args.args[1]
        self.assertIn("building useful apps", prompt)
        self.assertIn("Location: Nairobi", prompt)
        self.assertIn("Requested Tier: Diploma", prompt)

    @patch.object(ai_engines, "get_eligible_context", return_value=["Bachelor of Medicine"])
    @patch.object(ai_engines, "fetch_from_groq", return_value={"universities": [{"name": "Example University"}]})
    def test_recommendation_prompt_supports_course_exploration_without_grades(
        self,
        fetch_from_groq,
        _get_eligible_context,
    ):
        ai_engines.ask_hybrid_career_advice(
            "Student",
            "medicine",
            {},
            0,
            "Undetermined",
        )

        system_prompt, user_prompt = fetch_from_groq.call_args.args[:2]
        self.assertIn("No grades were provided", system_prompt)
        self.assertIn("Do not infer eligibility", system_prompt)
        self.assertIn("Not provided", system_prompt)
        self.assertIn("Points: not calculated", user_prompt)

    @patch.object(ai_engines, "client_groq")
    def test_chat_reply_uses_conversation_history_and_natural_response(self, client_groq):
        client_groq.chat.completions.create.return_value = SimpleNamespace(
            choices=[SimpleNamespace(message=SimpleNamespace(content=(
                '{"reply":"That sounds interesting. What do you enjoy most about it?",'
                '"suggestions":["Explore health careers","Talk about study options"],'
                '"assistant_name":"CareerPath AI"}'
            )))]
        )

        result = ai_engines.generate_chat_reply([
            {"role": "user", "content": "I like helping people through healthcare."}
        ])

        self.assertIn("What do you enjoy most", result["reply"])
        self.assertEqual(result["suggestions"], ["Explore health careers", "Talk about study options"])
        self.assertEqual(result["assistant_name"], "CareerPath AI")
        call = client_groq.chat.completions.create.call_args.kwargs
        self.assertEqual(call["model"], "llama-3.1-8b-instant")
        self.assertEqual(call["messages"][-1]["content"], "I like helping people through healthcare.")
        self.assertIn("career-interest discovery activity", call["messages"][0]["content"])
        self.assertIn("scenario-based question", call["messages"][0]["content"])
        self.assertIn("Do not repeat the introduction", call["messages"][0]["content"])
        self.assertIn("primary school", call["messages"][0]["content"])
        self.assertIn("optional and continue helping if they have none", call["messages"][0]["content"])
        self.assertIn("as broadly", call["messages"][0]["content"])
        self.assertIn("detail as they wish", call["messages"][0]["content"])
        self.assertIn("a condition for answering", call["messages"][0]["content"])
        self.assertGreater(call["temperature"], 0.3)
        self.assertEqual(call["response_format"], {"type": "json_object"})

    @patch.object(ai_engines, "client_groq")
    def test_chat_reply_accepts_a_student_chosen_assistant_name(self, client_groq):
        client_groq.chat.completions.create.return_value = SimpleNamespace(
            choices=[SimpleNamespace(message=SimpleNamespace(content=(
                '{"reply":"Amani it is!","suggestions":["Explore interests"],'
                '"assistant_name":"Amani"}'
            )))]
        )

        result = ai_engines.generate_chat_reply(
            [{"role": "user", "content": "I would like to call you Amani."}],
            assistant_name="CareerPath AI",
        )

        self.assertEqual(result["assistant_name"], "Amani")
        self.assertIn("You are CareerPath AI", client_groq.chat.completions.create.call_args.kwargs["messages"][0]["content"])

    @patch.object(ai_engines, "client_groq")
    def test_chat_reply_ignores_invalid_assistant_name(self, client_groq):
        client_groq.chat.completions.create.return_value = SimpleNamespace(
            choices=[SimpleNamespace(message=SimpleNamespace(content=(
                '{"reply":"Let’s keep chatting.","suggestions":["Explore options"],'
                '"assistant_name":"<script>alert(1)</script>"}'
            )))]
        )

        result = ai_engines.generate_chat_reply(
            [{"role": "user", "content": "What should I study?"}],
            assistant_name="CareerPath AI",
        )

        self.assertEqual(result["assistant_name"], "CareerPath AI")

    @patch.object(ai_engines, "client_gemini")
    def test_chat_reply_sends_attachments_to_multimodal_model(self, client_gemini):
        client_gemini.models.generate_content.return_value = SimpleNamespace(
            text='{"reply":"I can see the grades listed here. Please confirm that I read them correctly.",'
                 '"suggestions":["Confirm these grades","Ask about course requirements"],'
                 '"assistant_name":"CareerPath AI"}'
        )

        result = ai_engines.generate_chat_reply(
            [{"role": "user", "content": "Can you read this results page?"}],
            [{"data": b"%PDF-test", "mime_type": "application/pdf"}],
        )

        self.assertIn("confirm", result["reply"])
        self.assertEqual(len(result["suggestions"]), 2)
        call = client_gemini.models.generate_content.call_args.kwargs
        self.assertEqual(call["model"], "gemini-2.0-flash")
        self.assertEqual(call["contents"][0].parts[1].inline_data.mime_type, "application/pdf")
        self.assertEqual(call["config"].response_mime_type, "application/json")

    @patch.object(ai_engines, "client_gemini")
    def test_chat_reply_sends_video_to_multimodal_model(self, client_gemini):
        client_gemini.models.generate_content.return_value = SimpleNamespace(
            text='{"reply":"I can see the activity in the video. What would you like to know about it?",'
                 '"suggestions":["Describe the activity","Connect it to possible careers"],'
                 '"assistant_name":"CareerPath AI"}'
        )

        result = ai_engines.generate_chat_reply(
            [{"role": "user", "content": "What careers relate to this video?"}],
            [{"data": b"\x00\x00\x00\x18ftypisom", "mime_type": "video/mp4"}],
        )

        self.assertIn("video", result["reply"])
        call = client_gemini.models.generate_content.call_args.kwargs
        self.assertEqual(call["contents"][0].parts[1].inline_data.mime_type, "video/mp4")

    @patch.object(ai_engines, "client_groq")
    def test_chat_reply_rejects_unstructured_provider_output(self, client_groq):
        client_groq.chat.completions.create.return_value = SimpleNamespace(
            choices=[SimpleNamespace(message=SimpleNamespace(content="A reply without suggestions"))]
        )

        with self.assertRaisesRegex(RuntimeError, "unreadable reply"):
            ai_engines.generate_chat_reply([
                {"role": "user", "content": "What should I do next?"}
            ])

    def test_chat_reply_reports_missing_multimodal_service(self):
        with patch.object(ai_engines, "client_gemini", None):
            with self.assertRaisesRegex(RuntimeError, "Document, image, and video analysis is not configured"):
                ai_engines.generate_chat_reply(
                    [{"role": "user", "content": "Please review this."}],
                    [{"data": b"%PDF-test", "mime_type": "application/pdf"}],
                )


if __name__ == "__main__":
    unittest.main()
