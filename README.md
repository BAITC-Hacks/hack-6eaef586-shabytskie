# Электрокомплект: demand forecasting MVP

Manual procurement planning risks overstock and stockouts. This module cleans sales history, caps unusual bulk purchases, estimates stockout losses, and forecasts regular SKU demand. It **does not calculate supplier order quantities**.

## Run

Python 3.11+ is required. From the project root:

```bash
python3.11 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
python -m src.generate_synthetic_data
python main.py --input data/raw/synthetic_sales.csv --forecast-days 30 --chart-sku SKU001
python main.py --input data/raw/sales.xlsx --forecast-days 14
python -m unittest discover -s tests -v
python -m src.demonstrate
```

The synthetic example has 20 SKUs, 400 days, 16,000 rows, weekly and annual cycles, growing/declining products, stockouts, 350-unit client orders, four suppliers, categories, stock and transit snapshots. Its two `synthetic_*` columns are audit truth and are never model inputs. The demonstration writes controlled calendar/growth feature sensitivity and a recent 1,000-unit order perturbation to `outputs/synthetic_checks.json`.

Optional `--cv-splits 3` uses expanding chronological folds. `--output-dir PATH` changes report/forecast location; audits and fitted models retain their standard locations. Horizon accepts any positive integer, including 7, 14, 30 and 60. Horizon begins after the latest input date, not the computer's current date.

## Architecture

```text
main.py
requirements.txt
src/
  config.py                    # aliases, reproducible settings
  data_loader.py               # CSV/XLSX, mapping and validation
  preprocessing.py             # cleaning, daily aggregation
  anomaly_detection.py         # client caps, historical SKU IQR
  stockout.py                  # historical lost-demand estimates
  feature_engineering.py       # lag, rolling, calendar, growth
  forecasting.py               # estimator factory and recursive forecast
  evaluation.py                # date splits, baseline, MAE/RMSE/MAPE
  pipeline.py                  # orchestration, exports, charts
  generate_synthetic_data.py
  demonstrate.py
  __init__.py
data/raw/
data/processed/
models/
outputs/charts/
tests/test_pipeline.py
```

## Input contract

One row is a sales transaction or a dated stock snapshot with quantity zero. Required fields are `date`, `sku`, `quantity`; missing fields produce a clear error. Use ISO dates (YYYY-MM-DD) for unambiguous parsing. Identifiers are read as text; Excel numeric cells cannot preserve leading zeroes that exist only in cell formatting.

| Canonical field | Accepted examples | Status |
|---|---|---|
| date | sale_date, Дата, Дата продажи | required |
| sku | article, product_id, Артикул, Код товара | required |
| quantity | qty, Количество, Продажи | required |
| client_id | customer_id, Клиент, ID клиента | optional; enables client detection |
| product_name | name, Наименование, Название товара | optional |
| stock | inventory, current_stock, Остаток, Остаток на дату | optional dated inventory |
| stockout_flag | stockout, Нет в наличии | optional explicit indicator |
| price / warehouse | Цена / Склад | optional |
| supplier / category | Поставщик / Категория | optional |
| lead_time_days | lead_time, Срок поставки | optional |
| in_transit | Товар в пути, В пути | optional |

Mapping ignores case/outer spaces; conflicting aliases are rejected. Unknown extra columns remain in the transaction audit but are excluded from model inputs. Missing optional fields are logged. Invalid dates, missing identifiers, invalid quantities and negative returns are dropped with a count. Negative/invalid optional numeric values become missing. Exact duplicate rows are removed; provide a transaction ID to distinguish otherwise identical legitimate transactions. Product metadata carries forward only. Stockouts accept true/false, 1/0, yes/no, да/нет.

## Methodology

1. Aggregate each client's SKU purchases per date. Compare against **earlier dates'** SKU client-order distribution using IQR and median absolute deviation; after seven samples, cap extreme totals and proportionally allocate the cap to transactions. Record raw and cleaned quantities and count orders once.
2. Aggregate daily demand, retain zero days, and fill missing calendar days from each SKU's first observation to the global latest date. Compute each warehouse's last inventory snapshot per date and sum warehouses, avoiding repeated transaction-level stock counts. A positive explicit stockout flag or total stock zero marks the SKU-day.
3. Cap daily demand at prior 90-calendar-day Q3 + 1.5×IQR, with at least seven non-stockout observations. Anomaly corrections use only previous data and preserve raw quantity.
4. Estimate stockout demand from the prior 14 calendar days' median, excluding stockout and anomalous days. Fall back to expanding historical median. Lost demand is max(expected − cleaned sales, 0). Cold starts without reliable history have zero estimated loss and `stockout_estimate_available=False`.
5. Calendar features include weekday, ISO week, month, quarter, day of month and annual sine/cosine. Lags are 1/7/14/28; rolling means 7/14/30, median 7, standard deviations 7/30. Each feature uses history ending **before** its target day, equivalent to `shift(1)`. Growth compares the latest 30 days with the prior available period, requires more than 30 days, and is clipped to [-1, 10]. Weekly seasonality is a descriptive detrended lag-7 correlation; detection threshold 0.3 after 56 days.
6. Train one global RandomForest (100 trees, seed 42) with sparse one-hot SKU encoding and numeric imputation. Replacing `train_model` with an estimator exposing `predict` keeps the forecasting interface intact. Price/supplier/stock/transit are deliberately excluded from predictors: their future values are not known.
7. Split **unique dates** 80/20, train on earlier dates only, recursively forecast the entire holdout without actual-demand feedback. Optional TimeSeriesSplit also splits dates. The recursive 7-day moving-average baseline uses exactly the same information. Short-history SKUs use recent means. SKUs first appearing in holdout cannot be evaluated at the earlier origin.
8. Report MAE, RMSE and MAPE (percent, positive actuals only, null if no valid denominators). Primary metrics exclude censored and flagged abnormal targets; corrected-target metrics are labeled diagnostics. Select the forest per SKU only when it beats baseline MAE with at least seven reliable validation observations. Refit on all history and sum recursive daily predictions over the requested horizon.

## Outputs and integration

`outputs/forecast_output.csv` contains one row per SKU:

| Fields | Meaning |
|---|---|
| sku, product_name | product identifiers |
| forecast_demand, forecast_horizon_days, forecast_as_of | total regular-demand forecast, horizon and historical cutoff |
| growth_rate, trend_direction | fractional growth; increasing/stable/decreasing at ±10% |
| seasonality_detected, seasonality_strength | descriptive weekly pattern indicator |
| estimated_lost_demand | historical total across the supplied dataset, not future demand |
| outliers_detected, large_client_orders_detected | historical daily-outlier and client-order counts; can overlap |
| model_used | random_forest, baseline_7d or fallback_7d |
| baseline_mae, model_mae | per-SKU validation MAE; blank when unavailable |
| forecast_confidence | low/medium heuristic based on history, sample count and normalized validation error |
| forecast_reason | human-readable explanation |
| supplier, category, lead_time_days, stock, in_transit, price | latest available metadata if supplied |

`daily_forecast.csv` provides dated predictions for lead-time alignment. The procurement service joins by SKU and combines the demand forecast with **fresh** inventory, in-transit shipments, safety stock and supplier lead time. Metadata snapshots in this file may be stale; no final order quantity is emitted.

Other artifacts: `metrics.json`, `validation_predictions.csv`, `data/processed/transactions_audit.csv`, `data/processed/daily_demand.csv`, `models/demand_model.joblib` (model/config/selection/cutoff), optional SKU chart. Reloaded models require compatible dependency versions and a corrected demand history to build inference features; never load untrusted joblib files.

## Assumptions and limitations

- Missing calendar days imply zero recorded sales and unknown stock. This requires a complete sales extract; missing feeds, closed days or discontinued products need explicit handling upstream.
- Inventory is assumed to be a repeated end-of-day warehouse snapshot, not inventory movements. Without warehouse IDs, stock is a single SKU snapshot. Separate inventory/supplier tables must be joined upstream by effective date. Multiple suppliers per SKU are reduced to the last nonmissing value; richer supplier allocation belongs downstream.
- A warehouse-level explicit stockout can mark the whole SKU-day. Partial-warehouse availability needs a warehouse-level model for higher precision.
- Stockout corrections are estimates. The system cannot recover censored demand with no history. Robust caps may suppress genuine demand shifts, promotions or intermittent demand spikes; review audit flags. First-week anomalies cannot be identified reliably.
- Confidence labels are **not calibrated probabilities or intervals**. Model selection uses the validation set, so selected-model performance needs a separate untouched test period before production claims. Default holdout spans 20% of history, potentially longer than the requested forecast horizon.
- Weekly seasonality is reported; annual features can help prediction but one year is insufficient to establish repeatable annual patterns. Forests do not extrapolate long-term growth well; recursive long-horizon error accumulates.
- One global model avoids per-SKU model explosion; daily expansion is O(SKUs × days), and sparse one-hot encoding avoids a dense SKU matrix. 1,000+ SKUs are supported by the architecture but not benchmarked here; large transaction extracts may need batching/vectorized client detection.
- Quantities must use consistent units per SKU; returns are excluded, not netted. No external prices, calendars or real company data were used.

## Verified synthetic run

Python 3.12; 80/20 date split; 1,568 reliable validation SKU-days (32 flagged days excluded). These are synthetic-data results, not production accuracy claims.

| Model | MAE | RMSE | MAPE |
|---|---:|---:|---:|
| Recursive 7-day mean | 8.8483 | 12.1760 | 33.91% |
| Global RandomForest | 5.9552 | 8.3193 | 22.29% |

The audit detected 20 bulk client orders, 93 daily outliers and 260 stockout SKU-days; it estimated 7,992.5 lost units. Holding history fixed, weekday feature changes produced a 2.41-unit daily prediction range. Changing only growth from -30% to +30% changed the prediction from 26.10 to 25.12: the forest uses growth but is **not monotonic**, and this artificial feature intervention does not establish a causal growth response. A recent injected 1,000-unit order changed the cleaned-history 30-day forecast from 664.34 to 665.45 (+0.17%) with the fitted model held fixed.
