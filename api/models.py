import json

from django.db import models


class FlexibleJSONField(models.JSONField):
    def from_db_value(self, value, expression, connection):
        if value is None or not isinstance(value, (str, bytes, bytearray)):
            return value
        try:
            return json.loads(value, cls=self.decoder)
        except json.JSONDecodeError:
            return value


class User(models.Model):
    email = models.CharField(max_length=120, unique=True)
    password_hash = models.CharField(max_length=256)
    name = models.CharField(max_length=100, null=True, blank=True)
    is_verified = models.BooleanField(default=False)
    verification_code = models.CharField(max_length=10, null=True, blank=True)
    has_taken_test = models.BooleanField(default=False, db_column="_has_taken_test")
    history = FlexibleJSONField(default=list)
    personalization_consent = models.BooleanField(default=False)
    improvement_consent = models.BooleanField(default=False)
    consent_updated_at = models.DateTimeField(null=True, blank=True)
    consent_policy_version = models.CharField(max_length=32, null=True, blank=True)

    class Meta:
        db_table = "user"


class StudentLog(models.Model):
    timestamp = models.CharField(max_length=50, null=True, blank=True)
    student_name = models.CharField(max_length=100, null=True, blank=True)
    interest = models.CharField(max_length=100, null=True, blank=True)
    points = models.IntegerField(null=True, blank=True)
    grades = FlexibleJSONField(null=True, blank=True)
    ai_response = FlexibleJSONField(null=True, blank=True)
    history = FlexibleJSONField(default=list)
    contributed_to_improvement = models.BooleanField(default=False)

    class Meta:
        db_table = "student_log"


class RecommendationUsage(models.Model):
    user = models.OneToOneField(
        User,
        on_delete=models.CASCADE,
        primary_key=True,
        db_column="user_id",
        related_name="recommendation_usage",
    )
    attempts = models.PositiveIntegerField(default=0)

    class Meta:
        db_table = "recommendation_usage"
