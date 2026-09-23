# StockPilot UI and integration workstream

This addendum is intended to be appended to the existing repository README. It preserves the forecasting methodology already documented there.

Install the updated `requirements.txt`, then launch with `streamlit run app.py`. Use the sidebar to load synthetic data or upload an `.xlsx` workbook (`sales`, `inventory`, `suppliers`, optional `stock_history` sheets) or the equivalent CSV files. Validate before calculating. The app offers a Russian-language purchasing dashboard, per-SKU history and forecast charts, supplier filters, editable manager review, and CSV/Excel exports.

The demo engine is deterministic and approximate: trailing 28-day mean, a recent growth estimate or explicit growth assumption, a transparent lead-time/review-horizon replenishment calculation, and optional MOQ/pack rounding. It does not run the production forecast model and does not claim stockout correction, seasonality, measured accuracy or savings. Integrated mode calls the repository's forecasting functions and then `src.ordering.recommend_orders` through `integration/existing_provider.py`. See `docs/integration_contract.md` for signatures and schemas.

Run `pytest -q` for frontend/integration checks. Manager review is session-local, requires explicit confirmation before approved-line export, and does not submit orders or modify an ERP. Exports are draft files only.

