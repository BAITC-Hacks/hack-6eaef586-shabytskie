"""Read CSV/XLSX and resolve multilingual aliases without guessing ambiguities."""
import logging
from pathlib import Path
import pandas as pd
from src.config import ALIASES, REQUIRED


def map_columns(frame: pd.DataFrame) -> pd.DataFrame:
    """Map aliases and reject ambiguous or missing required fields."""
    lookup = {alias.strip().casefold(): key for key, aliases in ALIASES.items() for alias in aliases}
    renamed = [lookup.get(str(col).strip().casefold(), str(col).strip()) for col in frame.columns]
    if len(renamed) != len(set(renamed)):
        raise ValueError('Ambiguous columns: multiple input columns map to the same field')
    result = frame.copy()
    result.columns = renamed
    missing = REQUIRED - set(renamed)
    if missing:
        raise ValueError(f"Missing required fields: {', '.join(sorted(missing))}. Required: date, sku, quantity")
    optional = set(ALIASES) - REQUIRED - set(renamed)
    if optional:
        logging.warning('Missing optional fields: %s', ', '.join(sorted(optional)))
    return result


def load_data(path: str | Path) -> pd.DataFrame:
    """Preserve identifier strings, including leading zeroes."""
    path = Path(path)
    if path.suffix.lower() == '.csv':
        frame = pd.read_csv(path, dtype=str, sep=None, engine='python')
    elif path.suffix.lower() == '.xlsx':
        frame = pd.read_excel(path, dtype=str)
    else:
        raise ValueError('Input must be CSV or XLSX')
    logging.info('Loaded %s rows', f'{len(frame):,}')
    return map_columns(frame)
