import numpy as np
import pandas as pd
from sklearn.compose import ColumnTransformer
from sklearn.ensemble import RandomForestRegressor
from sklearn.impute import SimpleImputer
from sklearn.pipeline import Pipeline
from sklearn.preprocessing import OneHotEncoder
from src.config import Config
from src.feature_engineering import FEATURES, NUMERIC_FEATURES, engineer_features, feature_row
from src.security import bounded_number


def train_model(daily: pd.DataFrame, config: Config) -> Pipeline | None:
    features = engineer_features(daily)
    features = features[features.groupby('sku').cumcount() >= config.min_history]
    if len(features) < 20:
        return None
    processor = ColumnTransformer([
        ('sku', OneHotEncoder(handle_unknown='ignore'), ['sku']),
        ('numeric', SimpleImputer(strategy='constant', fill_value=0), NUMERIC_FEATURES),
    ])
    model = Pipeline([('features', processor), ('regressor', RandomForestRegressor(
        n_estimators=100, max_depth=16, min_samples_leaf=3, n_jobs=1, random_state=config.random_state))])
    model.fit(features[FEATURES], features.adjusted_demand)
    return model


def predict_future(history: pd.DataFrame, days: int, model: Pipeline | None, config: Config,
                   methods: dict[str, str] | None = None) -> pd.DataFrame:
    bounded_number(days, 'forecast_days', 1, 3653, integer=True)
    histories = {str(sku): g.adjusted_demand.tolist() for sku, g in history.groupby('sku', sort=False)}
    trained_skus = {sku for sku, values in histories.items() if len(values) > config.min_history}
    end = history.date.max()
    rows = []
    for date in pd.date_range(end + pd.Timedelta(days=1), periods=days):
        features = pd.DataFrame([feature_row(sku, date, values) for sku, values in histories.items()])
        predictions = model.predict(features[FEATURES]) if model is not None else np.zeros(len(features))
        for idx, sku in enumerate(histories):
            method = (methods or {}).get(sku, 'random_forest')
            if model is None or sku not in trained_skus or method != 'random_forest':
                # У редко продаваемого товара (>30% дней без продаж) среднее за 7 дней слишком шумное — берём 28.
                recent = np.asarray(histories[sku][-28:])
                window = 28 if (recent == 0).mean() > .3 else 7
                prediction = float(np.mean(histories[sku][-window:]))
                used = 'baseline_7d' if sku in trained_skus else 'fallback_7d'
            else:
                prediction, used = float(predictions[idx]), 'random_forest'
            prediction = max(0., prediction)
            rows.append(dict(sku=sku, date=date, prediction=prediction, model_used=used))
            # Рекурсивный прогноз: предсказание становится лагом для следующего дня.
            histories[sku].append(prediction)
    return pd.DataFrame(rows)
