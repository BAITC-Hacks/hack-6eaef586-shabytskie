# StockPilot integration contract

This document defines the boundary between the dashboard and the two calculation workstreams. The dashboard imports only `integration.adapter.run_analysis`; teammate module names and provider function signatures are isolated in that adapter.

## UI entry point

```python
run_analysis(data: InputBundle, settings: AnalysisSettings, mode: Literal["demo", "integrated"]) -> AnalysisResult
```

`InputBundle` has pandas DataFrames `sales`, `inventory`, `suppliers`, and optional `stock_history`. Internal identifiers are strings; dates are parseable date values; quantities remain numeric in the SKU's supplied unit. `AnalysisSettings` carries `as_of_date`, `review_period_days`, `safety_stock_days`, an optional fractional `expected_growth_override` (0.12 = 12%), and correction flags. `AnalysisResult` contains recommendations, history, forecast series, quality report and metadata. See `contracts.py` for exact dataclass declarations. The current repository bridge is `integration.existing_provider`: it calls the existing `src` forecasting pipeline first and `src.ordering.recommend_orders` second.

The adapter normalizes aliases and checks required recommendation fields. Integrated execution calls the forecasting provider first, then the replenishment provider. It never switches to demo output after an integration error. Providers may be passed to `run_analysis` as `forecaster=` and `replenisher=` while developing. The default callables are `integration.existing_provider.forecast` and `integration.existing_provider.recommend`.

## Person 1: forecasting provider

Signature expected by adapter:

```python
forecast(data: InputBundle, settings: AnalysisSettings) -> ForecastBundle | dict
```

`ForecastBundle` should contain:

| Frame | Required fields | Semantics |
|---|---|---|
| summary | `sku`, `forecast`, `horizon_days` | forecast is total expected demand for the stated horizon |
| daily_forecast | `date`, `sku`, `predicted_demand` | daily demand; bounds only when actually estimated |
| corrected_history | `date`, `sku`, `actual_sales` | optional `adjusted_demand`, `is_anomaly`, `is_stockout` |
| quality_report | provider-defined diagnostic columns | real checks/metrics only; no invented accuracy |

Optional summary diagnostics include `lost_demand` (historical unobserved demand, not a future backlog), `trend` (multiplier, e.g. 1.12), `is_seasonal`, and `anomalies_removed`. If stock history is absent, explain that stockout correction is unavailable. Preserve raw history and explain all corrections.

## Person 2: replenishment provider

Signature expected by adapter:

```python
recommend(data: InputBundle, settings: AnalysisSettings, forecast: ForecastBundle) -> AnalysisResult | dict
```

`recommendations` must include `sku`, `supplier`, `forecast`, `current_stock`, `in_transit`, `safety_stock`, `recommended_order`, `priority`, `reason`, `lead_time_days`, `horizon_days`. `priority` is HIGH, MEDIUM or LOW; it is a category, not a calibrated probability. Optional product, category, unit, cost/currency, lost-demand/trend/seasonality/anomaly fields and `calculation_breakdown` are displayed when supplied. Include history, forecast series, quality report and metadata where available.

For the proposed policy, `horizon_days = lead_time_days + review_period_days`. If the production service uses another policy, return its actual horizon explicitly. Document MOQ/order-multiple behavior and show its math. Do not interpret historical lost demand as an outstanding backlog. Do not subtract growth or seasonality a second time if already present in the forecast.

## Replacing the providers

1. Add importable modules exposing `forecast` and `recommend` at the paths above, or change only the path mapping in `integration/adapter.py`.
2. Have forecasting return the summary, daily series, corrected history and honest quality report.
3. Have replenishment consume that output and return required recommendation columns plus calculation reasons.
4. Run `pytest -q` and launch `streamlit run app.py`; select Integrated to exercise the real path.

Minimal example:

```python
from integration.adapter import run_analysis
result = run_analysis(bundle, settings, "integrated",
                      forecaster=forecast, replenisher=recommend)
```

## Assumptions and current gaps

- MVP assumes one active supplier per SKU. No multi-supplier optimization is implemented.
- Demo assumes aggregate in-transit units arrive within the protection horizon; no receipt-date risk is inferred.
- Demo uses a trailing 28-day mean and optional recent-growth factor. It has no advanced forecast model, stockout correction, or seasonal fit.
- `integration.existing_provider` adapts the current `src.pipeline` and `src.ordering` functions without changing those teammate modules. A future stable teammate API can replace this bridge only in `integration/adapter.py`.
- Manager approval is local session state, not identity verification or a durable audit log. Exports are files only; nothing is submitted to suppliers or an ERP.

