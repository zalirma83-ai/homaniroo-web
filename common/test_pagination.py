"""Regression checks for shared numbered pagination; no database required."""
from html.parser import HTMLParser
from urllib.parse import parse_qs
from types import SimpleNamespace

from django.core.paginator import Paginator
from django.template import Context, Template
from django.template.loader import render_to_string
from django.test import RequestFactory, SimpleTestCase

from common.templatetags.pagination import numbered_pagination


class _Markup(HTMLParser):
    def __init__(self, text):
        super().__init__()
        self.elements = []
        self.feed(text)

    def handle_starttag(self, tag, attrs):
        self.elements.append((tag, dict(attrs)))


class NumberedPaginationTests(SimpleTestCase):
    def setUp(self):
        self.factory = RequestFactory()

    def page(self, number=1, total_pages=10):
        return Paginator(range(total_pages * 25), 25).get_page(number)

    def context(self, query='', path='/customers/active/'):
        return Context({'request': self.factory.get(path + ('?' + query if query else ''))})

    def render_component(self, page, query=''):
        context = self.context(query)
        context['page'] = page
        return Template('{% load pagination %}{% numbered_pagination page %}').render(context)

    def test_none_page_is_hidden(self):
        self.assertFalse(numbered_pagination(self.context(), None)['show_pagination'])

    def test_single_page_is_hidden(self):
        self.assertNotIn('<nav', self.render_component(self.page(total_pages=1)))

    def test_empty_results_are_hidden(self):
        self.assertNotIn('<nav', self.render_component(Paginator([], 25).get_page(1)))

    def test_first_page_has_no_previous_links(self):
        result = numbered_pagination(self.context(), self.page())
        self.assertIsNone(result['first_url'])
        self.assertIsNone(result['previous_url'])
        self.assertEqual(parse_qs(result['next_url'][1:])['page'], ['2'])
        self.assertEqual(parse_qs(result['last_url'][1:])['page'], ['10'])

    def test_last_page_has_no_next_links(self):
        result = numbered_pagination(self.context(), self.page(10))
        self.assertIsNone(result['next_url'])
        self.assertIsNone(result['last_url'])
        self.assertEqual(parse_qs(result['previous_url'][1:])['page'], ['9'])
        self.assertEqual(parse_qs(result['first_url'][1:])['page'], ['1'])

    def test_small_page_count_has_all_number_links(self):
        result = numbered_pagination(self.context(), self.page(3, 5))
        self.assertEqual([link['number'] for link in result['page_links']], [1, 2, 3, 4, 5])

    def test_large_page_count_is_compact_but_keeps_first_and_last(self):
        result = numbered_pagination(self.context(), self.page(50, 100))
        numbers = [link['number'] for link in result['page_links'] if not link['is_gap']]
        self.assertEqual(numbers, [1, 48, 49, 50, 51, 52, 100])
        self.assertEqual(sum(link['is_gap'] for link in result['page_links']), 2)

    def test_only_current_page_is_marked(self):
        result = numbered_pagination(self.context(), self.page(4))
        self.assertEqual([link['number'] for link in result['page_links'] if link['is_current']], [4])
        self.assertIn('aria-current="page"', self.render_component(self.page(4)))

    def test_every_link_preserves_all_filters_and_ordering(self):
        query = 'q=%D9%81%D9%88%D9%84%D8%A7%D8%AF&quality=CLEAN&order=name&contract_data=with_volume&page=4'
        result = numbered_pagination(self.context(query), self.page(4))
        urls = [link['url'] for link in result['page_links'] if not link['is_gap']]
        urls += [result[key] for key in ('first_url', 'previous_url', 'next_url', 'last_url')]
        for url in urls:
            params = parse_qs(url[1:])
            self.assertEqual(params['q'], ['فولاد'])
            self.assertEqual(params['quality'], ['CLEAN'])
            self.assertEqual(params['order'], ['name'])
            self.assertEqual(params['contract_data'], ['with_volume'])
            self.assertEqual(len(params['page']), 1)

    def test_repeated_values_and_reserved_characters_are_preserved(self):
        context = self.context('tag=a&tag=b&q=A%26B%2BC%2F%D8%B1&page=4&page=8')
        result = numbered_pagination(context, self.page(4))
        params = parse_qs(result['next_url'][1:])
        self.assertEqual(params['tag'], ['a', 'b'])
        self.assertEqual(params['q'], ['A&B+C/ر'])
        self.assertEqual(params['page'], ['5'])
        self.assertEqual(context['request'].GET.getlist('page'), ['4', '8'])

    def test_jump_form_preserves_filters_but_not_old_page_values(self):
        result = numbered_pagination(self.context('q=test&tag=a&tag=b&page=2&page=9'), self.page(2))
        self.assertEqual(result['hidden_filters'], [('q', 'test'), ('tag', 'a'), ('tag', 'b')])
        self.assertEqual(result['pagination_path'], '/customers/active/')

    def test_jump_input_has_valid_bounds_and_get_method(self):
        markup = _Markup(self.render_component(self.page(3, 10), 'quality=CLEAN'))
        forms = [attrs for tag, attrs in markup.elements if tag == 'form']
        self.assertEqual(forms[0]['method'], 'get')
        self.assertEqual(forms[0]['action'], '/customers/active/')
        inputs = [attrs for tag, attrs in markup.elements if tag == 'input' and attrs.get('name') == 'page']
        self.assertEqual(len(inputs), 1)
        self.assertEqual(inputs[0]['type'], 'number')
        self.assertEqual((inputs[0]['min'], inputs[0]['max'], inputs[0]['value']), ('1', '10', '3'))

    def test_filter_text_is_escaped_in_hidden_inputs(self):
        html = self.render_component(self.page(), 'q=%22%3E%3Cscript%3Ealert(1)%3C%2Fscript%3E')
        self.assertNotIn('<script>', html)
        self.assertIn('&lt;script&gt;', html)

    def test_links_are_local_query_only(self):
        result = numbered_pagination(self.context('next=https%3A%2F%2Fexample.invalid'), self.page(2))
        self.assertTrue(all(link['url'].startswith('?') for link in result['page_links'] if not link['is_gap']))

    def test_missing_request_has_safe_page_only_links(self):
        result = numbered_pagination(Context({}), self.page(2))
        self.assertEqual(result['previous_url'], '?page=1')
        self.assertEqual(result['hidden_filters'], [])
        self.assertEqual(result['pagination_path'], '')

    def test_page_labels_use_persian_digits(self):
        html = self.render_component(self.page(3, 10))
        self.assertIn('صفحه ۳ از ۱۰', html)
        self.assertIn('aria-label="صفحه ۴"', html)

    def test_invalid_page_input_retains_existing_paginator_behavior(self):
        self.assertEqual(self.page('invalid').number, 1)
        self.assertEqual(self.page(999).number, 10)
        self.assertEqual(self.page(-1).number, 10)

    def test_all_six_existing_list_templates_use_shared_component(self):
        page = self.page(3)
        page.object_list = []
        user = SimpleNamespace(
            is_authenticated=True, is_superuser=False, is_staff=False,
            username='pagination_test', get_full_name=lambda: 'اپراتور',
        )
        context = {
            'request': self.context()['request'], 'user': user, 'page': page,
            'stats': {}, 'filters': {}, 'rows': [],
        }
        for name in (
            'customers/active_list.html', 'customers/prospective_list.html',
            'customers/dormant_list.html', 'contracts/contract_list.html',
            'interactions/alert_center.html', 'auditlog/audit_center.html',
        ):
            with self.subTest(template=name):
                html = render_to_string(name, context)
                self.assertEqual(html.count('class="pagination pagination-numbered"'), 1)
                self.assertIn('aria-current="page"', html)
