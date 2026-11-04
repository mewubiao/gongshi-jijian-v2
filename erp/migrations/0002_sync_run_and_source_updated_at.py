from django.db import migrations, models


def seed_sync_lock(apps, schema_editor):
    sync_lock = apps.get_model("erp", "SyncLock")
    sync_lock.objects.get_or_create(
        name="workorders",
        defaults={"holder": "", "held_until": None},
    )


def unseed_sync_lock(apps, schema_editor):
    sync_lock = apps.get_model("erp", "SyncLock")
    sync_lock.objects.filter(name="workorders").delete()


class Migration(migrations.Migration):
    dependencies = [
        ("erp", "0001_initial"),
    ]

    operations = [
        migrations.CreateModel(
            name="SyncLock",
            fields=[
                (
                    "name",
                    models.CharField(max_length=32, primary_key=True, serialize=False),
                ),
                ("holder", models.CharField(blank=True, default="", max_length=64)),
                ("held_until", models.DateTimeField(blank=True, null=True)),
            ],
            options={
                "verbose_name": "易飞同步锁",
                "verbose_name_plural": "易飞同步锁",
            },
        ),
        migrations.RunPython(seed_sync_lock, unseed_sync_lock),
    ]
