"""Traceable spreadsheet-safe recommendation exports."""
from io import BytesIO
import json
import re
import pandas as pd

def _safe_text(value):
    if isinstance(value, str) and re.match(r"^[\s]*[=+@\-]", value): return "'" + value
    return value

def safe_frame(df: pd.DataFrame) -> pd.DataFrame:
    out = df.copy()
    for col in out.select_dtypes(include=["object", "string"]).columns:
        out[col] = out[col].map(_safe_text)
    return out

def csv_bytes(df: pd.DataFrame) -> bytes:
    return safe_frame(df).to_csv(index=False).encode("utf-8-sig")

def workbook_bytes(result, draft: pd.DataFrame, quality: pd.DataFrame) -> bytes:
    out = BytesIO()
    rec = result.recommendations.copy()
    rec["export_status"] = "DRAFT"
    if {"supplier", "sku", "recommended_order"} <= set(rec.columns):
        supplier = rec.groupby("supplier", as_index=False).agg(sku_count=("sku", "nunique"), proposed_units=("recommended_order", "sum"))
    else:
        supplier = pd.DataFrame(columns=["supplier", "sku_count", "proposed_units"])
    with pd.ExcelWriter(out, engine="openpyxl") as writer:
        safe_frame(rec).to_excel(writer, sheet_name="Recommendations", index=False)
        safe_frame(supplier).to_excel(writer, sheet_name="Supplier_Summary", index=False)
        safe_frame(draft).to_excel(writer, sheet_name="Purchase_Draft", index=False)
        safe_frame(quality).to_excel(writer, sheet_name="Data_Quality", index=False)
        pd.DataFrame([{"key": k, "value": json.dumps(v, ensure_ascii=False, default=str) if isinstance(v, (dict, list)) else str(v)} for k,v in result.metadata.items()]).to_excel(writer, sheet_name="Run_Metadata", index=False)
        for ws in writer.book.worksheets:
            ws.freeze_panes = "A2"
            ws.auto_filter.ref = ws.dimensions
            for col in ws.columns:
                width = min(48, max(12, max((len(str(cell.value or "")) for cell in col[:100]), default=10) + 2))
                ws.column_dimensions[col[0].column_letter].width = width
    return out.getvalue()

