"""Расчёт рекомендованного заказа по артикулу (политика «пополнение до уровня»):

    покрытие = срок поставки + период пересмотра
    спрос = прогноз за покрытие × годовая сезонность × устойчивый тренд × (1 + плановый прирост)
    страховой = z × σ ошибки прогноза × √покрытие
    потребность = спрос + страховой − (остаток + в пути)
    заказ = потребность, округлённая вверх до кратности, но не меньше минимальной партии
"""
import math
import re
from pathlib import Path
import numpy as np
import pandas as pd
from src.config import Config
from src.preprocessing import SPACES

URGENCY_ORDER = {'критично': 0, 'высокая': 1, 'плановая': 2, 'не требуется': 3}
STATUS_DRAFT = 'черновик — требует утверждения'


def parse_fraction(value) -> float | None:
    if value is None or (isinstance(value, float) and math.isnan(value)) or pd.isna(value):
        return None
    text = re.sub(SPACES, '', str(value)).replace(',', '.')
    if not text:
        return None
    percent = text.endswith('%')
    number = float(text.rstrip('%'))
    return number / 100 if percent or abs(number) >= 1 else number


def _number(value, default=None):
    try:
        number = float(re.sub(SPACES, '', str(value)).replace(',', '.'))
    except (TypeError, ValueError):
        return default
    return default if math.isnan(number) or number < 0 else number


def sku_parameters(daily: pd.DataFrame, config: Config, catalog: pd.DataFrame | None = None,
                   suppliers: pd.DataFrame | None = None) -> pd.DataFrame:
    rows = []
    last = daily.groupby('sku', sort=False).last()
    catalog_rows = catalog.drop_duplicates('sku', keep='last').set_index('sku') if catalog is not None else None
    supplier_rows = suppliers.drop_duplicates('supplier', keep='last').set_index('supplier') if suppliers is not None else None
    for sku, row in last.iterrows():
        item = {'sku': sku}
        for col in ['product_name', 'category', 'supplier', 'lead_time_days', 'price']:
            item[col] = row.get(col) if col in last else None
        item['min_order_qty'], item['order_multiple'] = None, None
        if catalog_rows is not None and sku in catalog_rows.index:
            for col in ['product_name', 'category', 'supplier', 'lead_time_days', 'price',
                        'min_order_qty', 'order_multiple']:
                if col in catalog_rows and pd.notna(catalog_rows.at[sku, col]):
                    item[col] = catalog_rows.at[sku, col]
        if supplier_rows is not None and item['supplier'] in supplier_rows.index:
            info = supplier_rows.loc[item['supplier']]
            if 'lead_time_days' in info and pd.notna(info['lead_time_days']):
                item['lead_time_days'] = info['lead_time_days']
            if item['min_order_qty'] is None and 'min_order_qty' in info and pd.notna(info['min_order_qty']):
                item['min_order_qty'] = info['min_order_qty']
        item['lead_time_days'] = int(round(_number(item['lead_time_days'], config.default_lead_time_days))) or config.default_lead_time_days
        item['min_order_qty'] = _number(item['min_order_qty'], 0.)
        item['order_multiple'] = _number(item['order_multiple'], 1.) or 1.
        item['price'] = _number(item['price'])
        item['supplier'] = item['supplier'] if isinstance(item['supplier'], str) and item['supplier'] else 'Поставщик не указан'
        item['category'] = item['category'] if isinstance(item['category'], str) and item['category'] else 'Без категории'
        item['product_name'] = item['product_name'] if isinstance(item['product_name'], str) and item['product_name'] else sku
        rows.append(item)
    return pd.DataFrame(rows)


def current_inventory(daily: pd.DataFrame, stock: pd.DataFrame | None = None) -> pd.DataFrame:
    rows = []
    for sku, group in daily.groupby('sku', sort=False):
        item = {'sku': sku, 'stock': None, 'stock_as_of': 'нет данных', 'in_transit': 0.}
        if 'stock' in group and group.stock.notna().any():
            known = group[group.stock.notna()].iloc[-1]
            item['stock'], item['stock_as_of'] = float(known.stock), known.date.date().isoformat()
        if 'in_transit' in group and group.in_transit.notna().any():
            item['in_transit'] = float(group[group.in_transit.notna()].in_transit.iloc[-1])
        item['out_of_stock_now'] = bool(group.stockout_flag.iloc[-1])
        rows.append(item)
    result = pd.DataFrame(rows)
    if stock is not None and not stock.empty:
        table = stock.copy()
        for col in ['stock', 'in_transit']:
            if col in table:
                table[col] = table[col].map(_number)
        sums = table.groupby('sku').agg({col: lambda s: s.sum(min_count=1) for col in ['stock', 'in_transit'] if col in table})
        for col in sums:
            values = result.sku.map(sums[col])
            result[col] = values.where(values.notna(), result[col])
        if 'stock' in sums:
            result.loc[result.sku.isin(sums.index[sums.stock.notna()]), 'stock_as_of'] = 'справочник остатков'
    # Дефицит на последнюю дату важнее старого положительного остатка из продаж.
    stale = result.out_of_stock_now & result.stock_as_of.ne('справочник остатков')
    result.loc[stale, 'stock'] = 0.
    result.loc[stale, 'stock_as_of'] = 'нет в наличии на последнюю дату'
    return result.drop(columns='out_of_stock_now')


def annual_factor(group: pd.DataFrame, start: pd.Timestamp, coverage: int) -> float | None:
    series = group.set_index('date').adjusted_demand
    # Отношение спроса прошлого года в будущем окне к 28 дням перед ним.
    # Сдвиг на 52 недели (364 дня) сохраняет совпадение дней недели.
    origin = start - pd.Timedelta(days=364)
    window = series[origin:origin + pd.Timedelta(days=coverage - 1)]
    base = series[origin - pd.Timedelta(days=28):origin - pd.Timedelta(days=1)]
    if len(window) < coverage or len(base) < 28 or base.mean() <= 0:
        return None
    return float(np.clip(window.mean() / base.mean(), .5, 2.))


def trend_factor(group: pd.DataFrame, coverage: int) -> tuple[float, float | None]:
    # При истории больше года тренд считается год к году — так он не путается с сезонностью.
    # Иначе нужны два подряд месячных изменения одного знака больше 3%, чтобы не принять шум за тренд.
    values = group.adjusted_demand.to_numpy(dtype=float)
    monthly = None
    if len(values) >= 364 + 28:
        recent, last_year = values[-28:].mean(), values[-364 - 28:-364].mean()
        if last_year > 0 and abs(recent / last_year - 1) >= .05:
            monthly = (recent / last_year) ** (30 / 364) - 1
    elif len(values) >= 90:
        m1, m2, m3 = values[-30:].mean(), values[-60:-30].mean(), values[-90:-60].mean()
        if m2 > 0 and m3 > 0:
            g1, g2 = m1 / m2 - 1, m2 / m3 - 1
            if np.sign(g1) == np.sign(g2) and min(abs(g1), abs(g2)) >= .03:
                monthly = float(np.clip((g1 + g2) / 2, -.2, .2))
    if monthly is None:
        return 1., None
    return float(np.clip((1 + monthly) ** (coverage / 2 / 30), .8, 1.25)), float(monthly)


def planned_growth(growth: pd.DataFrame | None, sku: str, category: str) -> float:
    if growth is None or growth.empty or 'growth_forecast' not in growth:
        return 0.
    for key, value in [('sku', sku), ('category', category)]:
        if key in growth:
            match = growth[growth[key].astype(str) == str(value)]
            if len(match):
                parsed = parse_fraction(match.growth_forecast.iloc[-1])
                if parsed is not None:
                    return parsed
    return 0.


def forecast_window(future: pd.DataFrame, sku: str, start: pd.Timestamp, days: int) -> float:
    series = future[future.sku == sku].sort_values('date')
    window = series[series.date < start + pd.Timedelta(days=days)].prediction
    missing = days - len(window)
    tail = series.prediction.tail(7).mean() if len(series) else 0.
    return float(window.sum() + max(missing, 0) * (tail if np.isfinite(tail) else 0.))


def recommend_orders(daily: pd.DataFrame, future: pd.DataFrame, config: Config,
                     forecast_summary: pd.DataFrame | None = None, catalog: pd.DataFrame | None = None,
                     suppliers: pd.DataFrame | None = None, stock: pd.DataFrame | None = None,
                     growth: pd.DataFrame | None = None) -> pd.DataFrame:
    params = sku_parameters(daily, config, catalog, suppliers).set_index('sku')
    inventory = current_inventory(daily, stock).set_index('sku')
    summary = forecast_summary.set_index('sku') if forecast_summary is not None and len(forecast_summary) else None
    rows = []
    for sku, group in daily.groupby('sku', sort=False):
        p, inv = params.loc[sku], inventory.loc[sku]
        start = group.date.max() + pd.Timedelta(days=1)
        lead = int(p.lead_time_days)
        coverage = lead + config.review_period_days
        model_demand = forecast_window(future, sku, start, coverage)
        seasonal = annual_factor(group, start, coverage)
        trend, monthly = trend_factor(group, coverage)
        plan = planned_growth(growth, sku, p.category)
        demand = model_demand * (seasonal or 1.) * trend * (1 + plan)
        daily_rate = demand / coverage

        # σ берётся из ошибки прогноза на валидации (σ ≈ 1.25·MAE), без неё — из разброса спроса.
        error, error_source = None, 'разброс спроса за 90 дн.'
        if summary is not None and sku in summary.index:
            s = summary.loc[sku]
            mae = s.model_mae if s.model_used == 'random_forest' else s.baseline_mae
            if pd.notna(mae):
                error, error_source = 1.25 * float(mae), 'ошибка прогноза на валидации'
        if error is None:
            error = float(group.adjusted_demand.tail(90).std(ddof=0)) if len(group) > 1 else 0.
        safety = config.service_level_z * error * math.sqrt(coverage)

        stock_known = pd.notna(inv.stock)
        on_hand = inv.stock if stock_known else 0.
        in_transit = inv.in_transit if pd.notna(inv.in_transit) else 0.
        available = on_hand + in_transit
        need = demand + safety - available
        quantity = 0.
        if need > 0:
            quantity = math.ceil(need / p.order_multiple - 1e-9) * p.order_multiple
            quantity = max(quantity, p.min_order_qty)
        days_of_cover = available / daily_rate if daily_rate > 0 else float('inf')
        # Критично: запас закончится раньше, чем придёт поставка.
        if quantity <= 0:
            urgency = 'не требуется'
        elif days_of_cover < lead:
            urgency = 'критично'
        elif days_of_cover < coverage:
            urgency = 'высокая'
        else:
            urgency = 'плановая'

        # Для обоснования: какой была бы потребность по «сырым» продажам без коррекций.
        recent = group.tail(28)
        raw_rate = recent.quantity.mean()
        raw_need = max(raw_rate * coverage + safety - available, 0.)
        lost = recent.estimated_lost_demand.sum() * coverage / max(len(recent), 1)
        removed = (recent.quantity - recent.quantity_clean).sum() * coverage / max(len(recent), 1)

        row = dict(supplier=p.supplier, sku=sku, product_name=p.product_name, category=p.category,
                   recommended_qty=float(quantity), approved_qty=float(quantity), urgency=urgency,
                   days_of_cover=round(days_of_cover, 1) if np.isfinite(days_of_cover) else None,
                   lead_time_days=lead, coverage_days=coverage, model_demand=round(model_demand, 2),
                   seasonal_factor=round(seasonal, 4) if seasonal else None, trend_factor=round(trend, 4),
                   planned_growth=plan, demand_for_coverage=round(demand, 1), safety_stock=round(safety, 1),
                   stock=on_hand, stock_as_of=inv.stock_as_of, in_transit=in_transit, net_need=round(need, 1),
                   raw_sales_need=round(raw_need, 1), lost_demand_adjustment=round(lost, 1),
                   one_off_orders_removed=round(removed, 1), min_order_qty=p.min_order_qty,
                   order_multiple=p.order_multiple, price=p.price,
                   order_value=round(quantity * p.price, 2) if p.price is not None else None,
                   stock_known=stock_known, status=STATUS_DRAFT)
        row['reason'] = explain(row, monthly, error_source)
        rows.append(row)
    result = pd.DataFrame(rows)
    result['_rank'] = result.urgency.map(URGENCY_ORDER)
    result = result.sort_values(['supplier', '_rank', 'days_of_cover'], na_position='last')
    return result.drop(columns='_rank').reset_index(drop=True)


def explain(row: dict, monthly: float | None, error_source: str) -> str:
    parts = [f"Покрытие {row['coverage_days']} дн. (поставка {row['lead_time_days']} + пересмотр "
             f"{row['coverage_days'] - row['lead_time_days']}): прогноз модели {row['model_demand']:.0f} ед."]
    if row['seasonal_factor']:
        parts.append(f"годовая сезонность ×{row['seasonal_factor']:.2f}")
    if monthly is not None:
        parts.append(f"устойчивый тренд {monthly:+.1%}/мес ×{row['trend_factor']:.2f}")
    if row['planned_growth']:
        parts.append(f"плановый прирост {row['planned_growth']:+.0%}")
    text = ', '.join(parts) + f" → спрос {row['demand_for_coverage']:.0f} ед. "
    text += f"Страховой запас {row['safety_stock']:.0f} ед. ({error_source}). "
    stock_text = f"{row['stock']:.0f}" if row['stock_known'] else 'нет данных, принят 0'
    text += (f"Доступно {row['stock'] + row['in_transit']:.0f} ед. (остаток {stock_text}"
             f" + в пути {row['in_transit']:.0f}). ")
    if row['recommended_qty'] > 0:
        text += f"Потребность {row['net_need']:.0f} → заказ {row['recommended_qty']:.0f} ед."
        if row['order_multiple'] > 1 or row['min_order_qty'] > 0:
            text += f" (кратность {row['order_multiple']:.0f}, мин. партия {row['min_order_qty']:.0f})"
        text += '. '
        if row['days_of_cover'] is not None:
            text += f"Запаса хватит на {row['days_of_cover']:.1f} дн. при сроке поставки {row['lead_time_days']} дн. "
    else:
        text += 'Запаса достаточно, заказ не нужен. '
    noticeable = max(1., .02 * row['demand_for_coverage'])
    if row['lost_demand_adjustment'] >= noticeable:
        text += f"Учтён упущенный спрос при отсутствии товара: +{row['lost_demand_adjustment']:.0f} ед. "
    if row['one_off_orders_removed'] >= noticeable:
        text += f"Исключены разовые крупные заказы: −{row['one_off_orders_removed']:.0f} ед. "
    return text.strip()


def supplier_summary(orders: pd.DataFrame) -> pd.DataFrame:
    active = orders[orders.recommended_qty > 0]
    grouped = active.groupby('supplier')
    result = pd.DataFrame({
        'positions': grouped.sku.count(),
        'total_qty': grouped.recommended_qty.sum(),
        'total_value': grouped.order_value.sum(min_count=1),
        'critical_positions': grouped.urgency.apply(lambda s: int((s == 'критично').sum())),
    }).reset_index()
    return result.sort_values(['critical_positions', 'total_value'], ascending=False).reset_index(drop=True)


EXPORT_COLUMNS = {
    'supplier': 'Поставщик', 'sku': 'Артикул', 'product_name': 'Наименование', 'category': 'Категория',
    'recommended_qty': 'Рекомендуемое количество', 'approved_qty': 'Утверждённое количество',
    'urgency': 'Срочность', 'days_of_cover': 'Запас, дн.', 'lead_time_days': 'Срок поставки, дн.',
    'demand_for_coverage': 'Спрос на период', 'safety_stock': 'Страховой запас', 'stock': 'Остаток',
    'in_transit': 'В пути', 'price': 'Цена', 'order_value': 'Сумма', 'reason': 'Обоснование',
    'status': 'Статус',
}


def to_1c_csv(table: pd.DataFrame) -> bytes:
    # Для 1С и русского Excel: разделитель «;», десятичная запятая, UTF-8 с BOM, целые без «.0».
    table = table.copy()
    for col in table.select_dtypes('number'):
        values = table[col]
        if values.dropna().eq(values.dropna().round()).all():
            table[col] = values.round().astype('Int64')
    return table.to_csv(sep=';', decimal=',', index=False).encode('utf-8-sig')


def export_orders(orders: pd.DataFrame, directory) -> dict:
    directory = Path(directory)
    directory.mkdir(parents=True, exist_ok=True)
    readable = orders[list(EXPORT_COLUMNS)].rename(columns=EXPORT_COLUMNS)
    xlsx = directory / 'supplier_orders.xlsx'
    with pd.ExcelWriter(xlsx, engine='openpyxl') as writer:
        supplier_summary(orders).rename(columns={
            'supplier': 'Поставщик', 'positions': 'Позиций', 'total_qty': 'Количество',
            'total_value': 'Сумма', 'critical_positions': 'Критичных'}).to_excel(writer, sheet_name='Сводка', index=False)
        readable.to_excel(writer, sheet_name='Все позиции', index=False)
        used = set()
        for supplier, group in readable[readable['Рекомендуемое количество'] > 0].groupby('Поставщик'):
            name = ''.join(c for c in str(supplier) if c not in '[]:*?/\\')[:31] or 'Поставщик'
            while name in used:
                name = name[:28] + f'_{len(used)}'
            used.add(name)
            group.to_excel(writer, sheet_name=name, index=False)
    csv = directory / 'supplier_orders_1c.csv'
    csv.write_bytes(to_1c_csv(readable[readable['Рекомендуемое количество'] > 0]))
    full = directory / 'supplier_orders.csv'
    orders.to_csv(full, index=False)
    return {'xlsx': xlsx, 'csv_1c': csv, 'csv': full}
