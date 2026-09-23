import tempfile
import unittest
from pathlib import Path
import numpy as np
import pandas as pd
from src.config import Config
from src.forecasting import predict_future, train_model
from src.ordering import (annual_factor, current_inventory, parse_fraction, recommend_orders,
                          supplier_summary, validate_approved_orders)
from src.pipeline import prepare, run_pipeline

CONFIG = Config()


def sales(days: int = 70, qty: float = 10., start: str = '2025-01-01', **extra) -> pd.DataFrame:
    frame = pd.DataFrame({'sku': 'A1', 'date': pd.date_range(start, periods=days), 'quantity': qty,
                          'client_id': 'C1', 'stock': 100., 'in_transit': 0., 'supplier': 'S1',
                          'lead_time_days': 14., 'category': 'Кабель'})
    for key, value in extra.items():
        frame[key] = value
    return frame


def order(frame: pd.DataFrame, config: Config = CONFIG, stockouts=None, **refs) -> pd.Series:
    _, daily = prepare(frame, stockouts)
    future = predict_future(daily, 60, None, config)
    return recommend_orders(daily, future, config, **refs).iloc[0]


class MustHave1AllSourcesAffectResult(unittest.TestCase):

    def setUp(self):
        # 10 ед./день × (14 дн. поставки + 7 дн. пересмотра) − 100 на складе; спрос ровный, а  страховой запас 0.
        self.base = order(sales())
        self.assertEqual(self.base.recommended_qty, 110)

    def test_in_transit(self):
        self.assertEqual(order(sales(in_transit=50.)).recommended_qty, 60)

    def test_current_stock(self):
        self.assertEqual(order(sales(stock=150.)).recommended_qty, 60)

    def test_stock_table_overrides_sales_snapshot(self):
        stock = pd.DataFrame({'sku': ['A1', 'A1'], 'warehouse': ['W1', 'W2'], 'stock': ['20', '30']})
        self.assertEqual(order(sales(), stock=stock).recommended_qty, 160)

    def test_duplicate_warehouse_snapshot_is_not_double_counted(self):
        stock = pd.DataFrame({'sku': ['A1', 'A1'], 'warehouse': ['W1', 'W1'],
                              'stock': ['20', '20']})
        _, daily = prepare(sales())
        inventory = current_inventory(daily, stock).iloc[0]
        self.assertEqual(inventory.stock, 20)

    def test_latest_dated_warehouse_snapshot_wins(self):
        stock = pd.DataFrame({'sku': ['A1', 'A1'], 'warehouse': ['W1', 'W1'],
                              'date': ['2025-01-02', '2025-01-01'], 'stock': ['20', '90']})
        _, daily = prepare(sales())
        self.assertEqual(current_inventory(daily, stock).iloc[0].stock, 20)

    def test_sales_history(self):
        self.assertEqual(order(sales(qty=20.)).recommended_qty, 320)

    def test_lead_time_from_supplier_directory(self):
        suppliers = pd.DataFrame({'supplier': ['S1'], 'lead_time_days': ['28']})
        self.assertEqual(order(sales(), suppliers=suppliers).recommended_qty, 250)

    def test_category_growth_forecast(self):
        growth = pd.DataFrame({'category': ['Кабель'], 'growth_forecast': ['20%']})
        self.assertEqual(order(sales(), growth=growth).recommended_qty, 152)
        other = order(sales(category='Щиты'), growth=growth)
        self.assertEqual(other.recommended_qty, 110)

    def test_minimum_order_and_multiple(self):
        catalog = pd.DataFrame({'sku': ['A1'], 'order_multiple': ['25'], 'min_order_qty': ['0']})
        self.assertEqual(order(sales(), catalog=catalog).recommended_qty, 125)
        catalog = pd.DataFrame({'sku': ['A1'], 'min_order_qty': ['500']})
        self.assertEqual(order(sales(), catalog=catalog).recommended_qty, 500)

    def test_enough_stock_means_no_order(self):
        result = order(sales(stock=1000.))
        self.assertEqual(result.recommended_qty, 0)
        self.assertEqual(result.urgency, 'не требуется')

    def test_parse_fraction(self):
        self.assertEqual([parse_fraction(v) for v in ['10%', '0,1', '0.1', '10']], [.1, .1, .1, .1])

    def test_invalid_growth_is_rejected(self):
        growth = pd.DataFrame({'sku': ['A1'], 'growth_forecast': ['-150%']})
        with self.assertRaisesRegex(ValueError, 'growth_forecast'):
            order(sales(), growth=growth)

    def test_missing_stock_blocks_recommendation(self):
        result = order(sales().drop(columns='stock'))
        self.assertTrue(pd.isna(result.recommended_qty))
        self.assertEqual(result.urgency, 'требуются данные')
        self.assertIn('заблокирован', result.status)
        self.assertIn('остаток неизвестен', result.reason)


class MustHave2SeasonalityAndGrowth(unittest.TestCase):

    def test_annual_peak_raises_order_above_history_mean(self):
        dates = pd.date_range('2024-01-01', '2025-05-25')
        qty = np.where(dates.month.isin([6, 7, 8]), 30., 10.)
        frame = sales(len(dates), start='2024-01-01', stock=0.)
        frame['quantity'] = qty
        result = order(frame)
        history_mean_plan = qty.mean() * result.coverage_days
        self.assertGreater(result.seasonal_factor, 1.5)
        self.assertGreater(result.demand_for_coverage, 1.4 * history_mean_plan)
        self.assertIn('годовая сезонность', result.reason)

    def test_no_annual_factor_without_a_year_of_history(self):
        _, daily = prepare(sales())
        self.assertIsNone(annual_factor(daily, daily.date.max() + pd.Timedelta(days=1), 21))

    def test_random_forest_does_not_apply_second_annual_factor(self):
        dates = pd.date_range('2024-01-01', '2025-05-25')
        frame = sales(len(dates), start='2024-01-01', stock=0.)
        frame['quantity'] = np.where(dates.month.isin([6, 7, 8]), 30., 10.)
        _, daily = prepare(frame)
        future = predict_future(daily, 60, None, CONFIG)
        future['model_used'] = 'random_forest'
        result = recommend_orders(daily, future, CONFIG).iloc[0]
        self.assertIsNone(result.seasonal_factor)

    def test_weekly_pattern_in_model_forecast(self):
        dates = pd.date_range('2025-01-06', periods=140)
        frame = sales(140, start='2025-01-06')
        frame['quantity'] = np.where(dates.dayofweek >= 5, 30., 5.)
        _, daily = prepare(frame)
        model = train_model(daily, CONFIG)
        future = predict_future(daily, 14, model, CONFIG)
        weekend = future[future.date.dt.dayofweek >= 5].prediction.mean()
        weekday = future[future.date.dt.dayofweek < 5].prediction.mean()
        self.assertGreater(weekend, 3 * weekday)
        self.assertGreater(future.prediction.std(), 5)

    def test_sustained_growth_is_extrapolated(self):
        frame = sales(120)
        frame['quantity'] = np.linspace(10, 30, 120).round()
        result = order(frame, stock=None)
        self.assertGreater(result.trend_factor, 1.05)
        self.assertGreater(result.demand_for_coverage, result.model_demand)
        self.assertIn('устойчивый тренд', result.reason)

    def test_noise_is_not_a_trend(self):
        frame = sales(120)
        frame['quantity'] = np.tile([8., 12.], 60)
        self.assertEqual(order(frame).trend_factor, 1.)


class MustHave3LostDemand(unittest.TestCase):

    def stockout_frame(self):
        frame = sales()
        frame.loc[63:, ['quantity', 'stock']] = 0.
        return frame

    def test_need_higher_than_raw_sales_calculation(self):
        frame = self.stockout_frame()
        corrected = order(frame)
        _, daily = prepare(frame)
        daily['adjusted_demand'] = daily.quantity
        raw = recommend_orders(daily, predict_future(daily, 60, None, CONFIG), CONFIG).iloc[0]
        self.assertGreater(corrected.recommended_qty, raw.recommended_qty)
        self.assertGreater(corrected.recommended_qty, corrected.raw_sales_need)
        self.assertEqual(corrected.recommended_qty, 210)
        self.assertEqual(corrected.urgency, 'критично')
        self.assertIn('упущенный спрос', corrected.reason)

    def test_stockout_periods_table(self):
        frame = sales()
        frame.loc[63:, 'quantity'] = 0.
        frame.loc[63:, 'stock'] = np.nan
        periods = pd.DataFrame({'sku': ['A1'], 'date_from': [str(frame.date[63].date())], 'date_to': [None]})
        without_table = order(frame)
        with_table = order(frame, stockouts=periods)
        self.assertGreater(with_table.recommended_qty, without_table.recommended_qty)
        self.assertEqual(with_table.stock, 0)


class MustHave4OneOffOrders(unittest.TestCase):

    def two_clients(self):
        frame = sales(qty=5.)
        other = frame.copy()
        other['client_id'] = 'C2'
        other['stock'] = np.nan
        return pd.concat([frame, other], ignore_index=True)

    def test_injected_bulk_order_does_not_inflate_regular_need(self):
        frame = self.two_clients()
        clean = order(frame)
        bulk = frame.copy()
        bulk.loc[66, 'quantity'] = 1000.
        result = order(bulk)
        self.assertLessEqual(abs(result.recommended_qty - clean.recommended_qty), .05 * clean.recommended_qty)
        transactions, daily = prepare(bulk)
        flagged = transactions[transactions.is_large_client_order]
        self.assertEqual(list(flagged.client_id), ['C1'])
        self.assertEqual(flagged.quantity_clean.iloc[0], 5.)
        self.assertEqual(daily.large_client_orders_detected.sum(), 1)
        self.assertGreater(result.raw_sales_need, 3 * result.recommended_qty)
        self.assertIn('разовые крупные заказы', result.reason)

    def test_bulk_order_without_client_id_is_capped_as_outlier(self):
        frame = sales().drop(columns='client_id')
        clean = order(frame)
        frame.loc[66, 'quantity'] = 1000.
        self.assertEqual(order(frame).recommended_qty, clean.recommended_qty)


class MustHave5SupplierListWithReasons(unittest.TestCase):

    def test_manual_approval_validates_minimum_and_multiple(self):
        base = pd.DataFrame({'sku': ['A1'], 'approved_qty': [25.],
                             'min_order_qty': [20.], 'order_multiple': [5.]})
        self.assertEqual(validate_approved_orders(base).approved_qty.iloc[0], 25.)
        with self.assertRaisesRegex(ValueError, 'кратно'):
            validate_approved_orders(base.assign(approved_qty=23.))
        with self.assertRaisesRegex(ValueError, 'минимальной партии'):
            validate_approved_orders(base.assign(approved_qty=10.))
        with self.assertRaisesRegex(ValueError, 'неотрицательным'):
            validate_approved_orders(base.assign(approved_qty=-5.))
        blocked = base.assign(status='заблокирован — нет актуального остатка')
        with self.assertRaisesRegex(ValueError, 'без актуального остатка'):
            validate_approved_orders(blocked)

    def test_pipeline_outputs_grouped_orders_with_reason(self):
        parts = []
        # Артикулы подобраны так, что сортировка по ним перемешивает поставщиков.
        for sku, supplier, qty, stock, category in [('A0', 'S2', 8., 400., 'Щиты'), ('A1', 'S1', 10., 50., 'Кабель'),
                                                    ('A2', 'S1', 20., 900., 'Кабель'), ('B1', 'S2', 5., 0., 'Щиты')]:
            parts.append(sales(90, qty=qty, stock=stock).assign(sku=sku, supplier=supplier, category=category))
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            path = root / 'sales.csv'
            pd.concat(parts).to_csv(path, index=False)
            config = Config(output_dir=root / 'out', processed_dir=root / 'p', model_dir=root / 'm')
            orders = run_pipeline(path, config)
            self.assertEqual(list(orders.supplier), ['S1', 'S1', 'S2', 'S2'])
            self.assertEqual(list(orders[orders.supplier == 'S2'].sku), ['B1', 'A0'])
            self.assertTrue(orders.reason.str.len().gt(50).all())
            for _, row in orders.iterrows():
                if row.recommended_qty > 0:
                    self.assertIn(f"заказ {row.recommended_qty:.0f} ед.", row.reason)
            self.assertEqual(orders.set_index('sku').loc['A2', 'urgency'], 'не требуется')
            self.assertEqual(orders.set_index('sku').loc['B1', 'urgency'], 'критично')
            self.assertTrue(orders.status.str.contains('требует утверждения').all())
            summary = supplier_summary(orders)
            self.assertEqual(set(summary.supplier), {'S1', 'S2'})

            sheets = pd.read_excel(root / 'out' / 'supplier_orders.xlsx', sheet_name=None)
            self.assertTrue({'Сводка', 'Все позиции', 'S1', 'S2'} <= set(sheets))
            self.assertTrue(sheets['S1']['Обоснование'].notna().all())
            one_c = pd.read_csv(root / 'out' / 'supplier_orders_1c.csv', sep=';', encoding='utf-8-sig')
            self.assertEqual(set(one_c['Артикул']), {'A1', 'B1'})

            only = run_pipeline(path, config, category='Щиты')
            self.assertEqual(set(only.sku), {'A0', 'B1'})


if __name__ == '__main__':
    unittest.main()
