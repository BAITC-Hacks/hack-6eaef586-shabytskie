import numpy as np
import pandas as pd

NUMERIC_FEATURES = ['day_of_week', 'week_of_year', 'month', 'quarter', 'day_of_month',
                    'year_sin', 'year_cos', 'lag_1', 'lag_7', 'lag_14', 'lag_28',
                    'rolling_mean_7', 'rolling_mean_14', 'rolling_mean_30',
                    'rolling_median_7', 'rolling_std_7', 'rolling_std_30', 'growth_rate']
FEATURES = ['sku'] + NUMERIC_FEATURES


def feature_row(sku: str, date: pd.Timestamp, history: list[float]) -> dict:
    # history содержит только дни до целевого — будущие данные в признаки не попадают.
    h = np.asarray(history, dtype=float)
    row = dict(sku=str(sku), day_of_week=date.dayofweek, week_of_year=int(date.isocalendar().week),
               month=date.month, quarter=date.quarter, day_of_month=date.day,
               year_sin=np.sin(2 * np.pi * date.dayofyear / 365.25),
               year_cos=np.cos(2 * np.pi * date.dayofyear / 365.25))
    for lag in [1, 7, 14, 28]:
        row[f'lag_{lag}'] = h[-lag] if len(h) >= lag else np.nan
    for window in [7, 14, 30]:
        row[f'rolling_mean_{window}'] = np.mean(h[-window:]) if len(h) else np.nan
    row['rolling_median_7'] = np.median(h[-7:]) if len(h) else np.nan
    for window in [7, 30]:
        row[f'rolling_std_{window}'] = np.std(h[-window:]) if len(h) else np.nan
    recent, previous = h[-30:], h[-60:-30]
    row['growth_rate'] = float(np.clip(np.mean(recent) / np.mean(previous) - 1, -1, 10)) if len(previous) and np.mean(previous) > 0 else 0.
    return row


def engineer_features(daily: pd.DataFrame) -> pd.DataFrame:
    rows = []
    for sku, group in daily.groupby('sku', sort=False):
        history = []
        for row in group.itertuples():
            rows.append((row.Index, feature_row(sku, row.date, history)))
            history.append(row.adjusted_demand)
    features = pd.DataFrame([row for _, row in rows], index=[idx for idx, _ in rows])
    return daily.join(features.drop(columns='sku'))


def seasonality(history: pd.DataFrame) -> tuple[bool, float]:
    values = history.adjusted_demand
    if len(values) < 56:
        return False, 0.
    residual = values - values.rolling(28, min_periods=7).mean()
    strength = residual.autocorr(lag=7) if residual.std() > 1e-8 else 0.
    strength = float(np.clip(strength, 0, 1)) if np.isfinite(strength) else 0.
    return strength >= .3, strength
