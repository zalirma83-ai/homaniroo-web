"""Read-only information checks. CLEAN is not a human approval.

Rules are shared between SQL filters and rendered reasons. Nothing here updates
source flags, business classifications, amounts, dates or unit assumptions.
An unexplained historical flag cannot be silently cleared by fixing another gap.
"""
from dataclasses import dataclass, replace
from functools import reduce
from operator import or_
import re

from django.db.models import Exists, OuterRef, Q


INCOMPLETE = 'INCOMPLETE'
REVIEW = 'REVIEW_REQUIRED'
CLEAR = 'CLEAN'  # keep existing bookmark/query parameter; never trust raw CLEAN
LEGACY_REASON = 'این رکورد هنگام انتقال اطلاعات برای بررسی علامت‌گذاری شده؛ دلیل دقیق در اطلاعات موجود ثبت نشده است.'
CLEAR_EXPLANATION = 'در بررسی‌های خودکار فعلی موردی پیدا نشده است؛ این وضعیت به معنی تأیید انسانی یا تضمین صحت اطلاعات نیست.'


def blank(field):
    return Q(**{field + '__isnull': True}) | Q(**{field + '__regex': r'^\s*$'})


def any_q(conditions):
    return reduce(or_, conditions, Q(pk__in=[]))


def matches(obj, condition):
    """Evaluate the same scalar Q used by SQL; no database access."""
    results = []
    for child in condition.children:
        if isinstance(child, Q):
            results.append(matches(obj, child))
            continue
        key, expected = child
        parts = key.split('__')
        field = parts[0]
        lookup = parts[1] if len(parts) > 1 else 'exact'
        value = getattr(obj, field, None)
        if lookup == 'isnull':
            result = (value is None) == expected
        elif lookup == 'regex':
            result = value is not None and re.search(expected, str(value)) is not None
        elif lookup == 'in':
            result = value in expected
        elif lookup == 'exact':
            result = value == expected
        else:
            raise ValueError('Unsupported scalar information check: ' + key)
        results.append(result)
    result = all(results) if condition.connector == Q.AND else any(results)
    return not result if condition.negated else result


@dataclass(frozen=True)
class Issue:
    code: str
    kind: str
    short: str
    message: str
    impact: str = ''
    location: str = ''


@dataclass(frozen=True)
class Rule:
    condition: Q
    issue: Issue


@dataclass(frozen=True)
class InformationStatus:
    issues: tuple = ()

    @property
    def incomplete(self):
        return any(i.kind == INCOMPLETE for i in self.issues)

    @property
    def review(self):
        return any(i.kind == REVIEW for i in self.issues)

    @property
    def clear(self):
        return not self.issues

    @property
    def label(self):
        return ' و '.join(label for present, label in ((self.incomplete, 'نیازمند تکمیل'), (self.review, 'نیازمند بررسی')) if present) or 'بدون هشدار'

    @property
    def summary(self):
        values = list(dict.fromkeys(i.short for i in self.issues))
        return '؛ '.join(values[:3]) + ('؛ موارد بیشتر در جزئیات' if len(values) > 3 else '')

    @property
    def explanation(self):
        return CLEAR_EXPLANATION


def issue(code, kind, short, message=None, impact=''):
    return Issue(code, kind, short, message or short, impact)


HISTORICAL = issue('unexplained_source_flag', REVIEW, 'دلیل علامت بررسی ثبت نشده', LEGACY_REASON)
SOURCE_FLAG = ~Q(data_quality_status='CLEAN') & ~blank('data_quality_status')
ROOT_RULES = (
    Rule(Q(contract_start_gregorian__isnull=True), issue('start_missing', INCOMPLETE, 'تاریخ شروع قابل استفاده ثبت نشده', impact='بدون تاریخ شروع و پایان قابل استفاده، وضعیت زمانی قرارداد تعیین نمی‌شود.')),
    Rule(Q(contract_end_gregorian__isnull=True), issue('end_missing', INCOMPLETE, 'تاریخ پایان قابل استفاده ثبت نشده', impact='بدون تاریخ شروع و پایان قابل استفاده، وضعیت زمانی قرارداد تعیین نمی‌شود.')),
    Rule(SOURCE_FLAG, HISTORICAL),
)
RATE_FIELDS = ('base_rate', 'corrected_rate', 'monthly_rate', 'rate_1401', 'adjusted_rate_1401', 'adjusted_rate_1402')
HAS_RATE = any_q([Q(**{f + '__isnull': False}) for f in RATE_FIELDS])
LINE_RULES = (
    Rule(Q(contracted_volume__isnull=True), issue('volume_missing', INCOMPLETE, 'حجم ثبت نشده', impact='در جمع حجم‌های ثبت‌شده، مقدار خالی صفر فرض نمی‌شود.')),
    Rule(Q(amount_value__isnull=True), issue('amount_missing', INCOMPLETE, 'مبلغ کل ثبت نشده', 'مبلغ کل این ردیف ثبت نشده است؛ وجود حجم و نرخ جای مبلغ ثبت‌شده را نمی‌گیرد.')),
    Rule(Q(contracted_volume__isnull=False) & (blank('volume_unit') | Q(volume_unit='UNKNOWN')), issue('volume_unit_unknown', REVIEW, 'واحد حجم نامشخص')),
    Rule(HAS_RATE & (blank('rate_unit') | Q(rate_unit='UNKNOWN')), issue('rate_unit_unknown', REVIEW, 'واحد نرخ نامشخص')),
    Rule(Q(amount_value__isnull=False) & (blank('amount_unit') | Q(amount_unit='UNKNOWN')), issue('amount_unit_unknown', REVIEW, 'واحد مبلغ نامشخص')),
    Rule(SOURCE_FLAG, HISTORICAL),
)
MONTH_RULES = (
    Rule(Q(quantity_value__isnull=True), issue('monthly_value_missing', INCOMPLETE, 'مقدار ماهانه ثبت نشده')),
    Rule(blank('period_jalali'), issue('monthly_period_missing', INCOMPLETE, 'دوره مقدار ماهانه ثبت نشده')),
    Rule(Q(quantity_value__isnull=False) & (blank('quantity_unit') | Q(quantity_unit='UNKNOWN')), issue('monthly_unit_unknown', REVIEW, 'واحد مقدار ماهانه نامشخص')),
    Rule(SOURCE_FLAG, HISTORICAL),
)


def assess_rules(obj, rules, location=''):
    return tuple(replace(rule.issue, location=location) for rule in rules if matches(obj, rule.condition))


def assess_line(line, quantities=()):
    problems = list(assess_rules(line, LINE_RULES))
    for quantity in quantities:
        quantity.information = InformationStatus(assess_rules(quantity, MONTH_RULES))
        problems.extend(replace(i, location=f'دوره {quantity.period_jalali or "ثبت‌نشده"}') for i in quantity.information.issues)
    return InformationStatus(tuple(problems))


def assess_contract(contract, line_rows):
    problems = list(assess_rules(contract, ROOT_RULES, 'قرارداد'))
    if not line_rows:
        problems.append(replace(issue('lines_missing', INCOMPLETE, 'ردیف انرژی ثبت نشده'), location='قرارداد'))
    for index, row in enumerate(line_rows, 1):
        quantities = row['monthly_quantities']
        row['information'] = assess_line(row['item'], quantities)
        problems.extend(replace(i, location=f'ردیف انرژی {index}' + (f' / {i.location}' if i.location else '')) for i in row['information'].issues)
    return InformationStatus(tuple(problems))


CHANNEL_FIELDS = ('mobile_number', 'phone_number', 'email', 'website')
HAS_CHANNEL = any_q([~blank(f) for f in CHANNEL_FIELDS])


def assess_customer(customer, contacts=None):
    if contacts is None:
        has_channel = customer.info_has_channel  # annotations: bounded SQL, no per-row queries
        contact_review = customer.info_contact_review
    else:
        contacts = [c for c in contacts if c.tenant_id == customer.tenant_id and c.tenant_customer_id == customer.tenant_customer_id]
        has_channel = any(matches(c, HAS_CHANNEL) for c in contacts)
        contact_review = any(matches(c, SOURCE_FLAG) for c in contacts)
    issues = []
    if not has_channel:
        issues.append(issue('contact_missing', INCOMPLETE, 'راه تماس ثبت نشده', 'هیچ شماره همراه، تلفن، ایمیل یا وب‌سایتی برای این مشترک ثبت نشده است. نام شخص رابط به‌تنهایی راه تماس محسوب نمی‌شود.'))
    if contact_review or matches(customer, SOURCE_FLAG) or matches(customer.industrial_unit, SOURCE_FLAG):
        issues.append(HISTORICAL)
    return InformationStatus(tuple(issues))


def customer_information_queryset(queryset):
    from customers.models import CustomerContact
    contacts = CustomerContact.objects.filter(
        tenant_id=OuterRef('tenant_id'), tenant_customer_id=OuterRef('tenant_customer_id'),
    )
    return queryset.annotate(info_has_channel=Exists(contacts.filter(HAS_CHANNEL)), info_contact_review=Exists(contacts.filter(SOURCE_FLAG))).annotate(
        has_contact=Q(info_has_channel=True),
        info_incomplete=Q(info_has_channel=False),
        info_review=SOURCE_FLAG | Q(info_contact_review=True) | (~Q(industrial_unit__data_quality_status='CLEAN') & ~blank('industrial_unit__data_quality_status')),
    )


def _rules_q(rules, kind):
    return any_q(rule.condition for rule in rules if rule.issue.kind == kind)


def contract_information_queryset(queryset):
    from contracts.models import ContractLine, ContractMonthlyQuantity
    lines = ContractLine.objects.filter(tenant_id=OuterRef('tenant_id'), contract_id=OuterRef('contract_id'))
    months = ContractMonthlyQuantity.objects.filter(
        tenant_id=OuterRef('tenant_id'), contract_line__tenant_id=OuterRef('tenant_id'), contract_line__contract_id=OuterRef('contract_id'),
    )
    return queryset.annotate(
        info_has_lines=Exists(lines),
        info_line_incomplete=Exists(lines.filter(_rules_q(LINE_RULES, INCOMPLETE))),
        info_line_review=Exists(lines.filter(_rules_q(LINE_RULES, REVIEW))),
        info_month_incomplete=Exists(months.filter(_rules_q(MONTH_RULES, INCOMPLETE))),
        info_month_review=Exists(months.filter(_rules_q(MONTH_RULES, REVIEW))),
    ).annotate(
        info_incomplete=_rules_q(ROOT_RULES, INCOMPLETE) | Q(info_has_lines=False) | Q(info_line_incomplete=True) | Q(info_month_incomplete=True),
        info_review=_rules_q(ROOT_RULES, REVIEW) | Q(info_line_review=True) | Q(info_month_review=True),
    )


def filter_information(queryset, value):
    if value in (CLEAR, 'NO_WARNING'):
        return queryset.filter(info_incomplete=False, info_review=False)
    if value == INCOMPLETE:
        return queryset.filter(info_incomplete=True)
    if value == REVIEW:
        return queryset.filter(info_review=True)
    if value == 'ATTENTION':
        return queryset.filter(Q(info_incomplete=True) | Q(info_review=True))
    return queryset


def attach_customer_information(customers):
    for customer in customers:
        customer.information = assess_customer(customer)
    return customers
