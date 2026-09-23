import logging
from pathlib import Path
import pandas as pd
from src.config import ALIASES, REQUIRED

SALES_OPTIONAL = {'client_id', 'product_name', 'price', 'warehouse', 'stock', 'stockout_flag',
                  'supplier', 'lead_time_days', 'in_transit', 'category'}


def map_columns(frame: pd.DataFrame, required: set[str] = REQUIRED,
                optional: set[str] | None = SALES_OPTIONAL) -> pd.DataFrame:
    lookup = {alias.strip().casefold(): key for key, aliases in ALIASES.items() for alias in aliases}
    renamed = [lookup.get(str(col).strip().casefold(), str(col).strip()) for col in frame.columns]
    if len(renamed) != len(set(renamed)):
        raise ValueError('Ambiguous columns: multiple input columns map to the same field')
    result = frame.copy()
    result.columns = renamed
    missing = required - set(renamed)
    if missing:
        raise ValueError(f"Missing required fields: {', '.join(sorted(missing))}. "
                         f"Required: {', '.join(sorted(required))}")
    absent = (optional or set()) - set(renamed)
    if absent:
        logging.warning('Missing optional fields: %s', ', '.join(sorted(absent)))
    return result


def read_table(path: str | Path) -> pd.DataFrame:
    path = Path(path)
    if path.suffix.lower() == '.csv':
        return pd.read_csv(path, dtype=str, sep=None, engine='python', encoding='utf-8-sig')
    if path.suffix.lower() == '.xlsx':
        return pd.read_excel(path, dtype=str)
    raise ValueError(f'{path.name}: input must be CSV or XLSX')


def load_data(path: str | Path) -> pd.DataFrame:
    frame = read_table(path)
    logging.info('Loaded %s rows', f'{len(frame):,}')
    return map_columns(frame)


def load_reference(path: str | Path | None, required: set[str]) -> pd.DataFrame | None:
    if path is None:
        return None
    frame = map_columns(read_table(path), required, None)
    for col in ['sku', 'supplier', 'category', 'warehouse']:
        if col in frame:
            frame[col] = frame[col].astype('string').str.strip()
    return frame
