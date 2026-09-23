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
        rec["unit"] = rec.get("unit", "").fillna("не указана") if hasattr(rec.get("unit", ""), "fillna") else "не указана"
        supplier = rec.groupby(["supplier", "unit"], as_index=False).agg(sku_count=("sku", "nunique"), proposed_units=("recommended_order", "sum"))
    else:
        supplier = pd.DataFrame(columns=["supplier", "sku_count", "proposed_units"])
    with pd.ExcelWriter(out, engine="openpyxl") as writer:
        safe_frame(rec).to_excel(writer, sheet_name="Recommendations", index=False)
        safe_frame(supplier).to_excel(writer, sheet_name="Supplier_Summary", index=False)
        safe_frame(draft).to_excel(writer, sheet_name="Purchase_Draft", index=False)
        safe_frame(quality).to_excel(writer, sheet_name="Data_Quality", index=False)
        safe_frame(pd.DataFrame([{"key": k, "value": json.dumps(v, ensure_ascii=False, default=str) if isinstance(v, (dict, list)) else str(v)} for k,v in result.metadata.items()])).to_excel(writer, sheet_name="Run_Metadata", index=False)
        if "supplier" in draft:
            used={"Recommendations","Supplier_Summary","Purchase_Draft","Data_Quality","Run_Metadata"}
            for supplier_name, group in draft.groupby("supplier",sort=True):
                base=re.sub(r"[\\/*?:\[\]]","_",str(supplier_name)).strip()[:26] or "Supplier"
                sheet=base; counter=2
                while sheet in used:
                    sheet=f"{base[:23]}_{counter}"; counter+=1
                used.add(sheet)
                safe_frame(group).to_excel(writer,sheet_name=sheet,index=False)
        for ws in writer.book.worksheets:
            ws.freeze_panes = "A2"
            ws.auto_filter.ref = ws.dimensions
            for col in ws.columns:
                width = min(48, max(12, max((len(str(cell.value or "")) for cell in col[:100]), default=10) + 2))
                ws.column_dimensions[col[0].column_letter].width = width
    return out.getvalue()

