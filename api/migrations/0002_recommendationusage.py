from django.db import migrations, models
import django.db.models.deletion


class Migration(migrations.Migration):

    dependencies = [
        ("api", "0001_initial"),
    ]

    operations = [
        migrations.CreateModel(
            name="RecommendationUsage",
            fields=[
                (
                    "user",
                    models.OneToOneField(
                        db_column="user_id",
                        on_delete=django.db.models.deletion.CASCADE,
                        primary_key=True,
                        related_name="recommendation_usage",
                        serialize=False,
                        to="api.user",
                    ),
                ),
                ("attempts", models.PositiveIntegerField(default=0)),
            ],
            options={
                "db_table": "recommendation_usage",
            },
        ),
    ]
