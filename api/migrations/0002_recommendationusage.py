from django.db import migrations, models
import django.db.models.deletion


class Migration(migrations.Migration):

    dependencies = [
        ("api", "0001_initial"),
    ]

    operations = [
        migrations.SeparateDatabaseAndState(
            database_operations=[
                migrations.RunSQL(
                    sql=(
                        'CREATE TABLE IF NOT EXISTS "recommendation_usage" ('
                        '"user_id" INTEGER NOT NULL PRIMARY KEY '
                        'REFERENCES "user" ("id") ON DELETE CASCADE, '
                        '"attempts" INTEGER NOT NULL DEFAULT 0 '
                        'CHECK ("attempts" >= 0))'
                    ),
                    reverse_sql=migrations.RunSQL.noop,
                ),
            ],
            state_operations=[
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
            ],
        ),
    ]
