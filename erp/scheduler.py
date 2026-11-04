"""每 5 分钟跑一次同步。单独进程，不要放进网页进程。

用 APScheduler 的阻塞调度器，而不是 django-q2：这里只有这一件定时任务，
不需要队列、Redis 或额外的任务表。max_instances=1 加上数据库锁，
调度器自己和手工执行的命令都不会叠在一起跑。
"""

import logging
from zoneinfo import ZoneInfo

from django.conf import settings

logger = logging.getLogger("erp.scheduler")

JOB_ID = "sync_erp_workorders"


def scheduled_sync():
    from erp.sync import run_from_environment
    from erp.yifei_config import YifeiConfigError

    try:
        run = run_from_environment()
    except YifeiConfigError as exc:
        logger.error("易飞同步配置不可用：%s", exc)
        return
    except Exception as exc:
        logger.error("易飞定时同步没有完成：%s", exc)
        return
    logger.info(
        "易飞同步 %s 工单拉取 %s 新增 %s 更新 %s 关闭 %s 跳过 %s；入库拉取 %s 跳过 %s",
        run.status,
        run.fetched_count,
        run.created_count,
        run.updated_count,
        run.closed_count,
        run.skipped_count,
        run.receipt_fetched_count,
        run.receipt_skipped_count,
    )


def build_scheduler(interval_seconds=None):
    from apscheduler.schedulers.blocking import BlockingScheduler
    from apscheduler.triggers.interval import IntervalTrigger

    from erp.yifei_config import load_schedule_config

    if interval_seconds is None:
        interval_seconds = load_schedule_config().interval_seconds
    scheduler = BlockingScheduler(timezone=ZoneInfo(settings.TIME_ZONE))
    scheduler.add_job(
        scheduled_sync,
        trigger=IntervalTrigger(seconds=interval_seconds),
        id=JOB_ID,
        max_instances=1,
        coalesce=True,
        misfire_grace_time=interval_seconds,
        replace_existing=True,
    )
    return scheduler


def run_blocking_scheduler():
    scheduler = build_scheduler()
    scheduler.start()
