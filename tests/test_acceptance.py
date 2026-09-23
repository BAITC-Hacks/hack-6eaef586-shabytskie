import logging
import tempfile
import unittest
from pathlib import Path
import numpy as np
import pandas as pd
from src.config import Config
from src.pipeline import run_pipeline


def run(sales: pd.DataFrame) -> pd.DataFrame:
    root = Path(tempfile.mkdtemp())
    sales.to_csv(root / 'sales.csv', index=False)
    config = Config(output_dir=root / 'out', processed_dir=root / 'proc', model_dir=root / 'models')
    logging.disable(logging.CRITICAL)
    try:
        return run_pipeline(root / 'sales.csv', config).set_index('sku')
    finally:
        logging.disable(logging.NOTSET)


def seasonal_sales(skus: int = 2) -> pd.DataFrame:
    # Лето втрое выше остального года, недельный цикл ±30%, два года истории, расчёт в конце мая.
    rng = np.random.default_rng(7)
    rows = []
    for k in range(skus):
        for date in pd.date_range('2023-05-01', '2025-05-25'):
            level = (20 + 10 * k) * (3. if date.month in (6, 7, 8) else 1.) * (1 + .3 * np.sin(2 * np.pi * date.dayofweek / 7))
            rows.append(dict(date=date.date(), sku=f'S{k}', quantity=float(rng.poisson(level)), client_id=f'K{date.day % 5}',
                             stock=100., lead_time_days=21))
    return pd.DataFrame(rows)


class EndToEndAcceptance(unittest.TestCase):
    """Проверки из ТЗ через полный расчёт: обучение модели, выбор метода, заказ."""

    def test_sharp_seasonality_through_full_pipeline(self):
        sales = seasonal_sales()
        orders = run(sales)
        for sku, row in orders.iterrows():
            k = int(sku[1:])
            days = pd.date_range('2025-05-26', periods=row.coverage_days)
            truth = sum((20 + 10 * k) * (3. if d.month in (6, 7, 8) else 1.) for d in days)
            history_mean = sales[sales.sku == sku].quantity.mean() * row.coverage_days
            self.assertGreater(row.demand_for_coverage, 1.3 * history_mean)
            self.assertAlmostEqual(row.demand_for_coverage, truth, delta=.25 * truth)

    def test_anonymous_bulk_sale_through_full_pipeline(self):
        sales = seasonal_sales(1)
        sales = sales[sales.date >= pd.Timestamp('2024-09-01').date()].assign(stock=500.)
        base = run(sales).loc['S0', 'recommended_qty']
        bulk = sales.copy()
        bulk.loc[bulk.index[-3], ['quantity', 'client_id']] = [1500., np.nan]
        self.assertLessEqual(abs(run(bulk).loc['S0', 'recommended_qty'] - base), .05 * base)


if __name__ == '__main__':
    unittest.main()
