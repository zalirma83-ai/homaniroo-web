from django.contrib.auth import SESSION_KEY
from django.contrib.sessions.models import Session
from django.utils import timezone


def get_user_sessions(user):
    """نشست‌های معتبر متعلق به یک کاربر را برمی‌گرداند."""
    sessions = Session.objects.filter(expire_date__gte=timezone.now())
    for session in sessions:
        try:
            data = session.get_decoded()
        except Exception:
            continue
        if str(data.get(SESSION_KEY)) == str(user.pk):
            yield session
