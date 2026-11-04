from django.conf import settings


def gallery_allowed(request):
    """组件示例只在调试模式或已登录时打开。"""
    user = getattr(request, "user", None)
    return settings.DEBUG or bool(user and user.is_authenticated)
