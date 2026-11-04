from django.http import JsonResponse


def health(request):
    """进程可以响应请求。这里不访问数据库。"""
    return JsonResponse({"status": "ok"})
