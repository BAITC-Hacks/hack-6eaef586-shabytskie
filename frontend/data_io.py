"""Safe in-memory CSV/XLSX loading and downloadable input templates."""
from io import BytesIO
import pandas as pd
from contracts import InputBundle

TEMPLATES = {
"sales": pd.DataFrame(columns=["date", "sku", "quantity"]),
"inventory": pd.DataFrame(columns=["sku", "product_name", "category", "supplier", "current_stock", "in_transit", "growth_rate", "unit", "unit_cost", "currency", "moq", "order_multiple"]),
"suppliers": pd.DataFrame(columns=["supplier", "lead_time_days"]),
"stock_history": pd.DataFrame(columns=["date", "sku", "in_stock"])}

def read_table(file, table: str) -> pd.DataFrame:
    if file.name.lower().endswith(".csv"):
        return pd.read_csv(file, dtype={"sku": "string", "supplier": "string"}, encoding="utf-8-sig")
    raise ValueError("Upload CSV files here, or use the single-workbook uploader for Excel.")

def read_workbook(file) -> InputBundle:
    sheets = pd.read_excel(file, sheet_name=None, dtype={"sku": "string", "supplier": "string"}, engine="openpyxl")
    missing = {"sales", "inventory", "suppliers"} - set(sheets)
    if missing: raise ValueError("Excel workbook must contain sheets named sales, inventory, suppliers.")
    return InputBundle(sheets["sales"], sheets["inventory"], sheets["suppliers"], sheets.get("stock_history"))

def templates_xlsx() -> bytes:
    out = BytesIO()
    with pd.ExcelWriter(out, engine="openpyxl") as writer:
        for name, frame in TEMPLATES.items(): frame.to_excel(writer, sheet_name=name, index=False)
    return out.getvalue()

