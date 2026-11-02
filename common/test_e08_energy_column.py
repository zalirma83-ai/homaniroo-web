"""E08 presentation-only removal of the energy completeness column."""
from decimal import Decimal
from types import SimpleNamespace as N
from common import test_e04_presentation as e04
from django.test import SimpleTestCase

class E08EnergyColumnTests(SimpleTestCase):
    setUp = e04.E04PresentationTests.setUp
    render_page = e04.E04PresentationTests.render_page

    def render_energy(self, rows):
        return self.render_page('energy/trading_dashboard.html', rows=rows,
            summary={'purchase':10,'sales':12,'incomplete':3,'rows':4},
            filters={'completeness':'INCOMPLETE_BOTH'}, monthly_series=[])

    def table(self, html):
        return next(t for t in e04.Tables(html).tables if t['class']=='energy-balance-table')

    def test_empty_table_has_seven_headers_and_matching_colspan(self):
        html=self.render_energy([])
        table=self.table(html)
        self.assertEqual(len(table['rows'][0]),7)
        self.assertEqual(table['rows'][1][0][1]['colspan'],'7')
        self.assertIn('ردیفی مطابق فیلتر انتخاب‌شده وجود ندارد.',html)

    def test_complete_and_incomplete_rows_keep_all_seven_other_cells(self):
        for completeness in ('COMPLETE','INCOMPLETE_PURCHASE','INCOMPLETE_SALES','INCOMPLETE_BOTH'):
            with self.subTest(completeness=completeness):
                row=N(item=N(period_jalali='1405/08',purchase_volume=Decimal('10'),
                    sales_volume=Decimal('12'),balance_value=Decimal('-2'),volume_unit='MW',
                    data_completeness=completeness),
                    energy_type=N(energy_type_name_fa='برق حرارتی'),
                    load_band=N(load_band_name_fa='بار پایه'),status_class='danger',
                    status_label='کسری',completeness_label='E08_COLUMN_SENTINEL')
                html=self.render_energy([row]);table=self.table(html)
                self.assertEqual([len(r) for r in table['rows']],[7,7])
                self.assertNotIn('E08_COLUMN_SENTINEL',html)
                self.assertIn('برق حرارتی',html);self.assertIn('بار پایه',html)
                self.assertIn('کسری',html);self.assertIn('MW',html)
                self.assertIn('۱۰',html);self.assertIn('۱۲',html)
                self.assertEqual(row.item.balance_value,Decimal('-2'))

    def test_completeness_filter_and_aggregate_metrics_are_preserved(self):
        html=self.render_energy([])
        self.assertIn('name="completeness"',html)
        self.assertIn('value="INCOMPLETE_BOTH" selected',html)
        self.assertIn('ردیف ناقص تراز',html)
        self.assertIn('خرید ناقص',html);self.assertIn('فروش ناقص',html)
        self.assertIn('معاملات خرید',html);self.assertIn('معاملات فروش',html)
        self.assertIn('خرید و فروش ماهانه موجود',html)

    def test_template_change_is_only_column_and_empty_row_alignment(self):
        source=(self.root/'templates/energy/trading_dashboard.html').read_text(encoding='utf-8-sig')
        self.assertNotIn('<th>کامل‌بودن</th>',source)
        self.assertNotIn('row.completeness_label',source)
        self.assertNotIn('row.item.data_completeness',source)
        self.assertIn('name="completeness"',source)
        self.assertIn('row.item.balance_value',source)
        self.assertIn('row.status_label',source)
