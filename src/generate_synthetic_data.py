from pathlib import Path
import numpy as np
import pandas as pd

DAYS = 400


def generate(path: str | Path = 'data/demo/synthetic_sales.csv', seed: int = 42) -> pd.DataFrame:
    rng = np.random.default_rng(seed)
    rows = []
    for i in range(20):
        sku = f'SKU{i+1:03d}'
        base, cover = 12 + i * 2, (5, 12, 20, 35, 60)[i % 5]
        for t, date in enumerate(pd.date_range('2024-01-01', periods=DAYS)):
            trend = 1 + (.7 if i % 3 == 0 else -.45 if i % 3 == 1 else 0) * t / DAYS
            weekly = 1 + .35 * np.sin(2 * np.pi * date.dayofweek / 7)
            annual = 1 + .25 * np.sin(2 * np.pi * date.dayofyear / 365.25)
            demand = max(0, int(rng.poisson(base * trend * weekly * annual)))
            stockout = 120 <= t < 127 or 260 <= t < 266 or (i == 4 and t >= DAYS - 5)
            hidden_stockout = i == 9 and t >= DAYS - 4
            bulk = 350 if t == 290 else 600 if (i == 2 and t == DAYS - 5) else 0
            for client in range(2):
                regular = demand // 2 if client == 0 else demand - demand // 2
                sold = 0 if stockout or hidden_stockout else regular + (bulk if client == 0 else 0)
                rows.append(dict(date=date.date(), sku=sku, product_name=f'Электротовар {i+1}',
                                 quantity=sold, client_id=f'ANON{client+1:03d}', warehouse=f'WH{client+1}',
                                 price=500 + i * 80, stock=0 if stockout else None if hidden_stockout else int(base * cover / 2 * rng.uniform(.8, 1.2)),
                                 stockout_flag=stockout, supplier=f'Поставщик {i % 4 + 1}',
                                 lead_time_days=7 + i % 4 * 7,
                                 in_transit=(base * 3 if (t + 3 * i) % 30 < 8 else 0) if client == 0 else 0,
                                 category=f'Категория {i % 3 + 1}',
                                 synthetic_regular_demand=regular, synthetic_bulk=bulk if client == 0 else 0))
    frame = pd.DataFrame(rows)
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    frame.to_csv(path, index=False)
    root = path.parent
    pd.DataFrame({'supplier': [f'Поставщик {k}' for k in range(1, 5)],
                  'lead_time_days': [7, 14, 21, 28],
                  'min_order_qty': [0, 50, 100, 0]}).to_csv(root / 'suppliers.csv', index=False)
    pd.DataFrame({'sku': [f'SKU{i+1:03d}' for i in range(20)],
                  'product_name': [f'Электротовар {i+1}' for i in range(20)],
                  'category': [f'Категория {i % 3 + 1}' for i in range(20)],
                  'supplier': [f'Поставщик {i % 4 + 1}' for i in range(20)],
                  'order_multiple': [10 if i % 2 == 0 else 1 for i in range(20)]}).to_csv(root / 'catalog.csv', index=False)
    end = pd.Timestamp('2024-01-01') + pd.Timedelta(days=DAYS - 1)
    pd.DataFrame({'sku': ['SKU010'], 'date_from': [(end - pd.Timedelta(days=3)).date()],
                  'date_to': [end.date()]}).to_csv(root / 'stockouts.csv', index=False)
    pd.DataFrame({'category': ['Категория 1', 'Категория 2', 'Категория 3'],
                  'growth_forecast': ['10%', '0%', '-5%']}).to_csv(root / 'category_growth.csv', index=False)
    return frame


if __name__ == '__main__':
    generate()
