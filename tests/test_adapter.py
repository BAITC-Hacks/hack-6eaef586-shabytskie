import pandas as pd
import pytest
from contracts import AnalysisResult, AnalysisSettings, InputBundle
from integration.adapter import run_analysis

def bundle():
    return InputBundle(pd.DataFrame({"date":["2025-01-01"],"sku":["01"],"quantity":[1]}),
        pd.DataFrame({"sku":["01"],"product_name":["Cable"],"category":["Wire"],"supplier":["S1"],"current_stock":[0],"in_transit":[0]}),
        pd.DataFrame({"supplier":["S1"],"lead_time_days":[2]}))

def test_injected_providers_flow_in_order():
    calls=[]
    def forecast(data,settings): calls.append("forecast"); return {"summary":pd.DataFrame()}
    def recommend(data,settings,fc):
        calls.append("recommend")
        return AnalysisResult(pd.DataFrame([{"sku":"01","supplier":"S1","forecast":1,"current_stock":0,"in_transit":0,"safety_stock":0,"recommended_order":1,"priority":"HIGH","reason":"shortage","lead_time_days":2,"horizon_days":9}]))
    result=run_analysis(bundle(),AnalysisSettings(pd.Timestamp("2025-01-02").date()),"integrated",forecaster=forecast,replenisher=recommend)
    assert calls==["forecast","recommend"]; assert result.recommendations.sku.iloc[0]=="01"

def test_missing_provider_does_not_fallback():
    with pytest.raises(RuntimeError,match="Integrated provider failed"):
        run_analysis(bundle(),AnalysisSettings(pd.Timestamp("2025-01-02").date()),"integrated")

