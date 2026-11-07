import os
import sys

from django.apps import AppConfig


class ApiConfig(AppConfig):
    default_auto_field = "django.db.models.AutoField"
    name = "api"

    def ready(self):
        is_web_worker = any("gunicorn" in argument.lower() for argument in sys.argv)
        is_runserver_worker = (
            "runserver" in sys.argv
            and (
                os.environ.get("RUN_MAIN") == "true"
                or "--noreload" in sys.argv
            )
        )
        if is_web_worker or is_runserver_worker:
            from utils.web_scraper import start_course_page_maintenance

            start_course_page_maintenance()
