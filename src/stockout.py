import pandas as pd
from src.preprocessing import parse_dates


def apply_stockout_periods(daily: pd.DataFrame, periods: pd.DataFrame | None) -> pd.DataFrame:
    if periods is None or periods.empty:
        return daily
    result = daily.copy()
    starts = parse_dates(periods.date_from)
    ends = parse_dates(periods.date_to).fillna(result.date.max()) if 'date_to' in periods else pd.Series(result.date.max(), index=periods.index)
    for sku, start, end in zip(periods.sku.astype(str), starts, ends):
        if pd.isna(start):
            continue
        mask = result.sku.eq(sku) & result.date.between(start, end)
        result.loc[mask, 'stockout_flag'] = True
    return result


def correct_stockouts(daily: pd.DataFrame) -> pd.DataFrame:
    # Ожидаемый спрос в день дефицита — медиана 14 предыдущих надёжных дней
    # (без дефицита и аномалий); упущенный спрос = ожидаемый − продано.
    result = daily.copy()
    valid = ~(result.stockout_flag | result.is_outlier | result.is_large_client_order)
    reliable = result.quantity_clean.where(valid)
    expected = reliable.groupby(result.sku).transform(lambda s: s.shift(1).rolling(14, min_periods=1).median())
    fallback = reliable.groupby(result.sku).transform(lambda s: s.shift(1).expanding(min_periods=1).median())
    expected = expected.fillna(fallback)
    result['stockout_estimate_available'] = expected.notna()
    result['estimated_lost_demand'] = (expected.fillna(0) - result.quantity_clean).clip(lower=0).where(result.stockout_flag, 0)
    result['adjusted_demand'] = result.quantity_clean + result.estimated_lost_demand
    return result
