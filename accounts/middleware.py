from django.contrib.auth import SESSION_KEY, logout


class RevokeInvalidSessionMiddleware:
    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        if not request.user.is_authenticated and SESSION_KEY in request.session:
            logout(request)
        return self.get_response(request)
