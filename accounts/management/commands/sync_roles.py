from django.contrib.auth.models import Group
from django.core.management.base import BaseCommand
from django.db import connection, transaction


class Command(BaseCommand):
    help = "نقش‌های core.roles را با گروه‌های ورود Django همگام می‌کند."

    @transaction.atomic
    def handle(self, *args, **options):
        with connection.cursor() as cursor:
            cursor.execute(
                """
                SELECT role_code, role_name_fa
                FROM core.roles
                ORDER BY role_id
                """
            )
            roles = cursor.fetchall()

        for role_code, role_name_fa in roles:
            _, created = Group.objects.get_or_create(name=role_code)
            action = "ایجاد شد" if created else "از قبل موجود بود"
            self.stdout.write(f"{role_code} - {role_name_fa}: {action}")

        self.stdout.write(self.style.SUCCESS("همگام‌سازی نقش‌ها کامل شد."))
