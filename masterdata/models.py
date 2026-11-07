"""员工、班组、工作中心、产品与工序。

业务规则以 docs/DESIGN.md 为准。单价和标准工时修改必须留下修改人、
修改时间、旧值和新值。班组默认继承工作中心班次，也可以整段覆盖。
"""

from django.conf import settings
from django.core.exceptions import ValidationError
from django.db import models, transaction
from django.utils import timezone

from masterdata.access import require_role
from masterdata.shifts import validate_shift_pair
from masterdata.validation import ensure_aware, ensure_two_places


class Employee(models.Model):
    """员工主数据。管理端账号可选关联，终端仍走工牌 NFC 或二维码。"""

    class Role(models.TextChoices):
        WORKER = "worker", "车间工人"
        TEAM_LEADER = "team_leader", "班组长"
        SETTLEMENT = "settlement", "月结结算"
        PROCESS_ENGINEER = "process_engineer", "工艺人员"
        ADMIN = "admin", "系统管理员"

    code = models.CharField("员工编码", max_length=32, unique=True)
    name = models.CharField("姓名", max_length=64)
    badge_nfc = models.CharField(
        "工牌 NFC 号",
        max_length=64,
        null=True,
        blank=True,
        help_text="工牌丢失时可空，凭二维码标识进入终端。有值时全局唯一。",
    )
    qr_code = models.CharField("二维码标识", max_length=128)
    role = models.CharField("角色", max_length=32, choices=Role.choices)
    team = models.ForeignKey(
        "Team",
        verbose_name="班组",
        null=True,
        blank=True,
        on_delete=models.PROTECT,
        related_name="employees",
    )
    user = models.OneToOneField(
        settings.AUTH_USER_MODEL,
        verbose_name="登录账号",
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="employee",
        help_text="管理端登录须关联启用账号；车间终端使用工牌或二维码认人。",
    )
    active = models.BooleanField("启用", default=True, db_index=True)

    class Meta:
        verbose_name = "员工"
        verbose_name_plural = "员工"
        constraints = [
            models.UniqueConstraint(
                fields=["badge_nfc"],
                condition=models.Q(badge_nfc__isnull=False),
                name="employee_badge_nfc_unique",
            ),
            models.UniqueConstraint(
                fields=["qr_code"],
                name="employee_qr_code_unique",
            ),
            models.CheckConstraint(
                condition=models.Q(
                    role__in=[
                        "worker",
                        "team_leader",
                        "settlement",
                        "process_engineer",
                        "admin",
                    ]
                ),
                name="employee_role_valid",
            ),
        ]

    def __str__(self):
        return f"{self.name}（{self.get_role_display()}）"

    def clean(self):
        self._normalize_identity()
        if not self.qr_code:
            raise ValidationError({"qr_code": "二维码标识不能为空"})

    def save(self, *args, **kwargs):
        self._normalize_identity()
        self.full_clean()
        super().save(*args, **kwargs)

    def _normalize_identity(self):
        if self.badge_nfc is None or not str(self.badge_nfc).strip():
            self.badge_nfc = None
        else:
            self.badge_nfc = str(self.badge_nfc).strip()
        self.qr_code = "" if self.qr_code is None else str(self.qr_code).strip()


class WorkCenter(models.Model):
    """工作中心。白班 / 夜班起止是班次的默认配置。"""

    code = models.CharField("工作中心编码", max_length=32, unique=True)
    name = models.CharField("工作中心名称", max_length=100)
    active = models.BooleanField("启用", default=True, db_index=True)
    day_shift_start = models.TimeField("白班开始")
    day_shift_end = models.TimeField("白班结束")
    night_shift_start = models.TimeField("夜班开始")
    night_shift_end = models.TimeField("夜班结束")

    class Meta:
        verbose_name = "工作中心"
        verbose_name_plural = "工作中心"

    def __str__(self):
        return f"{self.code} {self.name}"

    def clean(self):
        validate_shift_pair(
            self.day_shift_start,
            self.day_shift_end,
            self.night_shift_start,
            self.night_shift_end,
        )

    def save(self, *args, **kwargs):
        self.full_clean()
        super().save(*args, **kwargs)


class Team(models.Model):
    """班组。班次默认继承工作中心，四个钟点都填写时改为整段覆盖。"""

    code = models.CharField("班组编码", max_length=32, unique=True)
    name = models.CharField("班组名称", max_length=100)
    work_center = models.ForeignKey(
        WorkCenter,
        verbose_name="工作中心",
        on_delete=models.PROTECT,
        related_name="teams",
    )
    leader = models.ForeignKey(
        Employee,
        verbose_name="班组长",
        on_delete=models.PROTECT,
        related_name="led_teams",
    )
    active = models.BooleanField("启用", default=True, db_index=True)
    day_shift_start = models.TimeField("白班开始", null=True, blank=True)
    day_shift_end = models.TimeField("白班结束", null=True, blank=True)
    night_shift_start = models.TimeField("夜班开始", null=True, blank=True)
    night_shift_end = models.TimeField("夜班结束", null=True, blank=True)

    class Meta:
        verbose_name = "班组"
        verbose_name_plural = "班组"
        constraints = [
            models.UniqueConstraint(
                fields=["work_center", "name"],
                name="team_name_unique_in_work_center",
            )
        ]

    def __str__(self):
        return self.name

    @property
    def has_shift_override(self):
        return all(
            value is not None
            for value in (
                self.day_shift_start,
                self.day_shift_end,
                self.night_shift_start,
                self.night_shift_end,
            )
        )

    def clean(self):
        if self.leader_id and self.leader.role != Employee.Role.TEAM_LEADER:
            raise ValidationError({"leader": "班组长必须是班组长角色"})
        if self.leader_id and not self.leader.active:
            raise ValidationError({"leader": "班组长必须在职"})
        times = (
            self.day_shift_start,
            self.day_shift_end,
            self.night_shift_start,
            self.night_shift_end,
        )
        if any(value is not None for value in times) and not self.has_shift_override:
            raise ValidationError(
                "班次覆盖须同时填写白班和夜班起止，或全部留空以继承工作中心"
            )
        if self.has_shift_override:
            validate_shift_pair(*times)

    def save(self, *args, **kwargs):
        self.full_clean()
        super().save(*args, **kwargs)


class Product(models.Model):
    code = models.CharField("产品编码", max_length=64, unique=True)
    name = models.CharField("品名", max_length=200)
    specification = models.CharField("规格", max_length=300, blank=True, default="")
    unit = models.CharField("单位", max_length=40, blank=True, default="")
    active = models.BooleanField("启用", default=True, db_index=True)

    class Meta:
        verbose_name = "产品"
        verbose_name_plural = "产品"

    def __str__(self):
        return f"{self.code} {self.name}"


class Process(models.Model):
    """产品上的一道工序。顺序即本系统维护的工艺路线。"""

    product = models.ForeignKey(
        Product,
        verbose_name="产品",
        on_delete=models.PROTECT,
        related_name="processes",
    )
    operation_code = models.CharField("工序编码", max_length=50)
    sequence = models.PositiveIntegerField("顺序号")
    name = models.CharField("工序名称", max_length=100)
    active = models.BooleanField("启用", default=True, db_index=True)
    unit_price = models.DecimalField("单价", max_digits=12, decimal_places=2)
    standard_minutes = models.DecimalField(
        "标准工时（分钟）",
        max_digits=10,
        decimal_places=2,
    )

    class Meta:
        verbose_name = "工序"
        verbose_name_plural = "工序"
        ordering = ["product_id", "sequence"]
        constraints = [
            models.UniqueConstraint(
                fields=["product", "operation_code"],
                name="process_operation_code_unique",
            ),
            models.UniqueConstraint(
                fields=["product", "sequence"],
                name="process_sequence_unique",
            ),
            models.CheckConstraint(
                condition=models.Q(sequence__gte=1),
                name="process_sequence_positive",
            ),
            models.CheckConstraint(
                condition=models.Q(unit_price__gte=0),
                name="process_unit_price_non_negative",
            ),
            models.CheckConstraint(
                condition=models.Q(standard_minutes__gte=0),
                name="process_standard_minutes_non_negative",
            ),
        ]

    def __str__(self):
        return f"{self.product.code}-{self.sequence} {self.name}"

    def clean(self):
        self.operation_code = (self.operation_code or "").strip()
        if not self.operation_code:
            raise ValidationError({"operation_code": "工序编码不能为空"})
        ensure_two_places(self.unit_price, "unit_price", "单价")
        ensure_two_places(self.standard_minutes, "standard_minutes", "标准工时")
        if self.unit_price is not None and self.unit_price < 0:
            raise ValidationError({"unit_price": "单价不能为负"})
        if self.standard_minutes is not None and self.standard_minutes < 0:
            raise ValidationError({"standard_minutes": "标准工时不能为负"})

    def save(self, *args, changed_by=None, changed_at=None, **kwargs):
        with transaction.atomic():
            previous = None
            rate_changed = False
            when = None
            if self.pk:
                previous = Process.objects.select_for_update().get(pk=self.pk)
                rate_changed = (
                    previous.unit_price != self.unit_price
                    or previous.standard_minutes != self.standard_minutes
                )
            if rate_changed:
                if changed_by is None:
                    raise ValidationError("修改单价或标准工时必须记录修改人")
                require_role(
                    changed_by,
                    Employee.Role.PROCESS_ENGINEER,
                    "单价和标准工时由启用的工艺人员修改",
                )
                when = changed_at or timezone.now()
                ensure_aware(when, "changed_at")
            self.full_clean()
            super().save(*args, **kwargs)
            if rate_changed:
                ProcessRateHistory.objects.create(
                    process=self,
                    changed_by=changed_by,
                    changed_at=when,
                    old_unit_price=previous.unit_price,
                    new_unit_price=self.unit_price,
                    old_standard_minutes=previous.standard_minutes,
                    new_standard_minutes=self.standard_minutes,
                )


class ProcessRateHistory(models.Model):
    """单价、标准工时的修改历史。只追加，不覆盖。"""

    process = models.ForeignKey(
        Process,
        verbose_name="工序",
        on_delete=models.PROTECT,
        related_name="rate_history",
    )
    changed_by = models.ForeignKey(
        Employee,
        verbose_name="修改人",
        on_delete=models.PROTECT,
        related_name="process_rate_changes",
    )
    changed_at = models.DateTimeField("修改时间")
    old_unit_price = models.DecimalField("原单价", max_digits=12, decimal_places=2)
    new_unit_price = models.DecimalField("新单价", max_digits=12, decimal_places=2)
    old_standard_minutes = models.DecimalField(
        "原标准工时（分钟）",
        max_digits=10,
        decimal_places=2,
    )
    new_standard_minutes = models.DecimalField(
        "新标准工时（分钟）",
        max_digits=10,
        decimal_places=2,
    )

    class Meta:
        verbose_name = "工序工价修改历史"
        verbose_name_plural = "工序工价修改历史"
        ordering = ["-changed_at", "-pk"]

    def __str__(self):
        return f"{self.process} @ {timezone.localtime(self.changed_at):%Y-%m-%d %H:%M}"

    def clean(self):
        ensure_aware(self.changed_at, "changed_at")
        if self.changed_by_id and (
            self.changed_by.role != Employee.Role.PROCESS_ENGINEER
        ):
            raise ValidationError({"changed_by": "修改人须为工艺人员"})

    def save(self, *args, **kwargs):
        if self.pk:
            raise ValidationError("工序修改历史不能修改")
        self.full_clean()
        super().save(*args, **kwargs)

    def delete(self, *args, **kwargs):
        raise ValidationError("工序修改历史不能删除")
