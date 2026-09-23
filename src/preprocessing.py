import hashlib
import logging
import secrets
import numpy as np
import pandas as pd
from src.config import ALIASES
from src.security import (MAX_ROWS, MAX_SKUS, MAX_WAREHOUSES, MAX_DAYS, MAX_DAILY_ROWS,
                          MAX_QUANTITY, MAX_CELL_CHARS, MAX_CELLS, SecurityError)

SPACES = '[\\s\u00a0\u202f]'
TRUE_VALUES = ['true', '1', '1.0', 'yes', 'да', 'истина']
FALSE_VALUES = ['false', '0', '0.0', 'no', 'нет', 'ложь', '']


def parse_dates(values: pd.Series) -> pd.Series:
    # ISO (2025-02-01, в т.ч. с временем из Excel) читается как есть, остальное — день первым,
    # как в выгрузках 1С: 01.02.2025 — это 1 февраля, а не 2 января.
    text = values.astype('string').str.strip()
    iso = text.str.match(r'^\d{4}-\d{1,2}-\d{1,2}').fillna(False).astype(bool)
    parsed = pd.to_datetime(text.where(iso), errors='coerce', format='mixed')
    dayfirst = pd.to_datetime(text.where(~iso), errors='coerce', format='mixed', dayfirst=True)
    return parsed.fillna(dayfirst).dt.normalize()


def parse_numbers(values: pd.Series) -> pd.Series:
    # 1С разделяет разряды неразрывным пробелом и пишет дробную часть через запятую: «1 234,5».
    text = values.astype('string').str.replace(SPACES, '', regex=True).str.replace(',', '.')
    return pd.to_numeric(text, errors='coerce').astype(float).replace([np.inf, -np.inf], np.nan)


def pseudonymize(values: pd.Series) -> pd.Series:
    # ID клиента нужен только для поиска разовых заказов. Он сразу заменяется хэшем со случайной
    # солью на время запуска, поэтому даже неанонимизированный ID не попадает в расчёт и файлы.
    salt = secrets.token_hex(32)
    text = values.astype('string').str.strip().replace('', pd.NA)
    return text.map(lambda v: 'C' + hashlib.sha256(f'{salt}{v}'.encode()).hexdigest()[:32], na_action='ignore')


def clean_transactions(frame: pd.DataFrame) -> pd.DataFrame:
    if len(frame) > MAX_ROWS:
        raise SecurityError('rows_limit', 'Слишком много строк продаж.', 413)
    for col in frame.select_dtypes(include=['object', 'string']):
        if frame[col].astype('string').str.len().gt(MAX_CELL_CHARS).any():
            raise SecurityError('cell_size', 'Ячейка слишком длинная.')
    frame = frame.drop_duplicates().copy()
    frame = frame[[col for col in frame if col in ALIASES]]
    frame['date'] = parse_dates(frame['date'])
    frame['sku'] = frame['sku'].astype('string').str.strip().replace('', pd.NA)
    if 'client_id' in frame:
        frame['client_id'] = pseudonymize(frame['client_id'].replace('', pd.NA))
    for col in ['quantity', 'stock', 'price', 'lead_time_days', 'in_transit']:
        if col in frame:
            frame[col] = parse_numbers(frame[col])
            maximum = 275 if col == 'lead_time_days' else MAX_QUANTITY
            if frame[col].abs().gt(maximum).any():
                raise SecurityError('numeric_bounds', f'Превышен предел поля {col}.')
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
        valid = values.isna() | values.isin(TRUE_VALUES + FALSE_VALUES)
        if not valid.all():
            raise ValueError('Unrecognized stockout_flag values; use true/false, 1/0, yes/no, да/нет or истина/ложь')
        frame['stockout_flag'] = values.isin(TRUE_VALUES)
    else:
        frame['stockout_flag'] = False
    frame['warehouse'] = frame.get('warehouse', pd.Series('all', index=frame.index)).fillna('unknown')
    validate_demand_size(frame)
    return frame.sort_values(['sku', 'date']).reset_index(drop=True)


def validate_demand_size(frame: pd.DataFrame) -> None:
    """Reject calendar/warehouse amplification before reindex or unstack."""
    if frame.sku.nunique() > MAX_SKUS or frame.warehouse.nunique() > MAX_WAREHOUSES:
        raise SecurityError('cardinality', 'Превышен лимит артикулов или складов.', 413)
    end = frame.date.max()
    spans = (end - frame.groupby('sku').date.min()).dt.days + 1
    warehouse_cells = len(frame[['sku', 'date']].drop_duplicates()) * frame.warehouse.nunique()
    if spans.max() > MAX_DAYS or spans.sum() > MAX_DAILY_ROWS or warehouse_cells > MAX_CELLS:
        raise SecurityError('calendar_expansion', 'Слишком большой диапазон дат или объём дневных данных.', 413)


def aggregate_daily(frame: pd.DataFrame) -> pd.DataFrame:
    validate_demand_size(frame)
    keys = ['sku', 'date']
    grouped = frame.groupby(keys, sort=True)
    daily = grouped.agg(quantity=('quantity', 'sum'), quantity_clean=('quantity_clean', 'sum'),
                        large_client_orders_detected=('large_client_order_count', 'sum'),
                        stockout_flag=('stockout_flag', 'max'))
    # Остаток артикула = сумма последних снимков по складам. Если по складу в этот день не было
    # строк, берётся его предыдущий снимок (до 30 дней наблюдений), иначе склад без продаж
    # выпал бы из остатка и дал ложный дефицит.
    snapshots = frame.groupby(keys + ['warehouse'], sort=True).last()
    for col in ['stock', 'in_transit']:
        if col in snapshots:
            wide = snapshots[col].unstack('warehouse').groupby(level='sku').ffill(limit=30)
            daily[col] = wide.sum(axis=1, min_count=1)
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
