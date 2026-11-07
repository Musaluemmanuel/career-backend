from django.conf import settings
from django.http import HttpResponse


class CorsMiddleware:
    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        if request.method == "OPTIONS":
            response = HttpResponse(status=200)
        else:
            response = self.get_response(request)

        origin = request.headers.get("Origin")
        allowed_origins = {
            item.strip()
            for item in settings.CORS_ALLOWED_ORIGINS.split(",")
            if item.strip()
        }
        if origin and (not allowed_origins or origin in allowed_origins):
            response["Access-Control-Allow-Origin"] = origin
            response["Vary"] = "Origin"
        elif not origin and not allowed_origins:
            response["Access-Control-Allow-Origin"] = "*"
        response["Access-Control-Allow-Credentials"] = "true"
        response["Access-Control-Allow-Headers"] = "Content-Type, Authorization"
        response["Access-Control-Allow-Methods"] = "GET, POST, DELETE, OPTIONS"
        return response
