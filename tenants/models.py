from django.db import models


class Tenant(models.Model):
    tenant_id = models.BigAutoField(primary_key=True)
    tenant_code = models.TextField(unique=True, verbose_name="کد شرکت")
    legal_name = models.TextField(verbose_name="نام قانونی")
    display_name = models.TextField(verbose_name="نام نمایشی")
    status = models.TextField(default="ACTIVE", verbose_name="وضعیت")
    created_at = models.DateTimeField(auto_now_add=True, verbose_name="زمان ایجاد")
    updated_at = models.DateTimeField(auto_now=True, verbose_name="زمان به‌روزرسانی")

    class Meta:
        managed = False
        db_table = '"core"."tenants"'
        verbose_name = "شرکت"
        verbose_name_plural = "شرکت‌ها"
        ordering = ("tenant_id",)

    def __str__(self):
        return self.display_name

    @property
    def is_active(self):
        return self.status == "ACTIVE"


class CoreUser(models.Model):
    user_id = models.BigAutoField(primary_key=True)
    full_name = models.TextField(verbose_name="نام کامل")
    email = models.TextField(null=True, blank=True, verbose_name="ایمیل")
    username = models.TextField(null=True, blank=True, verbose_name="نام کاربری")
    account_status = models.TextField(default="PENDING_SETUP", verbose_name="وضعیت حساب")
    created_at = models.DateTimeField(auto_now_add=True, verbose_name="زمان ایجاد")
    updated_at = models.DateTimeField(auto_now=True, verbose_name="زمان به‌روزرسانی")

    class Meta:
        managed = False
        db_table = '"core"."users"'
        verbose_name = "کاربر تجاری"
        verbose_name_plural = "کاربران تجاری"
        ordering = ("user_id",)

    def __str__(self):
        return self.full_name


class Role(models.Model):
    role_id = models.BigAutoField(primary_key=True)
    role_code = models.TextField(unique=True, verbose_name="کد نقش")
    role_name_fa = models.TextField(verbose_name="نام فارسی نقش")
    description = models.TextField(null=True, blank=True, verbose_name="توضیحات")
    is_system_role = models.BooleanField(default=True, verbose_name="نقش سیستمی")

    class Meta:
        managed = False
        db_table = '"core"."roles"'
        verbose_name = "نقش تجاری"
        verbose_name_plural = "نقش‌های تجاری"
        ordering = ("role_id",)

    def __str__(self):
        return self.role_name_fa


class TenantMembership(models.Model):
    tenant_user_id = models.BigAutoField(primary_key=True)
    tenant = models.ForeignKey(
        Tenant,
        on_delete=models.DO_NOTHING,
        db_column="tenant_id",
        related_name="memberships",
        verbose_name="شرکت",
    )
    core_user = models.ForeignKey(
        CoreUser,
        on_delete=models.DO_NOTHING,
        db_column="user_id",
        related_name="tenant_memberships",
        verbose_name="کاربر",
    )
    is_active = models.BooleanField(default=True, verbose_name="فعال")
    joined_at = models.DateTimeField(auto_now_add=True, verbose_name="زمان عضویت")

    class Meta:
        managed = False
        db_table = '"core"."tenant_users"'
        verbose_name = "عضویت کاربر در شرکت"
        verbose_name_plural = "عضویت کاربران در شرکت‌ها"
        ordering = ("tenant_user_id",)

    def __str__(self):
        return f"{self.core_user} - {self.tenant}"


class UserRoleAssignment(models.Model):
    user_role_assignment_id = models.BigAutoField(primary_key=True)
    core_user = models.ForeignKey(
        CoreUser,
        on_delete=models.DO_NOTHING,
        db_column="user_id",
        related_name="role_assignments",
        verbose_name="کاربر",
    )
    role = models.ForeignKey(
        Role,
        on_delete=models.DO_NOTHING,
        db_column="role_id",
        related_name="assignments",
        verbose_name="نقش",
    )
    tenant = models.ForeignKey(
        Tenant,
        on_delete=models.DO_NOTHING,
        db_column="tenant_id",
        related_name="role_assignments",
        null=True,
        blank=True,
        verbose_name="شرکت",
    )
    assigned_at = models.DateTimeField(auto_now_add=True, verbose_name="زمان تخصیص")

    class Meta:
        managed = False
        db_table = '"core"."user_role_assignments"'
        verbose_name = "تخصیص نقش"
        verbose_name_plural = "تخصیص نقش‌ها"
        ordering = ("user_role_assignment_id",)

    def __str__(self):
        scope = self.tenant.display_name if self.tenant_id else "کل سامانه"
        return f"{self.core_user} - {self.role} - {scope}"
