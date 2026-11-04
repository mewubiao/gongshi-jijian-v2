"""派工。单人一条组员；整组按顺序，第一位是初始小组长，身份可移交。

尾差归派工时列出的第一位（sequence=1），不随小组长移交改变。
分配计算留给结算服务，这里只保住这个人不被改写。

TODO(派工): 第一期不做派工超时提醒。
"""

from django.core.exceptions import ValidationError
from django.db import models, transaction
from django.utils import timezone

from erp.models import WorkOrder
from masterdata.models import Employee, Process
from masterdata.validation import ensure_aware, ensure_two_places


class Dispatch(models.Model):
    """班组长把工单上的工序派给工人。请用 create_single / create_group 写入。"""

    class Kind(models.TextChoices):
        SINGLE = "single", "单人"
        GROUP = "group", "整组"

    work_order = models.ForeignKey(
        WorkOrder,
        verbose_name="工单",
        on_delete=models.PROTECT,
        related_name="dispatches",
    )
    process = models.ForeignKey(
        Process,
        verbose_name="工序",
        on_delete=models.PROTECT,
        related_name="dispatches",
    )
    kind = models.CharField("派工方式", max_length=16, choices=Kind.choices)
    quantity = models.DecimalField("派工数量", max_digits=12, decimal_places=2)
    assigned_by = models.ForeignKey(
        Employee,
        verbose_name="派工人",
        on_delete=models.PROTECT,
        related_name="assigned_dispatches",
        help_text="班组长。第一期允许自派自审。",
    )
    assigned_at = models.DateTimeField("派工时间")
    current_leader = models.ForeignKey(
        Employee,
        verbose_name="当前小组长",
        on_delete=models.PROTECT,
        related_name="led_dispatches",
        help_text="整组可移交。单人派工就是这名工人。不是班组长角色。",
    )

    class Meta:
        verbose_name = "派工"
        verbose_name_plural = "派工"
        constraints = [
            models.CheckConstraint(
                condition=models.Q(quantity__gt=0),
                name="dispatch_quantity_positive",
            ),
            models.CheckConstraint(
                condition=models.Q(kind__in=["single", "group"]),
                name="dispatch_kind_valid",
            ),
        ]

    def __str__(self):
        return f"{self.work_order.order_no} / {self.process.name}"

    def clean(self):
        ensure_two_places(self.quantity, "quantity", "派工数量")
        ensure_aware(self.assigned_at, "assigned_at")
        if self.quantity is not None and self.quantity <= 0:
            raise ValidationError({"quantity": "派工数量必须大于 0"})
        if self.assigned_by_id and (self.assigned_by.role != Employee.Role.TEAM_LEADER):
            raise ValidationError({"assigned_by": "派工由班组长执行"})
        if self.process_id and self.work_order_id:
            if self.process.product_id != self.work_order.product_id:
                raise ValidationError("工序必须属于工单的产品")
        adding = self._state.adding
        if self.work_order_id and self.quantity is not None:
            if adding or self._quantity_changed():
                if self.quantity > self.work_order.remaining_qty:
                    raise ValidationError("派工数量不能超过工单剩余量")
        if adding and self.work_order_id:
            if self.work_order.status == WorkOrder.Status.CLOSED:
                raise ValidationError("工单已关闭，不能再新派工")
        if self.pk and not adding:
            old = Dispatch.objects.get(pk=self.pk)
            if (
                old.work_order_id != self.work_order_id
                or old.process_id != self.process_id
                or old.kind != self.kind
            ):
                raise ValidationError("派工的工单、工序和单人/整组不能改")
            if old.assigned_by_id != self.assigned_by_id:
                raise ValidationError("派工人不能改")
            if self.members.exists():
                self.validate_roster()

    def save(self, *args, **kwargs):
        self.full_clean()
        super().save(*args, **kwargs)

    def _quantity_changed(self):
        if not self.pk:
            return True
        old = Dispatch.objects.get(pk=self.pk)
        return old.quantity != self.quantity

    def first_listed_member(self):
        """派工时列出的第一位。整组尾差归此人，与当前小组长无关。"""
        return self.members.order_by("sequence").first()

    def assert_terminal_operator(self, employee):
        """单人必须是被派工人；整组必须是当前小组长。小组长不是班组长角色。"""
        if self.kind == self.Kind.SINGLE:
            only = self.members.order_by("sequence").first()
            if only is None or employee.pk != only.employee_id:
                raise ValidationError("只能由被派工人操作")
            return
        if employee.pk != self.current_leader_id:
            raise ValidationError("整组任务由当前小组长操作")

    def validate_roster(self):
        members = list(self.members.select_related("employee").order_by("sequence"))
        if not members:
            raise ValidationError("派工必须列出人员")
        sequences = [member.sequence for member in members]
        if sequences != list(range(1, len(members) + 1)):
            raise ValidationError("组员顺序须从 1 连续编号")
        employee_ids = [member.employee_id for member in members]
        if len(set(employee_ids)) != len(employee_ids):
            raise ValidationError("组员不能重复")
        for member in members:
            if not member.employee.is_active:
                raise ValidationError("不能派给已停用的员工")
        if self.kind == self.Kind.SINGLE:
            if len(members) != 1:
                raise ValidationError("单人派工只能有一名工人")
            if self.current_leader_id != members[0].employee_id:
                raise ValidationError("单人派工的工人与当前执行人不一致")
            return
        if len(members) < 2:
            raise ValidationError("整组派工至少两名组员")
        if self.current_leader_id not in employee_ids:
            raise ValidationError("小组长必须是组员之一")
        transfers = list(self.leader_transfers.order_by("transferred_at", "pk"))
        if not transfers:
            if self.current_leader_id != members[0].employee_id:
                raise ValidationError("整组第一位组员是小组长")
        elif self.current_leader_id != transfers[-1].to_employee_id:
            raise ValidationError("当前小组长须与最近一次移交一致")

    @classmethod
    def create_single(
        cls,
        *,
        work_order,
        process,
        quantity,
        assignee,
        assigned_by,
        assigned_at=None,
    ):
        assigned_at = assigned_at or timezone.now()
        with transaction.atomic():
            dispatch = cls(
                work_order=work_order,
                process=process,
                kind=cls.Kind.SINGLE,
                quantity=quantity,
                assigned_by=assigned_by,
                assigned_at=assigned_at,
                current_leader=assignee,
            )
            dispatch.save()
            DispatchMember.objects.create(
                dispatch=dispatch,
                employee=assignee,
                sequence=1,
            )
            dispatch.validate_roster()
            return dispatch

    @classmethod
    def create_group(
        cls,
        *,
        work_order,
        process,
        quantity,
        members,
        assigned_by,
        assigned_at=None,
    ):
        """members 按派工顺序。第一位是初始小组长。"""
        if len(members) < 2:
            raise ValidationError("整组派工至少两名组员")
        if len({member.pk for member in members}) != len(members):
            raise ValidationError("组员不能重复")
        assigned_at = assigned_at or timezone.now()
        with transaction.atomic():
            dispatch = cls(
                work_order=work_order,
                process=process,
                kind=cls.Kind.GROUP,
                quantity=quantity,
                assigned_by=assigned_by,
                assigned_at=assigned_at,
                current_leader=members[0],
            )
            dispatch.save()
            for sequence, employee in enumerate(members, start=1):
                DispatchMember.objects.create(
                    dispatch=dispatch,
                    employee=employee,
                    sequence=sequence,
                )
            dispatch.validate_roster()
            return dispatch

    def transfer_leader(self, *, to_employee, transferred_at=None):
        """小组长离开前，先把身份移交给组内另一人。"""
        if self.kind != self.Kind.GROUP:
            raise ValidationError("单人派工不能移交小组长")
        if not self.members.filter(employee=to_employee).exists():
            raise ValidationError("只能移交给本组组员")
        if to_employee.pk == self.current_leader_id:
            raise ValidationError("对方已经是小组长")
        transferred_at = transferred_at or timezone.now()
        ensure_aware(transferred_at, "transferred_at")
        open_session = self.sessions.filter(ended_at__isnull=True).first()
        if open_session is not None:
            present = open_session.member_spans.filter(
                employee=to_employee,
                left_at__isnull=True,
            ).exists()
            if not present:
                raise ValidationError("开工后只能移交给当前在组的组员")
        with transaction.atomic():
            LeaderTransfer.objects.create(
                dispatch=self,
                from_employee_id=self.current_leader_id,
                to_employee=to_employee,
                transferred_at=transferred_at,
            )
            self.current_leader = to_employee
            self.save(update_fields=["current_leader"])


class DispatchMember(models.Model):
    """派工组员。sequence 从 1 起，1 是派工时列出的第一位。"""

    dispatch = models.ForeignKey(
        Dispatch,
        verbose_name="派工",
        on_delete=models.CASCADE,
        related_name="members",
    )
    employee = models.ForeignKey(
        Employee,
        verbose_name="员工",
        on_delete=models.PROTECT,
        related_name="dispatch_memberships",
    )
    sequence = models.PositiveIntegerField(
        "顺序",
        help_text="从 1 起。1 是派工时列出的第一位，整组尾差归此人。",
    )

    class Meta:
        verbose_name = "派工组员"
        verbose_name_plural = "派工组员"
        ordering = ["sequence"]
        constraints = [
            models.UniqueConstraint(
                fields=["dispatch", "sequence"],
                name="dispatch_member_sequence_unique",
            ),
            models.UniqueConstraint(
                fields=["dispatch", "employee"],
                name="dispatch_member_employee_unique",
            ),
            models.CheckConstraint(
                condition=models.Q(sequence__gte=1),
                name="dispatch_member_sequence_positive",
            ),
        ]

    def __str__(self):
        return f"{self.sequence}. {self.employee.name}"

    def clean(self):
        if self.sequence is not None and self.sequence < 1:
            raise ValidationError({"sequence": "顺序从 1 开始"})
        if self.employee_id and not self.employee.is_active:
            raise ValidationError({"employee": "不能派给已停用的员工"})

    def save(self, *args, **kwargs):
        self.full_clean()
        super().save(*args, **kwargs)


class LeaderTransfer(models.Model):
    """小组长移交留痕。第一位组员不因移交而改变。"""

    dispatch = models.ForeignKey(
        Dispatch,
        verbose_name="派工",
        on_delete=models.CASCADE,
        related_name="leader_transfers",
    )
    from_employee = models.ForeignKey(
        Employee,
        verbose_name="原小组长",
        on_delete=models.PROTECT,
        related_name="leader_transfers_from",
    )
    to_employee = models.ForeignKey(
        Employee,
        verbose_name="新小组长",
        on_delete=models.PROTECT,
        related_name="leader_transfers_to",
    )
    transferred_at = models.DateTimeField("移交时间")

    class Meta:
        verbose_name = "小组长移交"
        verbose_name_plural = "小组长移交"
        ordering = ["transferred_at", "pk"]

    def __str__(self):
        return f"{self.from_employee.name} → {self.to_employee.name}"

    def clean(self):
        ensure_aware(self.transferred_at, "transferred_at")
        if self.from_employee_id and self.from_employee_id == self.to_employee_id:
            raise ValidationError("不能移交给自己")
        if self.dispatch_id:
            member_ids = set(
                self.dispatch.members.values_list("employee_id", flat=True)
            )
            if member_ids:
                if self.from_employee_id not in member_ids:
                    raise ValidationError("原小组长必须是组员")
                if self.to_employee_id not in member_ids:
                    raise ValidationError("新小组长必须是组员")
            if self.dispatch.kind != Dispatch.Kind.GROUP:
                raise ValidationError("单人派工不能移交小组长")

    def save(self, *args, **kwargs):
        if self.pk:
            raise ValidationError("小组长移交记录不能修改")
        self.full_clean()
        super().save(*args, **kwargs)

    def delete(self, *args, **kwargs):
        raise ValidationError("小组长移交记录不能删除")
