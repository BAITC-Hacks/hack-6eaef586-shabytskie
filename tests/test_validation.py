import pandas as pd
from contracts import InputBundle
from frontend.validation import validate_bundle

def valid():
    return InputBundle(pd.DataFrame({"date":["2025-01-01"],"sku":["001"],"quantity":[1]}),
        pd.DataFrame({"sku":["001"],"product_name":["Cable"],"category":["Wire"],"supplier":["S1"],"current_stock":[1],"in_transit":[0]}),
        pd.DataFrame({"supplier":["S1"],"lead_time_days":[5]}))

def test_valid_and_leading_zero():
    b=valid(); assert validate_bundle(b)==[]; assert str(b.inventory.sku.iloc[0])=="001"

def test_missing_and_negative_are_reported():
    b=valid(); b.sales=b.sales.drop(columns="quantity"); assert any("quantity" in e for e in validate_bundle(b))
    b=valid(); b.sales.loc[0,"quantity"]=-2; assert any("negative" in e for e in validate_bundle(b))

