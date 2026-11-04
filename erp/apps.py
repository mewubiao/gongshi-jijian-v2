from django.apps import AppConfig


class ErpConfig(AppConfig):
    default_auto_field = "django.db.models.BigAutoField"
    name = "erp"
    verbose_name = "易飞镜像"

    def ready(self):
        from django.db.models.signals import post_migrate

        from erp.mappings import seed_receipt_mappings

        post_migrate.connect(seed_receipt_mappings, sender=self)
