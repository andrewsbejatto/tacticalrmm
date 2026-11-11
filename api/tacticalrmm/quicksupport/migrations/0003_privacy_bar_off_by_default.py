from django.db import migrations, models


def privacy_bar_off(apps, schema_editor):
    # the privacy bar is now off by default (also for existing installs)
    QuickSupportConfig = apps.get_model("quicksupport", "QuickSupportConfig")
    QuickSupportConfig.objects.update(show_privacy_bar=False)


class Migration(migrations.Migration):
    dependencies = [
        ("quicksupport", "0002_quicksupportconfig_show_privacy_bar"),
    ]

    operations = [
        migrations.AlterField(
            model_name="quicksupportconfig",
            name="show_privacy_bar",
            field=models.BooleanField(default=False),
        ),
        migrations.RunPython(privacy_bar_off, reverse_code=migrations.RunPython.noop),
    ]
