import hashlib
import json
import secrets
from datetime import timedelta
from functools import wraps

from django.conf import settings
from django.contrib.auth import logout
from django.core import signing
from django.core.exceptions import ValidationError
from django.db import transaction
from django.db.models import Q
from django.shortcuts import redirect
from django.utils import timezone

from masterdata.models import Employee
from terminal.models import TerminalDevice

DEVICE_COOKIE = "terminal_device"
WORKER_COOKIE = "terminal_worker"
IDLE_SECONDS = 15
DEVICE_SECONDS = 30 * 86400


def active_employees():
    return Employee.objects.filter(
        active=True, role__in=[Employee.Role.WORKER, Employee.Role.TEAM_LEADER]
    ).filter(Q(user__isnull=True) | Q(user__is_active=True))


def set_device_cookie(response, device):
    response.set_signed_cookie(
        DEVICE_COOKIE,
        json.dumps({"id": device.pk, "v": device.generation}),
        salt="terminal-device",
        max_age=DEVICE_SECONDS,
        httponly=True,
        secure=not settings.DEBUG,
        samesite="Strict",
        path="/terminal/",
    )


def set_worker_cookie(response, device, employee):
    response.set_signed_cookie(
        WORKER_COOKIE,
        json.dumps(
            {
                "id": employee.pk,
                "d": device.pk,
                "v": device.generation,
                "s": device.login_sequence,
            }
        ),
        salt="terminal-worker",
        max_age=IDLE_SECONDS,
        httponly=True,
        secure=not settings.DEBUG,
        samesite="Strict",
        path="/terminal/",
    )


def clear_worker_cookie(response):
    response.delete_cookie(WORKER_COOKIE, path="/terminal/", samesite="Strict")
    return response


def read_cookie(request, name, salt, age):
    try:
        # 用 JSON 签名载荷，避免反序列化或信任客户端时间。
        value = request.get_signed_cookie(name, salt=salt, max_age=age)
        payload = json.loads(value)
        return payload if isinstance(payload, dict) else None
    except (signing.BadSignature, KeyError, ValueError, SyntaxError, TypeError):
        return None


def terminal_identity(request):
    device_data = read_cookie(request, DEVICE_COOKIE, "terminal-device", DEVICE_SECONDS)
    if not device_data or any(
        type(device_data.get(key)) is not int for key in ("id", "v")
    ):
        return None, None
    device = (
        TerminalDevice.objects.filter(
            pk=device_data.get("id"), generation=device_data.get("v"), active=True
        ).first()
        if device_data
        else None
    )
    if device is None:
        return None, None
    worker_data = read_cookie(request, WORKER_COOKIE, "terminal-worker", IDLE_SECONDS)
    if worker_data and any(
        type(worker_data.get(key)) is not int for key in ("id", "d", "v", "s")
    ):
        worker_data = None
    if not worker_data or (
        worker_data.get("d"),
        worker_data.get("v"),
        worker_data.get("s"),
    ) != (device.pk, device.generation, device.login_sequence):
        return device, None
    return device, active_employees().filter(pk=worker_data.get("id")).first()


def worker_required(view):
    @wraps(view)
    def wrapped(request, *args, **kwargs):
        if request.user.is_authenticated:
            logout(request)
        device, employee = terminal_identity(request)
        if device is None:
            return clear_worker_cookie(redirect("terminal:activate"))
        if employee is None:
            return clear_worker_cookie(redirect("terminal:identify"))
        request.terminal_device = device
        request.terminal_employee = employee
        response = view(request, *args, **kwargs)
        # 业务校验错误也是员工操作；登录、退出不使用此装饰器。
        set_worker_cookie(response, device, employee)
        return response

    return wrapped


def activate(code):
    digest = hashlib.sha256(code.encode()).hexdigest()
    with transaction.atomic():
        device = (
            TerminalDevice.objects.select_for_update()
            .filter(
                activation_digest=digest,
                active=True,
                activation_expires_at__gt=timezone.now(),
            )
            .first()
        )
        if device is None or not secrets.compare_digest(
            device.activation_digest, digest
        ):
            raise ValidationError("激活码无效、已使用或已过期，请联系管理员重新授权")
        device.activation_digest = ""
        device.activation_expires_at = None
        device.save(update_fields=["activation_digest", "activation_expires_at"])
        return device


def identify(device, method, credential):
    error = None
    employee = None
    with transaction.atomic():
        current = TerminalDevice.objects.select_for_update().get(pk=device.pk)
        if not current.active or current.generation != device.generation:
            raise ValidationError("终端授权已失效")
        now = timezone.now()
        if current.failure_window is None or now - current.failure_window >= timedelta(
            minutes=1
        ):
            current.failure_window, current.failed_attempts = now, 0
        if current.failed_attempts >= 10:
            error = "识别失败次数较多，请稍后重试"
        else:
            field = "badge_nfc" if method == "nfc" else "qr_code"
            employee = active_employees().filter(**{field: credential}).first()
            if employee is None:
                current.failed_attempts += 1
                error = "工牌或二维码无效，请联系班组长核对"
            else:
                current.failed_attempts = 0
                current.login_sequence += 1
        current.save(
            update_fields=["failed_attempts", "failure_window", "login_sequence"]
        )
    if error:
        raise ValidationError(error)
    device.refresh_from_db()
    return employee
