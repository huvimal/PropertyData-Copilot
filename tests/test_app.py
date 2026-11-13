import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from streamlit.testing.v1 import AppTest

ROOT = Path(__file__).resolve().parents[1]


class AppTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        env = patch.dict(os.environ, {'PROPERTYDATA_DB': str(Path(self.temp.name) / 'test.sqlite3')})
        env.start()
        self.addCleanup(env.stop)

    def click(self, app, label):
        next(b for b in app.button if b.label == label).click().run()
        self.assertFalse(app.exception)

    def start(self):
        app = AppTest.from_file(str(ROOT / 'app.py'), default_timeout=30).run()
        self.assertFalse(app.exception)
        self.click(app, 'Nạp 16 tin mẫu')
        self.click(app, 'Chuẩn hóa tin pending bằng quy tắc')
        return app

    def approve_current(self, app):
        next(t for t in app.text_input if t.label == 'Tên / bí danh người duyệt').set_value('Huni')
        next(t for t in app.text_input if t.label.startswith('Ghi chú đối chiếu')).set_value('Checked source, units and duplicate candidates')
        next(c for c in app.checkbox if c.label.startswith('Tôi đã lưu sửa đổi')).check()
        self.click(app, 'Duyệt tin đã lưu')

    def test_review_search_and_boundary_confirmation(self):
        app = self.start()
        self.approve_current(app)
        for identifier in ('R002', 'R003'):
            next(s for s in app.selectbox if s.label.startswith('2. Chọn tin')).select(identifier).run()
            self.approve_current(app)
        self.click(app, 'Đề xuất bộ lọc bằng quy tắc')
        self.click(app, 'Xác nhận bộ lọc và tìm')
        self.assertEqual(app.session_state['confirmed_query']['budget_operator'], 'lt')
        from store import Store
        from search import search
        rows = Store(os.environ['PROPERTYDATA_DB']).rows(approved_only=True)
        self.assertEqual({r['id'] for r in search(rows, app.session_state['confirmed_query'])}, {'R001', 'R002'})
        next(s for s in app.selectbox if s.label.startswith('Điều kiện ngân sách')).select('lte')
        self.click(app, 'Xác nhận bộ lọc và tìm')
        self.assertEqual({r['id'] for r in search(rows, app.session_state['confirmed_query'])}, {'R001', 'R002', 'R003'})

    def test_edit_revokes_and_no_api_on_load(self):
        with patch('provider.extract', side_effect=AssertionError('No implicit API')) as fake:
            app = self.start()
            self.approve_current(app)
            self.click(app, 'Lưu sửa đổi — hủy duyệt cũ')
        self.assertEqual(fake.call_count, 0)
        from store import Store
        self.assertEqual(Store(os.environ['PROPERTYDATA_DB']).rows(approved_only=True), [])

    def test_invalid_filter_does_not_keep_previous_results(self):
        app = self.start()
        self.click(app, 'Đề xuất bộ lọc bằng quy tắc')
        self.click(app, 'Xác nhận bộ lọc và tìm')
        purpose = next(s for s in app.selectbox if s.label.startswith('purpose:'))
        purpose.select('')
        self.click(app, 'Xác nhận bộ lọc và tìm')
        self.assertTrue(app.error)
        with self.assertRaises(KeyError):
            _ = app.session_state['confirmed_query']
