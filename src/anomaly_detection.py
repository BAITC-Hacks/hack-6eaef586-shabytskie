import numpy as np
import pandas as pd


def historical_bounds(values: pd.Series, window: int = 90) -> pd.Series:
    past = values.shift(1).rolling(window, min_periods=7)
    q1, q3 = past.quantile(.25), past.quantile(.75)
    return q3 + 1.5 * (q3 - q1)


def detect_client_orders(frame: pd.DataFrame) -> pd.DataFrame:
    result = frame.copy()
    result['quantity_clean'] = result['quantity']
    result['is_large_client_order'] = False
    result['large_client_order_count'] = 0
    if 'client_id' not in result:
        return result
    # Нулевые строки (снимки остатков) — не заказы: иначе медиана заказа 0 и любая продажа кажется разовой.
    purchases = result.client_id.notna() & result.client_id.ne('') & result.quantity.gt(0)
    orders = result[purchases].groupby(['sku', 'date', 'client_id']).quantity.sum().reset_index()
    caps = {}
    for sku, group in orders.groupby('sku', sort=False):
        history = []
        for date, day in group.groupby('date', sort=True):
            if len(history) >= 7:
                past = np.asarray(history[-500:])
                q1, median, q3 = np.quantile(past, [.25, .5, .75])
                mad = np.median(np.abs(past - median))
                # Порог по прошлым заказам клиентов; заказ выше порога считается разовым,
                # и его объём заменяется медианным обычным заказом.
                bound = max(q3 + 3 * (q3 - q1), median + 6 * 1.4826 * mad, 3 * median, 1.)
                for row in day.itertuples():
                    if row.quantity > bound:
                        caps[(sku, date, row.client_id)] = median / row.quantity
            history.extend(day.quantity.tolist())
    seen = set()
    for idx, row in result.iterrows():
        key = (row.sku, row.date, row.get('client_id'))
        if key in caps:
            result.at[idx, 'quantity_clean'] *= caps[key]
            result.at[idx, 'is_large_client_order'] = True
            result.at[idx, 'large_client_order_count'] = int(key not in seen)
            seen.add(key)
    return result


def detect_daily_outliers(daily: pd.DataFrame) -> pd.DataFrame:
    # Дневной спрос ограничивается Q3 + 1.5·IQR за предыдущие 90 дней без дефицита. Считаются только
    # дни с продажами: у редко продаваемого товара Q3 по всем дням равен 0, и выбросом стал бы любой день.
    result = daily.copy()
    eligible = result.quantity_clean.where(~result.stockout_flag & result.quantity_clean.gt(0))
    bounds = eligible.groupby(result.sku).transform(historical_bounds)
    result['outlier_upper_bound'] = bounds
    result['is_outlier'] = result.quantity_clean.gt(bounds)
    result['quantity_clean'] = result.quantity_clean.where(~result.is_outlier, bounds)
    # Extreme anonymous bulk orders must not leave an inflated cap in the model's
    # recent lags. Replace them with a typical past selling day, using no future data.
    typical = eligible.groupby(result.sku).transform(
        lambda values: values.shift(1).rolling(90, min_periods=7).median())
    extreme = eligible.gt(3 * bounds) & typical.notna()
    result.loc[extreme, 'quantity_clean'] = typical[extreme]
    result['is_large_client_order'] = result.large_client_orders_detected.gt(0)
    return result
