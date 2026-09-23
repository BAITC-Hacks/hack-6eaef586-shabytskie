"""Pipeline configuration and canonical input names."""
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

    def __post_init__(self) -> None:
        if self.forecast_days < 1:
            raise ValueError('forecast_days must be positive')
