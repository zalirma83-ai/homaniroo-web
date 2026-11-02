from django.contrib.admin.apps import AdminConfig


class SuperadminAdminConfig(AdminConfig):
    default_site = "accounts.admin_site.SuperadminAdminSite"
