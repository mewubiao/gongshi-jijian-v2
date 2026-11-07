import hashlib
import json

from django.core import signing
from django.core.exceptions import ValidationError
from django.utils.crypto import constant_time_compare


def settlement_revision(period, entries=(), minutes=None):
    last_event = (
        period.events.order_by("-pk").values_list("pk", flat=True).first()
        if period.pk
        else None
    )
    payload = [
        period.year,
        period.month,
        period.status,
        last_event,
        [
            [
                entry.report_id,
                entry.employee_id,
                entry.employee_code,
                entry.employee_name,
                entry.order_no,
                entry.process_name,
                entry.ownership_date.isoformat(),
                format(entry.quantity, ".2f"),
                format(entry.unit_price, ".2f"),
                entry.group_seconds,
                entry.amount_cents,
            ]
            for entry in entries
        ],
        [(key, format(value, ".4f")) for key, value in sorted((minutes or {}).items())],
    ]
    digest = hashlib.sha256(
        json.dumps(payload, separators=(",", ":")).encode()
    ).hexdigest()
    return signing.Signer(salt="settlement-revision").sign(digest)


def check_revision(period, expected, entries=(), minutes=None):
    if expected is not None and not constant_time_compare(
        expected, settlement_revision(period, entries, minutes)
    ):
        raise ValidationError("月结数据已变化，请刷新并重新核对后操作")
