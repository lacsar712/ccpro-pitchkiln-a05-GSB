# Generated for night-duty card (夜班在岗卡)

from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("kiln", "0001_initial"),
    ]

    operations = [
        migrations.CreateModel(
            name="NightDutyCard",
            fields=[
                (
                    "id",
                    models.BigAutoField(
                        auto_created=True,
                        primary_key=True,
                        serialize=False,
                        verbose_name="ID",
                    ),
                ),
                ("lane", models.PositiveIntegerField(verbose_name="过道号")),
                ("dutyDate", models.DateField(verbose_name="值班日")),
                ("shiftName", models.CharField(max_length=40, verbose_name="班次名称")),
                (
                    "maxActiveHearths",
                    models.PositiveIntegerField(verbose_name="上限灶数（人灶同数）"),
                ),
                (
                    "supervisorName",
                    models.CharField(max_length=80, verbose_name="值班主管姓名"),
                ),
            ],
            options={
                "verbose_name": "夜班在岗卡",
                "verbose_name_plural": "夜班在岗卡",
                "ordering": ["-dutyDate", "lane", "shiftName"],
            },
        ),
        migrations.AddConstraint(
            model_name="nightdutycard",
            constraint=models.UniqueConstraint(
                fields=("lane", "dutyDate", "shiftName"),
                name="uniq_lane_date_shift",
            ),
        ),
    ]
