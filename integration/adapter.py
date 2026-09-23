"""Only module that knows provider paths and normalizes provider output."""
from __future__ import annotations
from datetime import datetime, timezone
import importlib
import uuid
import pandas as pd
from contracts import AnalysisResult, AnalysisSettings, InputBundle, Mode
from integration.demo_provider import run_demo


def _load_callable(module_name: str, function_name: str):
    try:
        module = importlib.import_module(module_name)
        return getattr(module, function_name)
    except (ImportError, AttributeError) as exc:
        raise RuntimeError(f"Integrated provider {module_name}.{function_name} is unavailable. Add the teammate module or inject providers in run_analysis().") from exc


def run_analysis(data: InputBundle, settings: AnalysisSettings, mode: Mode = "demo", *, forecaster=None, replenisher=None) -> AnalysisResult:
    if mode == "demo":
        result = run_demo(data, settings)
    elif mode == "integrated":
        forecaster = forecaster or _load_callable("integration.existing_provider", "forecast")
        replenisher = replenisher or _load_callable("integration.existing_provider", "recommend")
        try:
            forecast = forecaster(data, settings)
            result = replenisher(data, settings, forecast)
        except (ImportError, AttributeError, TypeError) as exc:
            raise RuntimeError(f"Integrated provider failed: {exc}. Check the repository backend and integration contract.") from exc
        if not isinstance(result, AnalysisResult):
            result = AnalysisResult(**result)
    else:
        raise ValueError(f"Unsupported engine mode: {mode}")
    result.metadata.update({"run_id": str(uuid.uuid4()), "calculated_at": datetime.now(timezone.utc).isoformat(),
        "settings": {"as_of_date": str(settings.as_of_date), "review_period_days": settings.review_period_days,
                     "safety_stock_days": settings.safety_stock_days}, "engine_mode": mode})
    return _normalize(result)


def _normalize(result: AnalysisResult) -> AnalysisResult:
    frame = result.recommendations.copy()
    aliases = {"forecast_demand": "forecast", "recommended_quantity": "recommended_order", "urgency": "priority", "explanation": "reason"}
    frame = frame.rename(columns={k: v for k, v in aliases.items() if k in frame and v not in frame})
    required = {"sku", "supplier", "forecast", "current_stock", "in_transit", "safety_stock", "recommended_order", "priority", "reason", "lead_time_days", "horizon_days"}
    missing = required - set(frame.columns)
    if missing:
        raise RuntimeError(f"Replenishment provider output is missing required columns: {', '.join(sorted(missing))}")
    frame["sku"] = frame.sku.astype(str)
    frame["supplier"] = frame.supplier.astype(str)
    frame["recommended_order"] = pd.to_numeric(frame.recommended_order, errors="raise")
    if (frame.recommended_order < 0).any():
        raise RuntimeError("Provider returned negative recommended_order values.")
    result.recommendations = frame
    result.metadata.setdefault("warnings", [])
    return result

