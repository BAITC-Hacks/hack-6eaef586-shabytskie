import tempfile
import unittest
from pathlib import Path
import numpy as np
import pandas as pd
from src.anomaly_detection import detect_client_orders, detect_daily_outliers
from src.config import Config
from src.data_loader import load_data, map_columns
from src.evaluation import evaluate, metrics
from src.feature_engineering import engineer_features
from src.forecasting import predict_future
from src.pipeline import prepare, run_pipeline
from src.preprocessing import clean_transactions
from src.stockout import correct_stockouts


def fixture(days: int = 70) -> pd.DataFrame:
    return pd.DataFrame({'sku': '001', 'date': pd.date_range('2025-01-01', periods=days),
                         'quantity': 10., 'client_id': 'A', 'stock': 100.})


class DemandTests(unittest.TestCase):
    def test_iqr(self):
        frame = fixture()
        frame.loc[40, 'quantity'] = 1000
        _, daily = prepare(frame.drop(columns='client_id'))
        self.assertTrue(daily.loc[40, 'is_outlier'])
        self.assertEqual(daily.loc[40, 'quantity_clean'], 10)
        self.assertEqual(daily.loc[40, 'quantity'], 1000)

    def test_client_total_and_same_day_independence(self):
        frame = fixture()
        frame.loc[40, 'quantity'] = 400
        extra = frame.loc[[40]].copy()
        extra['quantity'] = 600
        result = detect_client_orders(clean_transactions(pd.concat([frame, extra], ignore_index=True)))
        bulk = result[result.date == frame.loc[40, 'date']]
        self.assertTrue(bulk.is_large_client_order.all())
        self.assertEqual(bulk.large_client_order_count.sum(), 1)
        self.assertAlmostEqual(bulk.quantity_clean.sum(), 10.)

    def test_stockout_and_partial_sales(self):
        frame = fixture()
        frame.loc[30:32, 'stock'] = 0
        frame.loc[30:31, 'quantity'] = 0
        frame.loc[32, 'quantity'] = 4
        _, daily = prepare(frame)
        np.testing.assert_array_equal(daily.loc[30:32, 'adjusted_demand'], [10, 10, 10])
        np.testing.assert_array_equal(daily.loc[30:32, 'estimated_lost_demand'], [10, 10, 6])

    def test_prefix_invariance(self):
        frame = fixture()
        _, before = prepare(frame)
        frame.loc[50:, 'quantity'] = 10000
        _, after = prepare(frame)
        pd.testing.assert_frame_equal(before.iloc[:50], after.iloc[:50])
        features_before = engineer_features(before)
        features_after = engineer_features(after)
        cols = ['lag_1', 'lag_7', 'rolling_mean_7', 'rolling_std_30', 'growth_rate']
        pd.testing.assert_frame_equal(features_before.loc[:50, cols], features_after.loc[:50, cols])

    def test_rolling_values(self):
        _, daily = prepare(fixture())
        daily['adjusted_demand'] = np.arange(len(daily), dtype=float)
        features = engineer_features(daily)
        self.assertEqual(features.loc[30, 'rolling_mean_7'], np.mean(np.arange(23, 30)))
        self.assertEqual(features.loc[30, 'lag_1'], 29)

    def test_input_formats_and_required_fields(self):
        with self.assertRaisesRegex(ValueError, 'quantity'):
            map_columns(pd.DataFrame({'Дата': [], 'Артикул': []}))
        with tempfile.TemporaryDirectory() as directory:
            frame = fixture(5).rename(columns={'sku': 'Артикул', 'date': 'Дата', 'quantity': 'Количество'})
            for suffix in ['csv', 'xlsx']:
                path = Path(directory) / f'sales.{suffix}'
                frame.to_csv(path, index=False) if suffix == 'csv' else frame.to_excel(path, index=False)
                loaded = load_data(path)
                self.assertEqual(loaded.sku.iloc[0], '001')
                self.assertEqual(len(clean_transactions(loaded)), 5)

    def test_warehouse_stock_is_not_double_counted(self):
        frame = fixture(4)
        frame['warehouse'] = 'WH1'
        more = frame.copy()
        more['client_id'] = 'B'
        _, daily = prepare(pd.concat([frame, more]))
        self.assertTrue(daily.stock.eq(100).all())
        self.assertTrue(daily.quantity.eq(20).all())

    def test_output_schema_short_history(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            path = root / 'sales.csv'
            fixture(5).to_csv(path, index=False)
            run_pipeline(path, Config(forecast_days=7, output_dir=root/'out', processed_dir=root/'processed', model_dir=root/'models'))
            output = pd.read_csv(root / 'out' / 'forecast_output.csv', dtype={'sku': str})
            required = {'sku', 'product_name', 'forecast_demand', 'forecast_horizon_days', 'growth_rate',
                        'trend_direction', 'seasonality_detected', 'estimated_lost_demand',
                        'outliers_detected', 'large_client_orders_detected', 'model_used',
                        'baseline_mae', 'model_mae', 'forecast_confidence', 'forecast_reason'}
            self.assertTrue(required <= set(output))
            self.assertEqual(output.forecast_demand.iloc[0], 70.)
            self.assertEqual(output.model_used.iloc[0], 'fallback_7d')

    def test_chronological_evaluation(self):
        _, daily = prepare(fixture())
        predictions, report = evaluate(daily, Config())
        self.assertTrue((predictions.date > predictions.train_end).all())
        self.assertEqual(predictions.date.nunique(), 14)
        self.assertEqual(report['baseline']['mae'], 0.)

    def test_zero_mape(self):
        self.assertIsNone(metrics(pd.Series([0, 0]), pd.Series([1, 2]))['mape'])

    def test_expanding_cv(self):
        _, daily = prepare(fixture())
        predictions, report = evaluate(daily, Config(), cv_splits=2)
        self.assertEqual(report['folds'], 2)
        self.assertTrue((predictions.date > predictions.train_end).all())

    def test_thousand_skus_and_sixty_day_horizon(self):
        daily = pd.DataFrame({'sku': [f'S{i}' for i in range(1000)],
                              'date': pd.Timestamp('2025-01-01'), 'adjusted_demand': 2.})
        future = predict_future(daily, 60, None, Config(forecast_days=60))
        self.assertEqual(len(future), 60000)
        self.assertTrue(future.groupby('sku').prediction.sum().eq(120.).all())

    def test_missing_dates_do_not_create_stockouts(self):
        frame = fixture(10).drop(index=[5, 6])
        frame.loc[4, 'stock'] = 0
        _, daily = prepare(frame)
        self.assertFalse(daily.loc[5, 'stockout_flag'])
        self.assertEqual(daily.loc[5, 'quantity'], 0)


if __name__ == '__main__':
    unittest.main()
