"""Clearly approximate, deterministic provider for demos and UI development."""
from __future__ import annotations
import numpy as np
import pandas as pd
from contracts import AnalysisSettings, AnalysisResult, InputBundle


def run_demo(data: InputBundle, settings: AnalysisSettings, origin: str = "uploaded") -> AnalysisResult:
    sales = data.sales.copy()
    sales["date"] = pd.to_datetime(sales["date"])
    inv = data.inventory.merge(data.suppliers, on="supplier", how="left", validate="many_to_one")
    as_of = pd.Timestamp(settings.as_of_date)
    sales = sales[sales.date <= as_of]
    end = sales.date.max()
    recs, forecast_rows = [], []
    daily = sales[(sales.date <= end) & (sales.date >= end - pd.Timedelta(days=55))].groupby(["sku", "date"], as_index=False).quantity.sum()
    for row in inv.itertuples(index=False):
        hist = daily[daily.sku == row.sku].set_index("date").quantity.asfreq("D", fill_value=0)
        avg = float(hist.tail(28).mean()) if len(hist) else 0.0
        prior = float(hist.tail(56).head(28).mean()) if len(hist) >= 29 else avg
        supplied_growth = getattr(row, "growth_rate", np.nan)
        observed_growth = float(np.clip(avg / prior - 1, -0.5, 1.0)) if prior > 0 else 0.0
        growth = settings.expected_growth_override if settings.expected_growth_override is not None else (float(supplied_growth) if pd.notna(supplied_growth) else observed_growth)
        daily_rate = max(0.0, avg * (1 + growth))
        horizon = int(row.lead_time_days) + settings.review_period_days
        forecast = daily_rate * horizon
        safety = daily_rate * settings.safety_stock_days
        raw = max(0.0, forecast + safety - float(row.current_stock) - float(row.in_transit))
        order = raw
        if raw > 0 and pd.notna(getattr(row, "moq", np.nan)):
            order = max(order, float(row.moq))
        multiple = getattr(row, "order_multiple", np.nan)
        if raw > 0 and pd.notna(multiple) and float(multiple) > 0:
            order = np.ceil(order / float(multiple)) * float(multiple)
        priority = "HIGH" if order > 0 and float(row.current_stock) < daily_rate * int(row.lead_time_days) else "MEDIUM" if order > 0 else "LOW"
        reason = (f"Demo baseline: {daily_rate:.2f} units/day × {horizon} days = {forecast:.2f} forecast; "
                  f"safety stock {safety:.2f} − stock {row.current_stock:g} − in transit {row.in_transit:g}. "
                  f"Growth assumption {growth:+.1%}; order rounded only by supplied MOQ/pack rules.")
        recs.append({"sku": str(row.sku), "product_name": row.product_name, "category": row.category,
                     "supplier": str(row.supplier), "forecast": forecast, "current_stock": float(row.current_stock),
                     "in_transit": float(row.in_transit), "safety_stock": safety, "recommended_order": float(order),
                     "priority": priority, "reason": reason, "lead_time_days": int(row.lead_time_days),
                     "horizon_days": horizon, "unit": getattr(row, "unit", ""),
                     "unit_cost": getattr(row, "unit_cost", np.nan), "currency": getattr(row, "currency", ""),
                     "trend": 1 + growth, "is_seasonal": None, "lost_demand": np.nan,
                     "calculation_breakdown": {"forecast": forecast, "safety_stock": safety,
                        "current_stock": float(row.current_stock), "in_transit": float(row.in_transit),
                        "raw_order": raw, "final_order": float(order)}})
        for day in pd.date_range(as_of + pd.Timedelta(days=1), periods=max(1, horizon)):
            forecast_rows.append({"date": day, "sku": str(row.sku), "predicted_demand": daily_rate})
    recommendations = pd.DataFrame(recs)
    recommendations["estimated_order_cost"] = recommendations.recommended_order * recommendations.unit_cost
    quality = pd.DataFrame([{"check": "Provider", "status": "Approximate demo baseline", "details": "Trailing 28-day average with an optional recent-growth adjustment; no ML or production corrections."},
                            {"check": "Stockout correction", "status": "Unavailable", "details": "Demo provider does not estimate censored demand. Historical stock flags are shown as input only."},
                            {"check": "Seasonality", "status": "Unavailable", "details": "Demo provider does not fit a seasonal model."},
                            {"check": "Transit timing", "status": "Assumption", "details": "Aggregate goods in transit are assumed to arrive within the protection horizon."}])
    return AnalysisResult(recommendations, history=sales.rename(columns={"quantity": "actual_sales"}),
        forecast_series=pd.DataFrame(forecast_rows), quality_report=quality,
        metadata={"provider": "demo_provider", "description": "Approximate trailing-average demo; no production forecasting or replenishment provider.", "data_origin": origin, "engine_mode": "demo", "warnings": ["Aggregate in-transit quantities are assumed to arrive within the protection horizon.", "Demo forecasts do not correct anomalies, stockouts, or seasonality."]})

