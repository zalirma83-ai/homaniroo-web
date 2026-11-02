"""All-records work queue, independent pagers and tenant isolation regressions."""
from datetime import timedelta
from inspect import unwrap
from types import SimpleNamespace
from unittest.mock import patch
from urllib.parse import parse_qs

import pytest
from django.core.paginator import Paginator
from django.http import Http404
from django.template import Context, Template
from django.template.loader import render_to_string
from django.test import RequestFactory, SimpleTestCase
from django.urls import reverse
from django.utils import timezone

from common.templatetags.pagination import numbered_pagination
from .models import FollowUp, Interaction
from .services import build_work_queue, close_follow_up, reschedule_follow_up
from .tests import contact_followup_tables, make_customer, operator


@pytest.fixture
def records(contact_followup_tables):
    first = make_customer(1, "مشتری شرکت اول", "ACTIVE")
    second = make_customer(2, "مشتری شرکت دوم", "LEAD")
    past = timezone.now() - timedelta(days=3)
    followups = {}
    for status in ("OPEN", "COMPLETED", "CANCELLED", "LEGACY_UNKNOWN"):
        followups[status] = FollowUp.objects.create(
            tenant_id=1, tenant_customer_id=first.pk, due_at=past,
            follow_up_status=status, reason="نمونه " + status,
        )
    foreign = FollowUp.objects.create(
        tenant_id=2, tenant_customer_id=second.pk, due_at=past,
        follow_up_status="OPEN", reason="نمونه شرکت دوم",
    )
    return SimpleNamespace(tenant=SimpleNamespace(tenant_id=1), first=first,
                           second=second, followups=followups, foreign=foreign)


def ids(context):
    return {row["item"].pk for row in context["rows"]}


@pytest.mark.django_db
def test_default_and_legacy_all_stay_open_only(records):
    for kwargs in ({}, {"scope": "all"}, {"scope": "invalid"}):
        context = build_work_queue(records.tenant, **kwargs)
        assert context["scope"] == "all"
        assert ids(context) == {records.followups["OPEN"].pk}
        assert context["stats"]["open"] == 1


@pytest.mark.django_db
def test_all_records_status_labels_and_actions_are_safe(records):
    context = build_work_queue(records.tenant, scope="all-records")
    assert ids(context) == {item.pk for item in records.followups.values()}
    assert records.foreign.pk not in ids(context)
    rows = {row["item"].follow_up_status: row for row in context["rows"]}
    assert rows["OPEN"]["due_label"] == "عقب‌افتاده"
    assert rows["OPEN"]["can_act"] and rows["OPEN"]["is_due"]
    for status, label in (("COMPLETED", "انجام‌شده"), ("CANCELLED", "لغوشده"),
                          ("LEGACY_UNKNOWN", "وضعیت نامشخص")):
        assert rows[status]["due_label"] == label
        assert not rows[status]["can_act"] and not rows[status]["is_due"]
    assert context["stats"]["open"] == context["stats"]["overdue"] == 1


@pytest.mark.django_db
def test_date_buckets_never_include_closed_records(records):
    for scope in ("overdue", "today", "upcoming", "without-date"):
        context = build_work_queue(records.tenant, scope=scope)
        assert all(row["item"].follow_up_status == "OPEN" for row in context["rows"])
    FollowUp.objects.filter(pk=records.followups["COMPLETED"].pk).update(due_at=None)
    assert not build_work_queue(records.tenant, scope="without-date")["rows"]


@pytest.mark.django_db
def test_search_and_customer_filter_apply_before_pagination(records):
    context = build_work_queue(records.tenant, scope="all-records", query="COMPLETED", customer_status="ACTIVE")
    assert ids(context) == {records.followups["COMPLETED"].pk}
    assert context["followup_page"].paginator.count == 1
    assert not build_work_queue(records.tenant, scope="all-records", customer_status="LEAD")["rows"]
    assert not build_work_queue(records.tenant, scope="all-records", query="شرکت دوم")["rows"]


@pytest.mark.django_db
def test_more_than_two_hundred_followups_are_reachable(records):
    for number in range(205):
        FollowUp.objects.create(tenant_id=1, tenant_customer_id=records.first.pk,
                                follow_up_status="OPEN", reason=f"تاریخچه {number}")
    expected = set(FollowUp.objects.filter(tenant_id=1).values_list("pk", flat=True))
    first = build_work_queue(records.tenant, scope="all-records", ordering="newest")
    assert first["followup_page"].paginator.count == 209
    assert first["followup_page"].paginator.per_page == 25
    seen = []
    for number in first["followup_page"].paginator.page_range:
        context = build_work_queue(records.tenant, scope="all-records", ordering="newest", page_number=number)
        seen.extend(row["item"].pk for row in context["rows"])
    assert len(seen) == len(set(seen)) == len(expected)
    assert set(seen) == expected
    assert first["stats"]["open"] == 206


@pytest.mark.django_db
def test_history_has_independent_pages_and_no_thirty_record_cap(records):
    for number in range(36):
        Interaction.objects.create(tenant_id=1, tenant_customer_id=records.first.pk,
                                   interaction_type="CALL", interaction_at=timezone.now(),
                                   interaction_status="NO_ANSWER", source_system="WEB", notes=str(number))
    Interaction.objects.create(tenant_id=2, tenant_customer_id=records.second.pk,
                               interaction_type="CALL", interaction_at=timezone.now(), source_system="WEB")
    for number in range(30):
        FollowUp.objects.create(tenant_id=1, tenant_customer_id=records.first.pk,
                                follow_up_status="OPEN", reason=str(number))
    first = build_work_queue(records.tenant, scope="all-records")
    second = build_work_queue(records.tenant, scope="all-records", history_page_number=2, page_number=2)
    assert len(first["recent_rows"]) == 30
    assert len(second["recent_rows"]) == 6
    assert first["history_page"].paginator.count == 36
    assert second["followup_page"].number == 2
    assert not ids(first) & ids(second)
    assert len(second["rows"]) == 9
    old_ids = {row["item"].pk for row in first["recent_rows"]}
    new_ids = {row["item"].pk for row in second["recent_rows"]}
    assert not old_ids & new_ids
    assert len(old_ids | new_ids) == 36
    assert all(row["item"].tenant_id == 1 for row in second["recent_rows"])
    # History deliberately remains company-wide, not filtered by follow-up search.
    filtered = build_work_queue(records.tenant, query="does-not-match", history_page_number=2)
    assert not filtered["rows"] and len(filtered["recent_rows"]) == 6
    assert filtered["interaction_stats"]["total"] == 36


@pytest.mark.django_db
def test_invalid_page_values_and_empty_tenant_are_safe(records):
    for number in ("bad", "999999999", "-1", "0", "1.5"):
        context = build_work_queue(records.tenant, page_number=number, history_page_number=number)
        assert context["followup_page"].number == context["history_page"].number == 1
    context = build_work_queue(SimpleNamespace(tenant_id=9999), scope="all-records")
    assert not context["rows"] and not context["recent_rows"]
    assert context["followup_page"].paginator.count == context["history_page"].paginator.count == 0


@pytest.mark.django_db
def test_backend_guards_still_reject_closed_and_foreign_mutations(records):
    before = Interaction.objects.count()
    for item in (records.followups["COMPLETED"], records.followups["CANCELLED"], records.foreign):
        item.refresh_from_db()  # Compare persisted values before and after rejected actions.
        status_before, due_before, reason_before = item.follow_up_status, item.due_at, item.reason
        with pytest.raises(Http404):
            close_follow_up(tenant=records.tenant, follow_up_id=item.pk, user=operator(), action="DONE")
        with pytest.raises(Http404):
            reschedule_follow_up(tenant=records.tenant, follow_up_id=item.pk, user=operator(), due_at=timezone.now())
        item.refresh_from_db()
        assert (item.follow_up_status, item.due_at, item.reason) == (status_before, due_before, reason_before)
    assert Interaction.objects.count() == before


@pytest.mark.django_db
def test_rendered_closed_rows_have_no_mutation_controls(records):
    context = build_work_queue(records.tenant, scope="all-records")
    request = RequestFactory().get('/interactions/?scope=all-records')
    user = SimpleNamespace(is_authenticated=True, is_superuser=True, is_staff=True,
                           username="operator", get_full_name=lambda: "اپراتور")
    html = render_to_string("interactions/work_queue.html", {**context, "request": request, "user": user})
    for status, item in records.followups.items():
        url = reverse('interactions:follow_up_action', args=[item.pk])
        reschedule = reverse('interactions:follow_up_reschedule', args=[item.pk])
        if status == "OPEN":
            assert url in html and reschedule in html
        else:
            assert url not in html and reschedule not in html
    assert "سوابق شرکت فعال" in html and "۳۰ رکورد آخر" not in html


class CustomPaginationTests(SimpleTestCase):
    def test_both_pagers_preserve_other_pager_and_repeated_filters(self):
        request = RequestFactory().get('/interactions/?scope=all-records&q=a&q=b&order=newest&customer_status=ACTIVE&followup_page=2&history_page=3')
        for own, other in (("followup_page", "history_page"), ("history_page", "followup_page")):
            with self.subTest(pager=own):
                result = numbered_pagination(Context({"request": request}), Paginator(range(250), 25).page(2), page_param=own)
                query = parse_qs(result["next_url"][1:])
                assert query[own] == ["3"] and query[other] == [request.GET[other]]
                assert query["q"] == ["a", "b"] and query["scope"] == ["all-records"]
                assert query["order"] == ["newest"] and query["customer_status"] == ["ACTIVE"]
                assert own not in dict(result["hidden_filters"])
                assert other in dict(result["hidden_filters"])
        assert request.GET['followup_page'] == '2'

    def test_custom_jump_fields_labels_and_escaping(self):
        context = Context({"request": RequestFactory().get('/interactions/?q=%22%3E%3Cscript%3E'),
                           "p": Paginator(range(250), 25).page(2)})
        template = Template('{% load pagination %}{% numbered_pagination p page_param="followup_page" aria_label="صفحه‌بندی پیگیری‌ها" %}{% numbered_pagination p page_param="history_page" aria_label="صفحه‌بندی سوابق ارتباط‌ها" %}')
        html = template.render(context)
        assert html.count('type="number" name="followup_page"') == 1
        assert html.count('type="number" name="history_page"') == 1
        assert 'aria-label="صفحه‌بندی پیگیری‌ها"' in html
        assert 'aria-label="صفحه‌بندی سوابق ارتباط‌ها"' in html
        assert '<script>' not in html and '&lt;script&gt;' in html

    def test_view_passes_independent_page_parameters(self):
        from .views import work_queue
        request = RequestFactory().get('/interactions/?scope=all-records&followup_page=4&history_page=2')
        request.tenant = SimpleNamespace(tenant_id=1)
        with patch('interactions.views.build_work_queue', return_value={}) as build, patch('interactions.views.render'):
            unwrap(work_queue)(request)
        assert build.call_args.kwargs['page_number'] == '4'
        assert build.call_args.kwargs['history_page_number'] == '2'
        assert build.call_args.kwargs['scope'] == 'all-records'
