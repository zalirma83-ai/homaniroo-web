from django.conf import settings
from django.db import models


class UserProfile(models.Model):
    """اتصال هویت ورود Django به کاربر تجاری موجود در core.users."""

    user = models.OneToOneField(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name="profile",
        verbose_name="کاربر",
    )
    core_user_id = models.PositiveBigIntegerField(
        unique=True,
        null=True,
        blank=True,
        verbose_name="شناسه کاربر تجاری",
    )
    must_change_password = models.BooleanField(
        default=False,
        verbose_name="الزام تغییر رمز",
    )
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        verbose_name = "پروفایل کاربر"
        verbose_name_plural = "پروفایل کاربران"

    def __str__(self):
        return self.user.get_full_name() or self.user.get_username()
