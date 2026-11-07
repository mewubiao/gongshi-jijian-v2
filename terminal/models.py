import hashlib
import secrets
from datetime import timedelta

from django.db import models, transaction
from django.utils import timezone


class TerminalDevice(models.Model):
    code = models.CharField("终端编码", max_length=32, unique=True)
    name = models.CharField("终端名称", max_length=100)
    active = models.BooleanField("启用", default=True)
    generation = models.PositiveIntegerField(default=1, editable=False)
    login_sequence = models.PositiveIntegerField(default=0, editable=False)
    activation_digest = models.CharField(max_length=64, blank=True, editable=False)
    activation_expires_at = models.DateTimeField(null=True, editable=False)
    failed_attempts = models.PositiveIntegerField(default=0, editable=False)
    failure_window = models.DateTimeField(null=True, editable=False)

    class Meta:
        verbose_name = "车间终端"
        verbose_name_plural = "车间终端"

    def __str__(self):
        return f"{self.code} · {self.name}"

    def issue_activation(self):
        code = secrets.token_urlsafe(24)
        with transaction.atomic():
            current = type(self).objects.select_for_update().get(pk=self.pk)
            current.generation += 1
            current.active = True
            current.activation_digest = hashlib.sha256(code.encode()).hexdigest()
            current.activation_expires_at = timezone.now() + timedelta(minutes=15)
            current.failed_attempts = 0
            current.save()
        self.refresh_from_db()
        return code
