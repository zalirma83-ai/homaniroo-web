from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('customers', '0004_customer_profile_models'),
    ]

    operations = [
        migrations.CreateModel(
            name='EntityBusinessProfile',
            fields=[
                ('profile_id', models.BigAutoField(primary_key=True, serialize=False)),
                ('tenant_id', models.BigIntegerField()),
                ('display_name', models.TextField()),
                ('relationship_type', models.TextField()),
                ('purchase_cycle', models.TextField()),
                ('classification_status', models.TextField()),
                ('review_reason', models.TextField(blank=True, null=True)),
                ('missing_in_initial_database', models.BooleanField(default=False)),
                ('notes', models.TextField(blank=True, null=True)),
                ('sources', models.JSONField(default=list)),
                ('created_at', models.DateTimeField()),
                ('updated_at', models.DateTimeField()),
                ('provided_national_id', models.TextField(blank=True, null=True)),
            ],
            options={
                'verbose_name': 'دسته\u200cبندی تجاری',
                'verbose_name_plural': 'دسته\u200cبندی\u200cهای تجاری',
                'db_table': '"core"."entity_business_profiles"',
                'ordering': ('display_name', 'profile_id'),
                'managed': False,
            },
        ),
    ]
