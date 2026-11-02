# راهنمای انتشار سامانه هما نیرو

## پیش‌نیازها
- Python سازگار با نسخه پروژه
- PostgreSQL 17 یا نسخه سازگار
- وب‌سرور Production و Reverse Proxy دارای HTTPS
- دسترسی امن به متغیرهای محیطی و مسیر نسخه پشتیبان

## آماده‌سازی محیط
1. فایل `.env.example` را به `.env` کپی کنید و تمام مقادیر نمونه را تغییر دهید.
2. `DJANGO_DEBUG=False` و دامنه واقعی را در `DJANGO_ALLOWED_HOSTS` قرار دهید.
3. برای محیط HTTPS گزینه‌های Secure Cookie، SSL Redirect و HSTS را فعال کنید.
4. اگر SSL در Reverse Proxy خاتمه می‌یابد، فقط پس از تنظیم صحیح Proxy مقدار `DJANGO_TRUST_PROXY_SSL_HEADER=True` شود.
5. فایل `.env`، نسخه پشتیبان دیتابیس و فایل‌های Log نباید وارد Git شوند.

## نصب و کنترل نسخه
```cmd
python -m pip install -r requirements.txt
python -m django check --deploy --settings=config.settings
python -m django migrate --settings=config.settings
python -m django collectstatic --noinput --settings=config.settings
python -m pytest -q
```

## نسخه پشتیبان PostgreSQL
قبل از Migration یا انتشار نسخه جدید از دیتابیس پشتیبان بگیرید:
```cmd
pg_dump -Fc -h 127.0.0.1 -U postgres -d homaniroo_dev -f homaniroo_backup.dump
```
بازیابی باید ابتدا در یک دیتابیس آزمایشی تمرین و کنترل شود:
```cmd
pg_restore --clean --if-exists -h 127.0.0.1 -U postgres -d homaniroo_restore_test homaniroo_backup.dump
```

## کنترل سلامت
پس از انتشار، مسیر زیر باید پاسخ HTTP 200 با وضعیت `ok` برگرداند:
```text
/health/
```
این مسیر فقط وضعیت دیتابیس و Migrationها را نمایش می‌دهد و هیچ اطلاعات محرمانه‌ای منتشر نمی‌کند.

## نگهداری دوره‌ای
```cmd
python -m django clearsessions --settings=config.settings
```
- نشست‌های منقضی‌شده به‌صورت دوره‌ای پاک شوند.
- اندازه و دوره نگهداری `core.audit_logs` پایش شود.
- Logها Rotate و آرشیو شوند.
- نسخه پشتیبان روزانه و آزمون بازیابی دوره‌ای انجام شود.

## نکات مهم
- از `runserver` در Production استفاده نکنید.
- فعال‌سازی HSTS فقط پس از اطمینان کامل از HTTPS انجام شود.
- Secret Key و رمز دیتابیس هرگز در Git یا گزارش‌ها قرار نگیرند.
- قبل از هر انتشار، Migration، تست‌ها، Static Files و مسیر `/health/` بررسی شوند.
