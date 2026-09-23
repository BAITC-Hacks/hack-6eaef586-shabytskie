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

