import logging
from pathlib import Path
import pandas as pd
from src.config import ALIASES, REQUIRED
from src.secure_files import read_bounded_table
from src.security import MAX_QUANTITY, SecurityError

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
    # Unknown export columns may contain names, phones or emails. They are not
    # needed by this module and must not flow into audit files or the browser.
    result = result[[col for col in result if col in ALIASES or col == 'transaction_id']]
    return result


def read_table(path: str | Path) -> pd.DataFrame:
    return read_bounded_table(path)


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
    for col in required:
        if frame[col].isna().any() or frame[col].astype('string').str.strip().eq('').any():
            raise SecurityError('reference_required', f'Обязательное поле {col} не заполнено.')
    # Bound numerical references before model training or date-range allocation.
    from src.preprocessing import parse_numbers
    for col in ['stock', 'in_transit', 'price', 'lead_time_days', 'min_order_qty', 'order_multiple']:
        if col not in frame:
            continue
        numbers = parse_numbers(frame[col])
        maximum = 275 if col == 'lead_time_days' else MAX_QUANTITY
        minimum = 1 if col in {'lead_time_days', 'order_multiple'} else 0
        invalid = frame[col].notna() & (numbers.isna() | numbers.lt(minimum) | numbers.gt(maximum))
        if invalid.any():
            raise SecurityError('reference_numeric', f'Недопустимые значения поля {col}.')
        frame[col] = numbers
    return frame
