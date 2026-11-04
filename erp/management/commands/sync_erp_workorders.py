"""从易飞拉取未结工单和生产入库。只读，不回写。"""

from django.core.management.base import BaseCommand, CommandError

from erp.models import SyncRun
from erp.sync import LockLost, load_runtime, preview_sync, run_from_environment
from erp.yifei_config import YifeiConfigError


class Command(BaseCommand):
    help = "从易飞拉取未结工单和生产入库数量，更新本地镜像。只读，不回写易飞。"

    def add_arguments(self, parser):
        parser.add_argument(
            "--dry-run",
            action="store_true",
            help="拉取并试算工单变更，然后回滚。不写同步记录，也不写入库。",
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
            run = run_from_environment()
        except YifeiConfigError as exc:
            raise CommandError(str(exc)) from exc
        except LockLost as exc:
            raise CommandError(str(exc)) from exc
        self.stdout.write(
            "工单："
            f"拉取 {run.fetched_count}，新增 {run.created_count}，"
            f"更新 {run.updated_count}，未变化 {run.unchanged_count}，"
            f"关闭 {run.closed_count}，跳过 {run.skipped_count}"
        )
        self.stdout.write(
            "入库："
            f"拉取 {run.receipt_fetched_count}，新增 {run.receipt_created_count}，"
            f"更新 {run.receipt_updated_count}，未变化 {run.receipt_unchanged_count}，"
            f"移除 {run.receipt_removed_count}，跳过 {run.receipt_skipped_count}"
        )
        for warning in run.warnings:
            self.stdout.write(f"告警：{warning}")
        if run.status == SyncRun.Status.SKIPPED:
            self.stdout.write(self.style.WARNING(run.error))
            return
        if run.status == SyncRun.Status.FAILED:
            raise CommandError(run.error or "同步失败")
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
        f"移除 {stats.removed}，跳过 {stats.skipped}"
    )
