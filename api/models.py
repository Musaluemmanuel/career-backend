from django.db import models


class User(models.Model):
    email = models.CharField(max_length=120, unique=True)
    password_hash = models.CharField(max_length=256)
    name = models.CharField(max_length=100, null=True, blank=True)
    is_verified = models.BooleanField(default=False)
    verification_code = models.CharField(max_length=10, null=True, blank=True)
    has_taken_test = models.BooleanField(default=False, db_column="_has_taken_test")
    history = models.JSONField(default=list)
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
    grades = models.JSONField(null=True, blank=True)
    ai_response = models.JSONField(null=True, blank=True)
    history = models.JSONField(default=list)
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
