"""Transaction cleaning and dense SKU daily aggregation."""
import logging
import numpy as np
import pandas as pd


def clean_transactions(frame: pd.DataFrame) -> pd.DataFrame:
    """Remove exact duplicates; reject invalid targets instead of fabricating demand."""
    frame = frame.drop_duplicates().copy()
    frame['date'] = pd.to_datetime(frame['date'], errors='coerce', format='mixed').dt.normalize()
    frame['sku'] = frame['sku'].astype('string').str.strip().replace('', pd.NA)
    for col in ['quantity', 'stock', 'price', 'lead_time_days', 'in_transit']:
        if col in frame:
            frame[col] = pd.to_numeric(frame[col].astype('string').str.replace(' ', '').str.replace(',', '.'), errors='coerce').astype(float).replace([np.inf, -np.inf], np.nan)
    invalid = frame[['date', 'sku', 'quantity']].isna().any(axis=1) | frame['quantity'].lt(0)
    if invalid.any():
        logging.warning('Dropped %d rows with invalid date/SKU/quantity or negative returns', invalid.sum())
    frame = frame.loc[~invalid].copy()
    if frame.empty:
        raise ValueError('No valid sales rows after cleaning')
    for col in ['stock', 'price', 'lead_time_days', 'in_transit']:
        if col in frame:
            frame.loc[frame[col].lt(0), col] = np.nan
    if 'stockout_flag' in frame:
        values = frame['stockout_flag'].astype('string').str.strip().str.casefold()
        valid = values.isna() | values.isin(['true', 'false', '1', '0', '1.0', '0.0', 'yes', 'no', 'да', 'нет', ''])
        if not valid.all():
            raise ValueError('Unrecognized stockout_flag values; use true/false, 1/0, yes/no or да/нет')
        frame['stockout_flag'] = values.isin(['true', '1', '1.0', 'yes', 'да'])
    else:
        frame['stockout_flag'] = False
    frame['warehouse'] = frame.get('warehouse', pd.Series('all', index=frame.index)).fillna('unknown')
    return frame.sort_values(['sku', 'date']).reset_index(drop=True)


def aggregate_daily(frame: pd.DataFrame) -> pd.DataFrame:
    """Sum demand, deduplicate warehouse snapshots, and retain explicit zero days.

    Missing calendar days imply zero recorded sales but unknown stock. Inventory
    snapshots are never carried forward to infer historical stockouts.
    """
    keys = ['sku', 'date']
    grouped = frame.groupby(keys, sort=True)
    daily = grouped.agg(quantity=('quantity', 'sum'), quantity_clean=('quantity_clean', 'sum'),
                        large_client_orders_detected=('large_client_order_count', 'sum'),
                        stockout_flag=('stockout_flag', 'max'))
    snapshots = frame.groupby(keys + ['warehouse'], sort=False).last()
    for col in ['stock', 'in_transit']:
        if col in snapshots:
            daily[col] = snapshots[col].groupby(level=keys).sum(min_count=1)
    if 'stock' in daily:
        daily['stockout_flag'] |= daily['stock'].eq(0)
    for col in ['product_name', 'supplier', 'category', 'lead_time_days', 'price']:
        if col in frame:
            daily[col] = grouped[col].last()
    daily = daily.reset_index()
    end = daily['date'].max()
    parts = []
    for sku, group in daily.groupby('sku', sort=False):
        group = group.set_index('date').reindex(pd.date_range(group.date.min(), end, name='date'))
        group['sku'] = sku
        group['observed_day'] = group['quantity'].notna()
        for col in ['quantity', 'quantity_clean', 'large_client_orders_detected']:
            group[col] = group[col].fillna(0)
        group['stockout_flag'] = group['stockout_flag'].fillna(False).astype(bool)
        for col in ['product_name', 'supplier', 'category', 'lead_time_days', 'price']:
            if col in group:
                group[col] = group[col].ffill()
        parts.append(group.reset_index())
    return pd.concat(parts, ignore_index=True).sort_values(keys).reset_index(drop=True)
