from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [
        ("quicksupport", "0001_initial"),
    ]

    operations = [
        migrations.AddField(
            model_name="quicksupportconfig",
            name="show_privacy_bar",
            field=models.BooleanField(default=True),
        ),
    ]
