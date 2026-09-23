"""Estimate censored demand using only earlier reliable observations."""
import pandas as pd


def correct_stockouts(daily: pd.DataFrame) -> pd.DataFrame:
    """Use prior 14 calendar days, then expanding history; leave cold starts zero."""
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
