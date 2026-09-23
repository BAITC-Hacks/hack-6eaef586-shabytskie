"""Input schema and cross-table validation; no business cleaning is performed."""
from __future__ import annotations
import numpy as np
import pandas as pd
from contracts import InputBundle

REQUIRED = {"sales": {"date", "sku", "quantity"}, "inventory": {"sku", "product_name", "category", "supplier", "current_stock", "in_transit"}, "suppliers": {"supplier", "lead_time_days"}}
NUMERIC = {"sales": ["quantity"], "inventory": ["current_stock", "in_transit", "unit_cost", "moq", "order_multiple"], "suppliers": ["lead_time_days"]}
PERSONAL_DATA_COLUMNS = {"customer_name", "client_name", "email", "phone", "telephone", "iin", "address"}


def validate_bundle(bundle: InputBundle) -> list[str]:
    errors: list[str] = []
    tables = {"sales": bundle.sales, "inventory": bundle.inventory, "suppliers": bundle.suppliers}
    if bundle.stock_history is not None:
        tables["stock_history"] = bundle.stock_history
    for name, df in tables.items():
        if df.empty:
            errors.append(f"{name}: file has no data rows.")
            continue
        missing = REQUIRED.get(name, set()) - set(df.columns)
        if missing:
            errors.append(f"{name}: missing required columns: {', '.join(sorted(missing))}.")
        personal = PERSONAL_DATA_COLUMNS & {str(col).strip().casefold() for col in df.columns}
        if personal:
            errors.append(f"{name}: personal customer columns are not allowed: {', '.join(sorted(personal))}. Use anonymized client_id.")
        if "sku" in df and df.sku.isna().any():
            errors.append(f"{name}.sku: {int(df.sku.isna().sum())} rows have missing SKU identifiers.")
        if "supplier" in df and df.supplier.isna().any():
            errors.append(f"{name}.supplier: {int(df.supplier.isna().sum())} rows have missing supplier identifiers.")
        if "date" in df:
            parsed = pd.to_datetime(df.date, errors="coerce")
            if parsed.isna().any(): errors.append(f"{name}.date: {int(parsed.isna().sum())} values are missing or invalid.")
        if name == "sales" and "client_id" in df and df.client_id.isna().any():
            errors.append(f"sales.client_id: {int(df.client_id.isna().sum())} rows are missing anonymized customer IDs.")
        required_numeric = {"sales": {"quantity"}, "inventory": {"current_stock", "in_transit"}, "suppliers": {"lead_time_days"}}
        for col in NUMERIC.get(name, []):
            if col in df:
                values = pd.to_numeric(df[col], errors="coerce")
                bad = (values.isna() | ~np.isfinite(values)) if col in required_numeric.get(name, set()) else (df[col].notna() & (values.isna() | ~np.isfinite(values)))
                if bad.any(): errors.append(f"{name}.{col}: {int(bad.sum())} rows are missing, non-numeric, or non-finite.")
                elif (values < 0).any(): errors.append(f"{name}.{col}: negative values are not allowed (rows {', '.join(map(str, (np.flatnonzero(values < 0)[:10] + 2).tolist()))}).")
                if name == "suppliers" and col == "lead_time_days" and not bad.any() and not (values.dropna() % 1 == 0).all():
                    errors.append("suppliers.lead_time_days: values must be whole non-negative days.")
        if name == "inventory" and "sku" in df and df.sku.astype(str).duplicated().any(): errors.append("inventory.sku: SKUs must be unique.")
        if name == "suppliers" and "supplier" in df and df.supplier.astype(str).duplicated().any(): errors.append("suppliers.supplier: suppliers must be unique.")
    if not errors and all(k in tables for k in ("sales", "inventory", "suppliers")):
        inv = set(bundle.inventory.sku.astype(str)); sales = set(bundle.sales.sku.astype(str)); suppliers = set(bundle.suppliers.supplier.astype(str))
        unknown_skus = sales - inv
        if unknown_skus: errors.append(f"sales.sku: {len(unknown_skus)} SKU(s) are absent from inventory, including {', '.join(sorted(unknown_skus)[:5])}.")
        unknown_suppliers = set(bundle.inventory.supplier.astype(str)) - suppliers
        if unknown_suppliers: errors.append(f"inventory.supplier: {len(unknown_suppliers)} supplier(s) are absent from suppliers table, including {', '.join(sorted(unknown_suppliers)[:5])}.")
    if bundle.stock_history is not None:
        sh = bundle.stock_history
        for col in ("date", "sku", "in_stock"):
            if col not in sh: errors.append(f"stock_history: missing required column {col}.")
        if "date" in sh and pd.to_datetime(sh.date, errors="coerce").isna().any(): errors.append("stock_history.date: contains invalid dates.")
        if "in_stock" in sh and not sh.in_stock.astype(str).str.lower().isin(["true", "false", "1", "0", "yes", "no"]).all(): errors.append("stock_history.in_stock: use true/false, 1/0, or yes/no.")
        if "sku" in sh and "sku" in bundle.inventory:
            unknown = set(sh.sku.astype(str)) - set(bundle.inventory.sku.astype(str))
            if unknown: errors.append(f"stock_history.sku: SKU(s) are absent from inventory, including {', '.join(sorted(unknown)[:5])}.")
    return errors

