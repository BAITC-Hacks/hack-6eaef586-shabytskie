from frontend.exports import csv_bytes
import pandas as pd

def test_manual_edits_are_keyed_by_sku_and_not_row_number():
    review={("run-a","SKU-02"):"edited"}
    rows=pd.DataFrame({"sku":["SKU-01","SKU-02"]})
    rows=rows.iloc[::-1]
    assert review[("run-a",str(rows.iloc[0].sku))]=="edited"


