"""Bridge the repository's forecasting and ordering modules to UI contracts."""
from __future__ import annotations
import numpy as np
import pandas as pd
from contracts import AnalysisResult, AnalysisSettings, ForecastBundle, InputBundle


def _tables(data: InputBundle, settings: AnalysisSettings):
    inventory = data.inventory.copy()
    catalog = inventory.rename(columns={"unit_cost": "price", "moq": "min_order_qty"})
    stock = inventory.rename(columns={"current_stock": "stock"})[["sku", "stock", "in_transit"]]
    suppliers = data.suppliers.copy()
    if "moq" in suppliers and "min_order_qty" not in suppliers:
        suppliers = suppliers.rename(columns={"moq": "min_order_qty"})
    growth = inventory[[c for c in ["sku", "category", "growth_rate"] if c in inventory]].copy()
    if settings.expected_growth_override is not None:
        growth["growth_rate"] = settings.expected_growth_override
    growth = growth.rename(columns={"growth_rate": "growth_forecast"}) if "growth_rate" in growth else None
    stockouts = None
    if settings.enable_stockout_correction and data.stock_history is not None:
        unavailable = data.stock_history.copy()
        values = unavailable.in_stock.astype(str).str.strip().str.casefold()
        unavailable = unavailable[~values.isin(["true", "1", "1.0", "yes", "да"])]
        if len(unavailable):
            stockouts = unavailable.rename(columns={"date": "date_from"})[["sku", "date_from"]]
            stockouts["date_to"] = stockouts.date_from
    return catalog, suppliers, stock, growth, stockouts


def forecast(data: InputBundle, settings: AnalysisSettings) -> ForecastBundle:
    from src.config import Config
    from src.evaluation import evaluate
    from src.forecasting import predict_future, train_model
    from src.pipeline import build_output, prepare

    catalog, suppliers, stock, growth, stockouts = _tables(data, settings)
    frame = data.sales.copy()
    frame = frame[pd.to_datetime(frame.date, errors="coerce") <= pd.Timestamp(settings.as_of_date)].copy()
    if frame.empty:
        raise ValueError("No sales rows exist on or before the selected as-of date.")
    metadata_cols = [c for c in ["sku", "product_name", "category", "supplier", "unit_cost"] if c in data.inventory]
    frame = frame.merge(data.inventory[metadata_cols].rename(columns={"unit_cost": "price"}), on="sku", how="left", validate="many_to_one")
    config = Config(forecast_days=max(30, int(suppliers.lead_time_days.max()) + settings.review_period_days),
                    review_period_days=settings.review_period_days,
                    service_level_z=1.65 if settings.safety_stock_days else 0.0)
    _, daily = prepare(frame, stockouts if settings.enable_stockout_correction else None)
    validation, report = evaluate(daily, config)
    methods = {}
    if not validation.empty:
        reliable = validation[~(validation.stockout_flag | validation.is_outlier | validation.is_large_client_order)]
        for sku, group in reliable.groupby("sku"):
            model_error = (group.quantity - group.model_prediction).abs().mean()
            baseline_error = (group.quantity - group.baseline_prediction).abs().mean()
            methods[sku] = "random_forest" if len(group) >= 7 and model_error < baseline_error else "baseline_7d"
    for sku in daily.sku.unique(): methods.setdefault(sku, "baseline_7d")
    model = train_model(daily, config)
    future = predict_future(daily, config.forecast_days, model, config, methods)
    summary = build_output(daily, future, validation, config)
    normalized = summary.rename(columns={"forecast_demand": "forecast", "forecast_horizon_days": "horizon_days",
        "estimated_lost_demand": "lost_demand", "seasonality_detected": "is_seasonal",
        "large_client_orders_detected": "anomalies_removed"})
    history = daily.rename(columns={"quantity": "actual_sales", "stockout_flag": "is_stockout"})
    daily_forecast = future.rename(columns={"prediction": "predicted_demand"})
    quality = pd.DataFrame([{"check": key, "value": str(value)} for key, value in report.items()])
    return ForecastBundle(normalized, daily_forecast, history, quality,
        {"native_daily": daily, "native_future": future, "native_summary": summary,
         "catalog": catalog, "suppliers": suppliers, "stock": stock, "growth": growth,
         "config": config, "method": "Repository RandomForest/baseline forecast and stockout/anomaly pipeline"})


def recommend(data: InputBundle, settings: AnalysisSettings, forecast_bundle: ForecastBundle) -> AnalysisResult:
    from src.ordering import recommend_orders

    meta = forecast_bundle.metadata
    orders = recommend_orders(meta["native_daily"], meta["native_future"], meta["config"],
        meta["native_summary"], meta["catalog"], meta["suppliers"], meta["stock"], meta["growth"])
    priority = {"критично": "HIGH", "высокая": "MEDIUM", "плановая": "LOW", "не требуется": "LOW"}
    recs = orders.rename(columns={"recommended_qty": "recommended_order", "urgency": "priority",
        "coverage_days": "horizon_days", "demand_for_coverage": "forecast", "stock": "current_stock",
        "lost_demand_adjustment": "lost_demand", "one_off_orders_removed": "anomalies_removed",
        "order_value": "estimated_order_cost"})
    recs["priority"] = recs.priority.map(priority).fillna(recs.priority.astype(str).str.upper())
    recs["unit_cost"] = recs.get("price", np.nan)
    recs["currency"] = data.inventory.set_index("sku").currency.reindex(recs.sku).to_numpy() if "currency" in data.inventory else None
    recs["unit"] = data.inventory.set_index("sku").unit.reindex(recs.sku).to_numpy() if "unit" in data.inventory else None
    recs["calculation_breakdown"] = recs.apply(lambda r: {"model_demand": r.get("model_demand"),
        "seasonal_factor": r.get("seasonal_factor"), "trend_factor": r.get("trend_factor"),
        "planned_growth": r.get("planned_growth"), "forecast": r.get("forecast"),
        "safety_stock": r.get("safety_stock"), "current_stock": r.get("current_stock"),
        "in_transit": r.get("in_transit"), "net_need": r.get("net_need"),
        "recommended_order": r.get("recommended_order")}, axis=1)
    return AnalysisResult(recs, forecast_bundle.corrected_history, forecast_bundle.daily_forecast,
        forecast_bundle.quality_report, {"provider": "existing_repository_backend",
        "description": meta["method"], "warnings": []})

