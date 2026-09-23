# StockPilot

StockPilot is a local Streamlit purchasing dashboard for the Elektrokomplekt inventory-replenishment case. It supports file validation, provider integration, a transparent synthetic/demo provider, manager review and purchase-draft exports. It does not send orders or write to an ERP.

## Run locally

Python 3.11 or newer is recommended. From the repository root:

```bash
python -m venv .venv
# Windows PowerShell: .venv\Scripts\Activate.ps1
# macOS/Linux: source .venv/bin/activate
pip install -r requirements.txt
streamlit run app.py
```

In the sidebar load synthetic data, then calculate recommendations. To run the test suite: `pytest -q`.

## Inputs

Use one `.xlsx` workbook with sheets `sales`, `inventory`, `suppliers`, and optional `stock_history`, or upload the three required CSVs and optional stock-history CSV separately. Identifiers are read as text so leading zeroes are retained.

| Table | Required columns | Optional columns |
|---|---|---|
| sales | `date`, `sku`, `quantity` | `client_id` (anonymized), `price`, `warehouse`; additional fields are preserved for the forecasting provider |
| inventory | `sku`, `product_name`, `category`, `supplier`, `current_stock`, `in_transit` | `growth_rate`, `unit`, `unit_cost`, `currency`, `moq`, `order_multiple` |
| suppliers | `supplier`, `lead_time_days` | — |
| stock_history | `date`, `sku`, `in_stock` | — |

MVP assumes one active supplier per SKU and unique inventory/supplier keys. Sales rows are treated as the supplied transaction granularity; duplicate transactions are not silently aggregated by validation. Negative sales, returns and invalid quantities are reported, never rewritten as zero. Input validation is separate from demand preprocessing. Files stay in memory in the dashboard session.

Templates can be downloaded from the sidebar. If stock history is absent, the app says stockout correction is unavailable; zero sales alone never imply a stockout.

Use anonymized values in `client_id`. The validator blocks common direct personal-data columns such as customer name, phone, email, IIN and address. Without `client_id`, the integrated engine can still limit daily outliers, but it cannot verify a one-off large order by one customer.

## Engines and calculations

**Demo engine** works without teammate modules or API keys. It uses the latest 28 calendar days' average sales, an optional recent growth adjustment, and transparent replenishment arithmetic. With an aggregate inventory transit quantity it assumes goods arrive within the protection horizon. It does not claim to model seasonality, detect production anomalies, correct stockouts, or establish forecast accuracy. MOQ and order-multiple rounding apply only when supplied and the raw order is positive.

**Integrated engine** requires the forecasting and replenishment providers described in [docs/integration_contract.md](docs/integration_contract.md). It calls Person 1 first, then Person 2. Missing or incompatible modules surface an actionable error; there is no silent demo fallback. The existing `src.pipeline` remains a separate command-line forecast report until the forecasting workstream adds the provider bridge.

## Manager review and exports

Review selections, editable quantities and comments live in Streamlit session state, keyed by calculation run and SKU. Any input or setting change makes the previous result stale and blocks exports until recalculation. The manager must confirm the draft before approved-lines export. This is a local demo interaction, not authentication or a durable audit trail. Downloading exports does not submit an order. Spreadsheet-formula-like text is sanitized.

Exports include visible-recommendation CSV and an Excel workbook with recommendations, supplier summary, purchase draft, data quality and run metadata. Mixed currencies are never totaled together; missing prices are not treated as zero.

## Team connection points

- Person 1: forecasting provider `forecast(data: InputBundle, settings: AnalysisSettings)`.
- Person 2: replenishment provider `recommend(data, settings, forecast_bundle)`.
- Integration adapter: `integration/adapter.py` is the only frontend-facing provider boundary.

See [docs/integration_contract.md](docs/integration_contract.md) and [docs/demo_script.md](docs/demo_script.md) for schemas, assumptions and a short presentation path.

