import pandas as pd
from contracts import AnalysisSettings
from data.generate_demo import generate_demo
from integration.demo_provider import run_demo

def test_deterministic_and_inventory_monotone():
    b=generate_demo(); s=AnalysisSettings(pd.Timestamp("2025-10-01").date())
    a=run_demo(b,s).recommendations; again=run_demo(b,s).recommendations
    pd.testing.assert_frame_equal(a,again)
    b.inventory.loc[0,"current_stock"]+=10000
    larger=run_demo(b,s).recommendations
    assert larger.loc[0,"recommended_order"]<=a.loc[0,"recommended_order"]

def test_zero_demand_has_no_negative_order():
    b=generate_demo(skus=2,days=30); b.sales.loc[b.sales.sku=="SKU-001","quantity"]=0
    b.inventory.loc[b.inventory.sku=="SKU-001","current_stock"]=10000
    result=run_demo(b,AnalysisSettings(pd.Timestamp("2025-02-01").date())).recommendations
    assert (result.recommended_order>=0).all()

