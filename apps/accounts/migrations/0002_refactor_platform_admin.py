from django.db import migrations, models


def copy_platform_admin_role(apps, schema_editor):
    User = apps.get_model("accounts", "User")
    User.objects.filter(role="PLATFORM_SUPER_ADMIN").update(is_platform_admin=True)


class Migration(migrations.Migration):
    dependencies = [
        ("accounts", "0001_initial"),
    ]

    operations = [
        migrations.AddField(
            model_name="user",
            name="is_platform_admin",
            field=models.BooleanField(default=False),
        ),
        migrations.RunPython(copy_platform_admin_role, migrations.RunPython.noop),
        migrations.RemoveField(
            model_name="user",
            name="role",
        ),
    ]
