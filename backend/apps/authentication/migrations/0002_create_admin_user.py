# Generated Django migration to create initial admin user

import os

from django.contrib.auth.hashers import make_password
from django.db import migrations


def create_admin_user(apps, schema_editor):
    User = apps.get_model('authentication', 'User')
    if not User.objects.filter(username='admin').exists():
        password = os.environ.get('ADMIN_INITIAL_PASSWORD', 'admin123')
        User.objects.create(
            username='admin',
            email='admin@tradeclaw.local',
            is_admin=True,
            is_active=True,
            is_frozen=False,
            password=make_password(password),
        )


def remove_admin_user(apps, schema_editor):
    User = apps.get_model('authentication', 'User')
    User.objects.filter(username='admin').delete()


class Migration(migrations.Migration):
    dependencies = [
        ('authentication', '0001_initial'),
    ]

    operations = [
        migrations.RunPython(create_admin_user, remove_admin_user),
    ]
