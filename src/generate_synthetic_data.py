"""Deterministic daily sales with observable anomalies and latent demand truth."""
from pathlib import Path
import numpy as np
import pandas as pd


def generate(path: str | Path = 'data/raw/synthetic_sales.csv', seed: int = 42) -> pd.DataFrame:
    """Generate 20 SKUs, 400 days, two clients and two warehouses per SKU/day."""
    rng = np.random.default_rng(seed)
    rows = []
    for i in range(20):
        for t, date in enumerate(pd.date_range('2024-01-01', periods=400)):
            trend = 1 + (.7 if i % 3 == 0 else -.45 if i % 3 == 1 else 0) * t / 400
            weekly = 1 + .35 * np.sin(2 * np.pi * date.dayofweek / 7)
            annual = 1 + .25 * np.sin(2 * np.pi * date.dayofyear / 365.25)
            demand = max(0, int(rng.poisson((12 + i * 2) * trend * weekly * annual)))
            stockout = 120 <= t < 127 or 260 <= t < 266
            bulk = 350 if t == 290 else 0
            for client in range(2):
                regular = demand // 2 if client == 0 else demand - demand // 2
                rows.append(dict(date=date.date(), sku=f'SKU{i+1:03d}', product_name=f'Electrical product {i+1}',
                                 quantity=0 if stockout else regular + (bulk if client == 0 else 0),
                                 client_id=f'ANON{client+1:03d}', warehouse=f'WH{client+1}', price=500 + i * 80,
                                 stock=0 if stockout else int(rng.integers(20, 150)), stockout_flag=stockout,
                                 supplier=f'Supplier {i % 4 + 1}', lead_time_days=7 + i % 4 * 7,
                                 in_transit=20 if t % 30 < 8 else 0, category=f'Category {i % 3 + 1}',
                                 synthetic_regular_demand=regular, synthetic_bulk=bulk if client == 0 else 0))
    frame = pd.DataFrame(rows)
    Path(path).parent.mkdir(parents=True, exist_ok=True)
    frame.to_csv(path, index=False)
    return frame


if __name__ == '__main__':
    generate()
