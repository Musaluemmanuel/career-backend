from django.db import migrations, models


class Migration(migrations.Migration):
    initial = True

    dependencies = []

    operations = [
        migrations.CreateModel(
            name="User",
            fields=[
                ("id", models.AutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                ("email", models.CharField(max_length=120, unique=True)),
                ("password_hash", models.CharField(max_length=256)),
                ("name", models.CharField(blank=True, max_length=100, null=True)),
                ("is_verified", models.BooleanField(default=False)),
                ("verification_code", models.CharField(blank=True, max_length=10, null=True)),
                ("has_taken_test", models.BooleanField(db_column="_has_taken_test", default=False)),
                ("history", models.JSONField(default=list)),
            ],
            options={"db_table": "user"},
        ),
        migrations.CreateModel(
            name="StudentLog",
            fields=[
                ("id", models.AutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                ("timestamp", models.CharField(blank=True, max_length=50, null=True)),
                ("student_name", models.CharField(blank=True, max_length=100, null=True)),
                ("interest", models.CharField(blank=True, max_length=100, null=True)),
                ("points", models.IntegerField(blank=True, null=True)),
                ("grades", models.JSONField(blank=True, null=True)),
                ("ai_response", models.JSONField(blank=True, null=True)),
                ("history", models.JSONField(default=list)),
            ],
            options={"db_table": "student_log"},
        ),
    ]
