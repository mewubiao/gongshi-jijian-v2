"""单独进程里每 5 分钟同步一次易飞。不要和 runserver / gunicorn 放在一起。"""

from django.core.management.base import BaseCommand

from erp.scheduler import run_blocking_scheduler


class Command(BaseCommand):
    help = "每 5 分钟执行一次易飞只读同步。这个进程请单独跑，不要塞进网页工作进程。"

    def handle(self, *args, **options):
        self.stdout.write(
            "易飞同步调度已启动。间隔见 YIFEI_SYNC_INTERVAL_SECONDS，默认 300 秒。"
        )
        run_blocking_scheduler()
