from io import BytesIO
import pandas as pd
from contracts import AnalysisResult
from frontend.exports import csv_bytes, workbook_bytes

def test_spreadsheet_formula_injection_and_workbook_reopens():
    frame=pd.DataFrame({"sku":["=1+1"],"reason":["@cmd"]})
    assert b"'=1+1" in csv_bytes(frame)
    result=AnalysisResult(frame)
    payload=workbook_bytes(result,frame,pd.DataFrame({"check":["ok"]}))
    book=pd.ExcelFile(BytesIO(payload)); assert "Recommendations" in book.sheet_names


def test_workbook_groups_units_and_adds_supplier_draft_sheets():
    recommendations=pd.DataFrame({
        "sku":["A","B"],"supplier":["Поставщик 1","Поставщик 1"],
        "unit":["шт","м"],"recommended_order":[10,20],
    })
    draft=recommendations.assign(final_quantity=[10,20])
    result=AnalysisResult(recommendations,metadata={"run_id":"=unsafe"})
    payload=workbook_bytes(result,draft,pd.DataFrame({"check":["ok"]}))
    book=pd.ExcelFile(BytesIO(payload))
    summary=pd.read_excel(book,"Supplier_Summary")
    metadata=pd.read_excel(book,"Run_Metadata")
    assert len(summary) == 2
    assert "Поставщик 1" in book.sheet_names
    assert metadata.loc[metadata.key == "run_id","value"].iloc[0] == "'=unsafe"

