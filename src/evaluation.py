import numpy as np
import pandas as pd
from sklearn.model_selection import TimeSeriesSplit
from src.config import Config
from src.forecasting import predict_future, train_model
from src.security import bounded_number


def metrics(actual: pd.Series, predicted: pd.Series) -> dict:
    actual, predicted = np.asarray(actual), np.asarray(predicted)
    error = predicted - actual
    nonzero = actual > 0
    return {'mae': float(np.mean(np.abs(error))), 'rmse': float(np.sqrt(np.mean(error ** 2))),
            'mape': float(np.mean(np.abs(error[nonzero] / actual[nonzero])) * 100) if nonzero.any() else None,
            'mape_valid_count': int(nonzero.sum()), 'count': int(len(actual))}


def evaluate(daily: pd.DataFrame, config: Config, cv_splits: int = 0) -> tuple[pd.DataFrame, dict]:
    bounded_number(cv_splits, 'cv_splits', 0, 5, integer=True)
    dates = np.sort(daily.date.unique())
    if len(dates) < 10:
        return pd.DataFrame(), {'status': 'insufficient_history'}
    if cv_splits:
        if cv_splits < 2:
            raise ValueError('cv_splits must be 0 or at least 2')
        splits = list(TimeSeriesSplit(n_splits=cv_splits).split(dates))
    else:
        cut = max(1, int(len(dates) * .8))
        splits = [(np.arange(cut), np.arange(cut, len(dates)))]
    results = []
    for fold, (train_idx, test_idx) in enumerate(splits):
        train = daily[daily.date <= dates[train_idx[-1]]]
        test = daily[daily.date.isin(dates[test_idx])]
        model = train_model(train, config)
        days = len(test_idx)
        forest = predict_future(train, days, model, config).rename(columns={'prediction': 'model_prediction'})
        baseline = predict_future(train, days, None, config).rename(columns={'prediction': 'baseline_prediction'})
        joined = test.merge(forest, on=['sku', 'date']).merge(baseline[['sku', 'date', 'baseline_prediction']], on=['sku', 'date'])
        joined['fold'] = fold
        joined['train_end'] = dates[train_idx[-1]]
        joined['model_available'] = model is not None
        results.append(joined)
    predictions = pd.concat(results, ignore_index=True)
    # Качество оценивается только на днях без дефицита и аномалий: коррекции — оценка, а не факт.
    reliable = predictions[~(predictions.stockout_flag | predictions.is_outlier | predictions.is_large_client_order)]
    report = {'status': 'ok' if len(reliable) else 'no_reliable_test_targets',
              'folds': len(splits), 'evaluation_rows': len(reliable),
              'excluded_censored_or_anomalous_rows': len(predictions) - len(reliable)}
    if len(reliable):
        report['baseline'] = metrics(reliable.quantity, reliable.baseline_prediction)
        report['random_forest'] = metrics(reliable.quantity, reliable.model_prediction)
        report['random_forest_worse_than_baseline'] = report['random_forest']['mae'] > report['baseline']['mae']
    report['corrected_target_diagnostic'] = {
        'baseline': metrics(predictions.adjusted_demand, predictions.baseline_prediction),
        'random_forest': metrics(predictions.adjusted_demand, predictions.model_prediction)}
    return predictions, report
