from django.conf import settings

class JWTAuthCookieMiddleware:
    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        if settings.DEBUG:
            token = request.COOKIES.get("jwt") or request.GET.get("jwt")
            if token and "HTTP_AUTHORIZATION" not in request.META:
                if not token.lower().startswith("bearer "):
                    token = f"Bearer {token}"
                request.META["HTTP_AUTHORIZATION"] = token
        return self.get_response(request)
