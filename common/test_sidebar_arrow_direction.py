"""Keep physical sidebar chevrons from mirroring in the RTL shell."""
from html.parser import HTMLParser
from pathlib import Path
from common import test_e04_presentation as e04
from django.test import SimpleTestCase

class ArrowParser(HTMLParser):
    def __init__(self, html):
        super().__init__();self.button=None;self.arrow=None;self.arrows={};self.feed(html)
    def handle_starttag(self,tag,attrs):
        attrs=dict(attrs)
        if tag=='button':
            self.button=next((k for k in ('data-sidebar-close','data-sidebar-toggle') if k in attrs),None)
        if tag=='span' and self.button:
            self.arrow={'attrs':attrs,'text':''};self.arrows[self.button]=self.arrow
    def handle_data(self,data):
        if self.arrow is not None:self.arrow['text']+=data
    def handle_endtag(self,tag):
        if tag=='span':self.arrow=None
        if tag=='button':self.button=None

class SidebarArrowDirectionTests(SimpleTestCase):
    setUp=e04.E04PresentationTests.setUp
    render_page=e04.E04PresentationTests.render_page

    def test_rendered_open_and_closed_arrows_are_ltr_isolated(self):
        html=self.render_page('energy/trading_dashboard.html')
        arrows=ArrowParser(html).arrows
        for control,glyph in (('data-sidebar-close','›'),('data-sidebar-toggle','‹')):
            with self.subTest(control=control):
                self.assertEqual(arrows[control]['attrs']['dir'],'ltr')
                self.assertEqual(arrows[control]['attrs']['aria-hidden'],'true')
                self.assertEqual(arrows[control]['text'],glyph)
        self.assertIn('data-sidebar-arrow',arrows['data-sidebar-toggle']['attrs'])
        self.assertIn('<html lang="fa" dir="rtl">',html)

    def test_javascript_direction_and_accessibility_contract_remain(self):
        source=(self.root/'static/js/app.js').read_text(encoding='utf-8-sig')
        self.assertIn("arrow.textContent = open ? '›' : '‹'",source)
        self.assertIn("toggle?.setAttribute('aria-expanded', String(open))",source)
        self.assertIn("close?.addEventListener('click', () => setSidebar(false, true))",source)
