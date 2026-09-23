from dataclasses import dataclass
from pathlib import Path

ALIASES = {
    'date': ['date', 'sale_date', 'Дата', 'Дата продажи'],
    'sku': ['sku', 'article', 'Артикул', 'Код товара', 'product_id'],
    'quantity': ['quantity', 'qty', 'Количество', 'Продажи'],
    'client_id': ['client_id', 'customer_id', 'Клиент', 'ID клиента'],
    'product_name': ['product_name', 'name', 'Наименование', 'Название товара'],
    'price': ['price', 'Цена'],
    'warehouse': ['warehouse', 'Склад'],
    'stock': ['stock', 'inventory', 'Остаток', 'Остаток на дату', 'current_stock'],
    'stockout_flag': ['stockout_flag', 'stockout', 'Нет в наличии'],
    'supplier': ['supplier', 'Поставщик'],
    'lead_time_days': ['lead_time', 'lead_time_days', 'Срок поставки'],
    'in_transit': ['in_transit', 'Товар в пути', 'В пути'],
    'category': ['category', 'Категория'],
    'min_order_qty': ['min_order_qty', 'moq', 'Минимальная партия', 'Мин. партия'],
    'order_multiple': ['order_multiple', 'pack_size', 'Кратность', 'Кратность заказа'],
    'growth_forecast': ['growth_forecast', 'growth', 'Прогноз прироста', 'Прирост'],
    'date_from': ['date_from', 'start', 'Дата начала', 'С'],
    'date_to': ['date_to', 'end', 'Дата окончания', 'По'],
}
REQUIRED = {'date', 'sku', 'quantity'}


@dataclass(frozen=True)
class Config:
    forecast_days: int = 30
    random_state: int = 42
    min_history: int = 35
    output_dir: Path = Path('outputs')
    processed_dir: Path = Path('data/processed')
    model_dir: Path = Path('models')
    review_period_days: int = 7
    default_lead_time_days: int = 14
    service_level_z: float = 1.65  # ≈ 95% уровень сервиса

    def __post_init__(self) -> None:
        if self.forecast_days < 1:
            raise ValueError('forecast_days must be positive')
        if self.review_period_days < 1 or self.default_lead_time_days < 1:
            raise ValueError('review_period_days and default_lead_time_days must be positive')
        if self.service_level_z < 0:
            raise ValueError('service_level_z must be non-negative')
