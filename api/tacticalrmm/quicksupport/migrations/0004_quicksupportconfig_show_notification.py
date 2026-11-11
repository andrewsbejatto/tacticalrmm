from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [
        ("quicksupport", "0003_privacy_bar_off_by_default"),
    ]

    operations = [
        migrations.AddField(
            model_name="quicksupportconfig",
            name="show_notification",
            field=models.BooleanField(default=False),
        ),
    ]
