from django.db import migrations, models


def copy_can_use_mesh_to_quick_support(apps, schema_editor):
    # quick support opens MeshCentral sessions, like "Use MeshCentral"
    Role = apps.get_model("accounts", "Role")
    Role.objects.filter(can_use_mesh=True).update(can_use_quick_support=True)


class Migration(migrations.Migration):
    dependencies = [
        ("accounts", "0044_copy_mesh_permission_to_file_browser"),
    ]

    operations = [
        migrations.AddField(
            model_name="role",
            name="can_use_quick_support",
            field=models.BooleanField(default=False),
        ),
        migrations.RunPython(
            copy_can_use_mesh_to_quick_support,
            reverse_code=migrations.RunPython.noop,
        ),
    ]
