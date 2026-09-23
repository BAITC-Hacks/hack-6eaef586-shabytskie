"""End-to-end orchestration and procurement-service handoff artifacts."""
import json
import logging
from pathlib import Path
import joblib
import pandas as pd
from src.anomaly_detection import detect_client_orders, detect_daily_outliers
from src.config import Config
from src.data_loader import load_data
from src.evaluation import evaluate, metrics
from src.feature_engineering import feature_row, seasonality
from src.forecasting import predict_future, train_model
from src.preprocessing import aggregate_daily, clean_transactions
from src.stockout import correct_stockouts


def prepare(frame: pd.DataFrame) -> tuple[pd.DataFrame, pd.DataFrame]:
    """Keep transaction audit and fully corrected daily history."""
    transactions = detect_client_orders(clean_transactions(frame))
    daily = correct_stockouts(detect_daily_outliers(aggregate_daily(transactions)))
    return transactions, daily


def build_output(daily: pd.DataFrame, future: pd.DataFrame, validation: pd.DataFrame,
                 config: Config) -> pd.DataFrame:
    """Summarize expected horizon demand; never calculate procurement quantities."""
    rows = []
    for sku, group in daily.groupby('sku', sort=False):
        forecast = future[future.sku == sku]
        growth = feature_row(sku, group.date.max() + pd.Timedelta(days=1), group.adjusted_demand.tolist())['growth_rate']
        seasonal, strength = seasonality(group)
        row = dict(sku=sku, product_name=sku, forecast_demand=forecast.prediction.sum(),
                   forecast_horizon_days=config.forecast_days, growth_rate=growth,
                   trend_direction='increasing' if growth > .1 else 'decreasing' if growth < -.1 else 'stable',
                   seasonality_detected=seasonal, seasonality_strength=strength,
                   estimated_lost_demand=group.estimated_lost_demand.sum(),
                   outliers_detected=int(group.is_outlier.sum()),
                   large_client_orders_detected=int(group.large_client_orders_detected.sum()),
                   model_used=forecast.model_used.iloc[0], baseline_mae=None, model_mae=None,
                   forecast_confidence='low', forecast_as_of=group.date.max().date().isoformat())
        if not validation.empty:
            valid = validation[(validation.sku == sku) & ~(validation.stockout_flag | validation.is_outlier | validation.is_large_client_order)]
            if len(valid):
                row['baseline_mae'] = metrics(valid.quantity, valid.baseline_prediction)['mae']
                row['model_mae'] = metrics(valid.quantity, valid.model_prediction)['mae']
                error = row['model_mae'] if row['model_used'] == 'random_forest' else row['baseline_mae']
                if len(valid) >= 14 and len(group) >= 90 and error / max(valid.quantity.mean(), 1) < .5:
                    row['forecast_confidence'] = 'medium'
        for col in ['product_name', 'supplier', 'category', 'lead_time_days', 'stock', 'in_transit', 'price']:
            if col in group and group[col].notna().any():
                row[col] = group[col].dropna().iloc[-1]
        row['forecast_reason'] = (
            f"Forecast demand for the next {config.forecast_days} days is {row['forecast_demand']:.1f} units. "
            f"Recent 30-day demand changed by {growth:+.1%} versus the previous period. "
            f"Estimated historical lost demand: {row['estimated_lost_demand']:.1f} units. "
            f"Capped {row['large_client_orders_detected']} large client orders and {row['outliers_detected']} daily outliers. "
            f"Method: {row['model_used']}. Confidence is a heuristic, not a prediction interval.")
        rows.append(row)
    return pd.DataFrame(rows)


def save_chart(daily: pd.DataFrame, future: pd.DataFrame, validation: pd.DataFrame,
               sku: str, directory: Path) -> None:
    """Write a selected-SKU audit/forecast plot using a headless backend."""
    import matplotlib
    matplotlib.use('Agg')
    import matplotlib.pyplot as plt
    group = daily[daily.sku == sku]
    if group.empty:
        raise ValueError(f'Chart SKU {sku!r} does not exist')
    fig, ax = plt.subplots(figsize=(13, 5))
    for col in ['quantity', 'quantity_clean', 'adjusted_demand']:
        ax.plot(group.date, group[col], label=col, alpha=.7)
    f = future[future.sku == sku]
    ax.plot(f.date, f.prediction, label='future forecast')
    if not validation.empty:
        v = validation[validation.sku == sku]
        ax.plot(v.date, v.model_prediction, label='holdout model', linestyle='--')
    ax.set(title=f'Demand audit: {sku}', ylabel='Units per day')
    ax.legend()
    fig.tight_layout()
    directory.mkdir(parents=True, exist_ok=True)
    safe_name = ''.join(c if c.isalnum() or c in '-_' else '_' for c in sku)
    fig.savefig(directory / f'{safe_name}.png', dpi=130)
    plt.close(fig)


def run_pipeline(input_path: str | Path, config: Config, chart_sku: str | None = None,
                 cv_splits: int = 0) -> pd.DataFrame:
    """Load, correct, evaluate, refit, recursively forecast and persist artifacts."""
    for directory in [config.output_dir, config.processed_dir, config.model_dir]:
        directory.mkdir(parents=True, exist_ok=True)
    transactions, daily = prepare(load_data(input_path))
    logging.info('Found %d SKUs', daily.sku.nunique())
    logging.info('Detected %d SKU-level outliers', daily.is_outlier.sum())
    logging.info('Detected %d large single-client orders', daily.large_client_orders_detected.sum())
    logging.info('Found %d stockout SKU-days', daily.stockout_flag.sum())
    validation, report = evaluate(daily, config, cv_splits)
    methods = {}
    if not validation.empty:
        reliable = validation[~(validation.stockout_flag | validation.is_outlier | validation.is_large_client_order)]
        for sku, group in reliable.groupby('sku'):
            methods[sku] = 'random_forest' if len(group) >= 7 and (group.quantity - group.model_prediction).abs().mean() < (group.quantity - group.baseline_prediction).abs().mean() else 'baseline_7d'
    # No evidence of improvement => baseline, including newly introduced SKUs.
    for sku in daily.sku.unique():
        methods.setdefault(sku, 'baseline_7d')
    if 'baseline' in report:
        logging.info('Baseline MAE: %.4f; RandomForest MAE: %.4f', report['baseline']['mae'], report['random_forest']['mae'])
        if report['random_forest_worse_than_baseline']:
            logging.warning('RandomForest performs worse than baseline on validation')
    model = train_model(daily, config)
    future = predict_future(daily, config.forecast_days, model, config, methods)
    output = build_output(daily, future, validation, config)
    transactions.to_csv(config.processed_dir / 'transactions_audit.csv', index=False)
    daily.to_csv(config.processed_dir / 'daily_demand.csv', index=False)
    validation.to_csv(config.output_dir / 'validation_predictions.csv', index=False)
    future.to_csv(config.output_dir / 'daily_forecast.csv', index=False)
    output.to_csv(config.output_dir / 'forecast_output.csv', index=False)
    (config.output_dir / 'metrics.json').write_text(json.dumps(report, indent=2), encoding='utf-8')
    joblib.dump({'model': model, 'config': config, 'methods': methods, 'as_of': daily.date.max()}, config.model_dir / 'demand_model.joblib')
    if chart_sku:
        save_chart(daily, future, validation, chart_sku, config.output_dir / 'charts')
    logging.info('Saved %s', config.output_dir / 'forecast_output.csv')
    return output
