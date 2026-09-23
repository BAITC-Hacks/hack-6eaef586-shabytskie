"""UI smoke checks and real local worker integration (no external services)."""
from io import BytesIO
import os
from pathlib import Path
import time
import unittest
from unittest.mock import patch

import pandas as pd
from streamlit.testing.v1 import AppTest

from src.security import SecurityError
from src.web_auth import Principal
from src.web_service import WebService

APP = str(Path(__file__).resolve().parents[1] / 'app.py')


class Upload(BytesIO):
    def __init__(self, data, name='sales.csv', reported_size=None):
        super().__init__(data)
        self.name = name
        self.type = 'text/csv'
        self.size = len(data) if reported_size is None else reported_size


class AppSecurityTests(unittest.TestCase):
    def test_demo_has_no_real_upload(self):
        with patch.dict(os.environ, APP_MODE='demo'):
            app = AppTest.from_file(APP, default_timeout=30).run()
        self.assertFalse(app.exception)
        self.assertFalse(app.get('file_uploader'))
        self.assertEqual(app.radio(key='source').options, ['Демо-данные'])

    def test_production_fails_closed_without_configuration(self):
        with patch.dict(os.environ, APP_MODE='production'):
            app = AppTest.from_file(APP, default_timeout=30)
            app.secrets['auth'] = {}
            app.secrets['access'] = {}
            app.run()
        self.assertFalse(app.exception)
        self.assertTrue(app.error)
        self.assertFalse(app.get('file_uploader'))
        self.assertFalse(app.button)

    def test_unknown_mode_fails_closed(self):
        with patch.dict(os.environ, APP_MODE='anything-else'):
            app = AppTest.from_file(APP, default_timeout=30).run()
        self.assertTrue(app.error)
        self.assertFalse(app.get('file_uploader'))
        self.assertFalse(app.button)

    def test_demo_end_to_end_and_approval(self):
        with patch.dict(os.environ, APP_MODE='demo'):
            app = AppTest.from_file(APP, default_timeout=30).run()
            app.button(key='demo_start').click().run(timeout=120)
            self.assertFalse(app.exception)
            self.assertFalse(app.error)
            self.assertEqual(app.metric[0].value, '14')
            app.button(key='approve').click().run()
            self.assertFalse(app.exception)
            self.assertFalse(app.error)
            self.assertTrue(app.success)

    def test_real_upload_worker_and_role_enforcement(self):
        data = pd.DataFrame({'date': pd.date_range('2025-01-01', periods=40),
                             'sku': '001', 'quantity': 10, 'stock': 0,
                             'supplier': 'Supplier', 'phone': 'private-number',
                             'client_id': 'private-client'})
        service = WebService()
        analyst = Principal('alice', 'analyst', time.time() + 600)
        manager = Principal('alice', 'manager', time.time() + 600)
        try:
            token = service.calculate(analyst, 'session', demo=False,
                                      uploads={'sales': Upload(data.to_csv(index=False).encode())})
            item = service.resolve(token, analyst, 'session')
            orders, daily, future = service.load_results(token, analyst, 'session')
            self.assertEqual(orders.sku.iloc[0], '001')
            self.assertFalse(daily.empty)
            self.assertFalse(future.empty)
            self.assertTrue(service.download(token, analyst, 'session').startswith(b'PK'))
            self.assertFalse(list(item.root.glob('*.csv')))
            self.assertFalse((item.root / 'request.json').exists())
            self.assertFalse(list(item.root.rglob('*.joblib')))
            for path in item.root.rglob('*.csv'):
                self.assertNotIn('private-number', path.read_text())
                self.assertNotIn('private-client', path.read_text())
            edits = orders[['sku', 'approved_qty']]
            with self.assertRaises(SecurityError) as error:
                service.approve(token, analyst, 'session', 'Supplier', edits)
            self.assertEqual(error.exception.status, 403)
            stem, xlsx, csv = service.approve(token, manager, 'session', 'Supplier', edits)
            self.assertTrue(stem.startswith('order_'))
            self.assertTrue(xlsx.startswith(b'PK'))
            self.assertIn('alice', csv.decode('utf-8-sig'))
        finally:
            service.close()

    def test_rejected_upload_cleans_workspace(self):
        service = WebService()
        principal = Principal('alice', 'analyst', time.time() + 600)
        try:
            with self.assertRaises(SecurityError):
                service.calculate(principal, 'session', demo=False,
                                  uploads={'sales': Upload(b'invalid', '../../escape.csv')})
            self.assertFalse(service.workspaces)
        finally:
            service.close()

    def test_forged_reported_sizes_do_not_bypass_total_cap(self):
        import tempfile
        service = WebService()
        with tempfile.TemporaryDirectory() as directory, patch('src.web_service.MAX_BATCH_BYTES', 8):
            with self.assertRaises(SecurityError) as error:
                service._save_uploads({'sales': Upload(b'a\n123', reported_size=1),
                                       'stock': Upload(b'a\n456', reported_size=1)}, Path(directory))
        self.assertEqual(error.exception.code, 'batch_size')


if __name__ == '__main__':
    unittest.main()
