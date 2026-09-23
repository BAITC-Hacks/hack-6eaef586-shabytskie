import json
from pathlib import Path
import joblib
import numpy as np
import pandas as pd
from src.config import Config
from src.data_loader import load_data
from src.feature_engineering import FEATURES, feature_row
from src.forecasting import predict_future
from src.pipeline import prepare


def main() -> None:
    raw = load_data('data/raw/synthetic_sales.csv')
    _, daily = prepare(raw)
    bundle = joblib.load('models/demand_model.joblib')
    model = bundle['model']
    group = daily[daily.sku == 'SKU001']
    history = group.adjusted_demand.tolist()
    date = group.date.max() + pd.Timedelta(days=1)
    base = feature_row('SKU001', date, history)
    weekday_rows = [dict(base, day_of_week=day) for day in range(7)]
    growth_rows = [dict(base, growth_rate=rate) for rate in [-.3, .3]]
    weekdays = model.predict(pd.DataFrame(weekday_rows)[FEATURES])
    growth = model.predict(pd.DataFrame(growth_rows)[FEATURES])
    modified = raw.copy()
    idx = modified[(modified.sku == 'SKU001') & (modified.date == modified.date.max())].index[0]
    modified.loc[idx, 'quantity'] = str(float(modified.loc[idx, 'quantity']) + 1000)
    _, changed = prepare(modified)
    baseline_forecast = predict_future(daily, 30, model, Config())
    changed_forecast = predict_future(changed, 30, model, Config())
    a = baseline_forecast[baseline_forecast.sku == 'SKU001'].prediction.sum()
    b = changed_forecast[changed_forecast.sku == 'SKU001'].prediction.sum()
    report = {
        'calendar_sensitivity': {'weekday_predictions_fixed_history': weekdays.tolist(),
                                 'prediction_range': float(np.ptp(weekdays))},
        'growth_sensitivity': {'prediction_at_minus_30_percent': float(growth[0]),
                               'prediction_at_plus_30_percent': float(growth[1]),
                               'difference': float(growth[1] - growth[0])},
        'stockout_correction': {'stockout_sku_days': int(daily.stockout_flag.sum()),
                               'total_lost_demand_estimated': float(daily.estimated_lost_demand.sum())},
        'bulk_order_sensitivity': {'injected_units': 1000, 'original_30_day_forecast': float(a),
                                   'perturbed_30_day_forecast': float(b),
                                   'forecast_change_percent': float(100 * (b / a - 1)),
                                   'note': 'Fixed fitted model; altered history is reprocessed before forecasting.'}}
    Path('outputs/synthetic_checks.json').write_text(json.dumps(report, indent=2))
    print(json.dumps(report, indent=2))


if __name__ == '__main__':
    main()
