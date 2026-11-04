"""从易飞拉取未结工单和生产入库。只读，不回写。"""

from django.core.management.base import BaseCommand, CommandError

from erp.models import SyncLog
from erp.sync import LockLost, load_runtime, preview_sync, run_from_environment
from erp.yifei_config import YifeiConfigError


class Command(BaseCommand):
    help = "从易飞拉取未结工单和生产入库数量，更新本地镜像。只读，不回写易飞。"

    def add_arguments(self, parser):
        parser.add_argument(
            "--dry-run",
            action="store_true",
            help="拉取并试算变更，然后回滚。不写同步日志。",
        )

    def handle(self, *args, **options):
        try:
            if options["dry_run"]:
                source, work_map, receipt_map, schedule, _connection = load_runtime()
                work_stats, receipt_stats = preview_sync(
                    source,
                    work_map,
                    receipt_map,
                    stale_seconds=schedule.stale_seconds,
                )
                self.stdout.write(self.style.WARNING("试运行，未写入。"))
                self.stdout.write(_format_work(work_stats))
                self.stdout.write(_format_receipt(receipt_stats))
                for warning in [*work_stats["warnings"], *receipt_stats.warnings]:
                    self.stdout.write(f"告警：{warning}")
                return
            work_log, receipt_log = run_from_environment()
        except YifeiConfigError as exc:
            raise CommandError(str(exc)) from exc
        except LockLost as exc:
            raise CommandError(str(exc)) from exc
        self.stdout.write(_format_log("工单", work_log))
        if receipt_log is None:
            self.stdout.write("入库：未执行")
        else:
            self.stdout.write(_format_log("入库", receipt_log))
        for log in (work_log, receipt_log):
            if log is not None and log.error_summary:
                self.stdout.write(log.error_summary)
        failed = work_log.status == SyncLog.Status.FAILED or (
            receipt_log is not None and receipt_log.status == SyncLog.Status.FAILED
        )
        if failed:
            summary = work_log.error_summary
            if receipt_log is not None and receipt_log.status == SyncLog.Status.FAILED:
                summary = receipt_log.error_summary or summary
            raise CommandError(summary or "同步失败")
        self.stdout.write(self.style.SUCCESS("同步完成"))


def _format_work(stats):
    return (
        "工单："
        f"拉取 {stats['fetched']}，新增 {stats['created']}，"
        f"更新 {stats['updated']}，未变化 {stats['unchanged']}，"
        f"关闭 {stats['closed']}，跳过 {stats['skipped']}"
    )


def _format_receipt(stats):
    return (
        "入库："
        f"拉取 {stats.fetched}，新增 {stats.created}，"
        f"更新 {stats.updated}，未变化 {stats.unchanged}，"
        f"失效 {stats.removed}，跳过 {stats.skipped}"
    )


def _format_log(label, log):
    return (
        f"{label}："
        f"新增 {log.inserted}，更新 {log.updated}，"
        f"跳过 {log.skipped}，失效 {log.invalidated}"
    )
