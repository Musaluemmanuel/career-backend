from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [
        ("api", "0002_recommendationusage"),
    ]

    operations = [
        migrations.SeparateDatabaseAndState(
            database_operations=[
                migrations.RunSQL(
                    sql='ALTER TABLE "user" ADD COLUMN "personalization_consent" BOOLEAN NOT NULL DEFAULT FALSE',
                    reverse_sql=migrations.RunSQL.noop,
                ),
                migrations.RunSQL(
                    sql='ALTER TABLE "user" ADD COLUMN "improvement_consent" BOOLEAN NOT NULL DEFAULT FALSE',
                    reverse_sql=migrations.RunSQL.noop,
                ),
                migrations.RunSQL(
                    sql='ALTER TABLE "user" ADD COLUMN "consent_updated_at" TIMESTAMP NULL',
                    reverse_sql=migrations.RunSQL.noop,
                ),
                migrations.RunSQL(
                    sql='ALTER TABLE "user" ADD COLUMN "consent_policy_version" VARCHAR(32) NULL',
                    reverse_sql=migrations.RunSQL.noop,
                ),
                migrations.RunSQL(
                    sql='ALTER TABLE "student_log" ADD COLUMN "contributed_to_improvement" BOOLEAN NOT NULL DEFAULT FALSE',
                    reverse_sql=migrations.RunSQL.noop,
                ),
            ],
            state_operations=[
                migrations.AddField(
                    model_name="user",
                    name="personalization_consent",
                    field=models.BooleanField(default=False),
                ),
                migrations.AddField(
                    model_name="user",
                    name="improvement_consent",
                    field=models.BooleanField(default=False),
                ),
                migrations.AddField(
                    model_name="user",
                    name="consent_updated_at",
                    field=models.DateTimeField(blank=True, null=True),
                ),
                migrations.AddField(
                    model_name="user",
                    name="consent_policy_version",
                    field=models.CharField(blank=True, max_length=32, null=True),
                ),
                migrations.AddField(
                    model_name="studentlog",
                    name="contributed_to_improvement",
                    field=models.BooleanField(default=False),
                ),
            ],
        ),
    ]
