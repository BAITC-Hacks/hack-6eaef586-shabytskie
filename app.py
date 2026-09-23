import tempfile
from datetime import datetime
from io import BytesIO
from pathlib import Path
import altair as alt
import pandas as pd
import streamlit as st
from src.config import Config
from src.ordering import (EXPORT_COLUMNS, STATUS_MISSING_STOCK, URGENCY_ORDER, to_1c_csv,
                          validate_approved_orders)
from src.pipeline import run_pipeline

DEMO = Path('data/demo')
URGENCY_COLORS = {'требуются данные': '⚫', 'критично': '🔴', 'высокая': '🟠',
                  'плановая': '🟢', 'не требуется': '⚪'}
SERIES = {'Продажи (факт)': '#8a8984', 'Спрос для расчёта': '#2a78d6', 'Прогноз': '#2a78d6'}
ONE_OFF_COLOR, STOCKOUT_COLOR = '#eb6834', '#e34948'
INPUTS = {
    'sales': ('История продаж *', 'synthetic_sales.csv',
              'Дата, Артикул, Количество; необязательно: ID клиента, Склад, Остаток, Поставщик, Категория, В пути'),
    'suppliers': ('Справочник поставщиков', 'suppliers.csv', 'Поставщик, Срок поставки, Минимальная партия'),
    'catalog': ('Номенклатура (1С)', 'catalog.csv', 'Артикул, Наименование, Категория, Поставщик, Кратность'),
    'stock': ('Текущие остатки', None, 'Артикул, Остаток; необязательно: Склад, В пути'),
    'stockouts': ('Периоды отсутствия товара', 'stockouts.csv', 'Артикул, Дата начала, Дата окончания'),
    'growth': ('Прогноз прироста', 'category_growth.csv', 'Категория или Артикул, Прирост (10% или 0.1)'),
}

st.set_page_config(page_title='Заказы поставщикам',
                   page_icon=str(Path(__file__).resolve().parent / 'site-icon.jpg'), layout='wide')


def demo_paths() -> dict:
    if not (DEMO / 'synthetic_sales.csv').exists():
        from src.generate_synthetic_data import generate
        generate(DEMO / 'synthetic_sales.csv')
    return {key: DEMO / file if file else None for key, (_, file, _) in INPUTS.items()}


def save_uploads(uploads: dict, directory: Path) -> dict:
    paths = {}
    for key, upload in uploads.items():
        if upload is None:
            paths[key] = None
            continue
        path = directory / f'{key}{Path(upload.name).suffix.lower()}'
        path.write_bytes(upload.getvalue())
        paths[key] = path
    return paths


def calculate(paths: dict, config: Config, warehouse: str | None, category: str | None) -> None:
    run_pipeline(paths['sales'], config, suppliers_path=paths['suppliers'], catalog_path=paths['catalog'],
                 stock_path=paths['stock'], stockouts_path=paths['stockouts'], growth_path=paths['growth'],
                 warehouse=warehouse, category=category)


def session_config(root: Path, review: int = 7, z: float = 1.65) -> Config:
    return Config(review_period_days=review, service_level_z=z, output_dir=root / 'outputs',
                  processed_dir=root / 'processed', model_dir=root / 'models')


# Результаты хранятся отдельно для каждой сессии браузера: в облаке файлы
# одного пользователя не видны другому. Демо с параметрами по умолчанию
# считается один раз на сервер и переиспользуется всеми сессиями.
@st.cache_resource(show_spinner=False)
def demo_result() -> Path:
    root = Path(tempfile.mkdtemp(prefix='demo_orders_'))
    calculate(demo_paths(), session_config(root), None, None)
    return root


def new_session_dir() -> Path:
    # Keep the last successful result intact if a new calculation fails.
    return Path(tempfile.mkdtemp(prefix='orders_'))


@st.cache_data(show_spinner=False)
def load_results(root: str, stamp: float) -> tuple[pd.DataFrame, pd.DataFrame, pd.DataFrame]:
    base = Path(root)
    orders = pd.read_csv(base / 'outputs' / 'supplier_orders.csv', dtype={'sku': str})
    daily = pd.read_csv(base / 'processed' / 'daily_demand.csv', parse_dates=['date'], dtype={'sku': str})
    future = pd.read_csv(base / 'outputs' / 'daily_forecast.csv', parse_dates=['date'], dtype={'sku': str})
    return orders, daily, future


def to_excel(table: pd.DataFrame) -> bytes:
    buffer = BytesIO()
    table.to_excel(buffer, index=False)
    return buffer.getvalue()


def demand_chart(history: pd.DataFrame, forecast: pd.DataFrame) -> alt.LayerChart:
    lines = pd.concat([
        history[['date', 'quantity']].rename(columns={'quantity': 'value'}).assign(series='Продажи (факт)'),
        history[['date', 'adjusted_demand']].rename(columns={'adjusted_demand': 'value'}).assign(series='Спрос для расчёта'),
        forecast[['date', 'prediction']].rename(columns={'prediction': 'value'}).assign(series='Прогноз'),
    ])
    # Шкала ограничена уровнем обычного спроса: иначе разовый пик в сотни единиц
    # сплющивает регулярный спрос в линию у нуля. Пики показываются маркерами у верхнего края.
    regular = pd.concat([history.adjusted_demand, forecast.prediction, history.quantity.where(history.excluded <= .5)])
    top = max(float(regular.max()) * 1.25, 1.)
    x = alt.X('date:T', title=None, axis=alt.Axis(format='%d.%m', grid=False))
    y = alt.Y('value:Q', title='ед./день', scale=alt.Scale(domain=[0, top], clamp=True), axis=alt.Axis(gridOpacity=.35))
    color = alt.Color('series:N', title=None, legend=alt.Legend(orient='top'),
                      scale=alt.Scale(domain=list(SERIES), range=list(SERIES.values())))
    dash = alt.StrokeDash('series:N', legend=None,
                          scale=alt.Scale(domain=list(SERIES), range=[[1, 0], [1, 0], [6, 4]]))
    stockouts = history[history.stockout_flag].assign(end=lambda d: d.date + pd.Timedelta(days=1))
    one_off = history[history.excluded > .5].assign(
        shown=lambda d: d.quantity.clip(upper=top), label=lambda d: d.quantity.map(lambda v: f'{v:,.0f}'.replace(',', ' ')))
    point_y = alt.Y('shown:Q', scale=alt.Scale(domain=[0, top], clamp=True))

    layers = [
        alt.Chart(stockouts).mark_rect(color=STOCKOUT_COLOR, opacity=.14).encode(
            x='date:T', x2='end:T',
            tooltip=[alt.Tooltip('date:T', title='Нет в наличии', format='%d.%m.%Y'),
                     alt.Tooltip('estimated_lost_demand:Q', title='Упущенный спрос', format='.0f')]),
        alt.Chart(lines).mark_line(strokeWidth=2, clip=True).encode(x=x, y=y, color=color, strokeDash=dash),
        alt.Chart(one_off).mark_point(filled=True, size=110, color=ONE_OFF_COLOR, opacity=1,
                                      stroke='white', strokeWidth=2).encode(
            x='date:T', y=point_y,
            tooltip=[alt.Tooltip('date:T', title='Разовый заказ', format='%d.%m.%Y'),
                     alt.Tooltip('quantity:Q', title='Продано', format='.0f'),
                     alt.Tooltip('excluded:Q', title='Исключено', format='.0f')]),
        alt.Chart(one_off).mark_text(dx=8, align='left', baseline='middle', fontSize=11).encode(
            x='date:T', y=point_y, text='label:N'),
    ]
    wide = lines.pivot_table(index='date', columns='series', values='value').reset_index()
    hover = alt.selection_point(fields=['date'], nearest=True, on='pointerover', empty=False, clear='pointerout')
    tooltip = [alt.Tooltip('date:T', title='Дата', format='%d.%m.%Y')] + [
        alt.Tooltip(f'{name}:Q', title=name, format='.1f') for name in SERIES if name in wide]
    layers.append(alt.Chart(wide).mark_rule(color='#8a8984', strokeWidth=1).encode(
        x='date:T', opacity=alt.condition(hover, alt.value(.6), alt.value(0)), tooltip=tooltip).add_params(hover))
    return alt.layer(*layers).properties(height=340)


with st.sidebar:
    st.header('Данные')
    source = st.radio('Источник', ['Загрузить свои файлы', 'Демо-данные'], key='source')
    uploads = {}
    if source == 'Загрузить свои файлы':
        for key, (label, _, columns) in INPUTS.items():
            uploads[key] = st.file_uploader(label, type=['csv', 'xlsx'], help=f'Колонки: {columns}', key=f'upload_{key}')
        with st.expander('Шаблоны файлов'):
            st.caption('Примеры в нужном формате — можно заполнить своими данными.')
            for key, (label, file, _) in INPUTS.items():
                path = DEMO / (file or '')
                if file and path.exists():
                    data = path.read_bytes() if key != 'sales' else pd.read_csv(path, nrows=200).to_csv(index=False).encode('utf-8-sig')
                    st.download_button(label.rstrip(' *'), data, file, key=f'template_{key}')
    else:
        st.caption('Синтетика: 20 артикулов, 400 дней, 4 поставщика, разовые заказы и периоды дефицита.')

    st.header('Параметры')
    warehouse = st.text_input('Склад (пусто = все)', '').strip() or None
    category = st.text_input('Категория (пусто = все)', '').strip() or None
    review = int(st.number_input('Период пересмотра, дн.', 1, 90, 7))
    z = float(st.number_input('z уровня сервиса', 0., 4., 1.65, .05, help='1.65 ≈ 95% уровень сервиса'))

    ready = source == 'Демо-данные' or uploads.get('sales') is not None
    if st.button('Запустить расчёт', type='primary', disabled=not ready, key='run', width='stretch'):
        with st.spinner('Расчёт… до минуты'):
            try:
                if source == 'Демо-данные' and (warehouse, category, review, z) == (None, None, 7, 1.65):
                    st.session_state.result_dir = demo_result()
                else:
                    root = new_session_dir()
                    with tempfile.TemporaryDirectory() as directory:
                        paths = demo_paths() if source == 'Демо-данные' else save_uploads(uploads, Path(directory))
                        calculate(paths, session_config(root, review, z), warehouse, category)
                    st.session_state.result_dir = root
                st.session_state.calculated_from = 'демо-данные' if source == 'Демо-данные' else uploads['sales'].name
                st.session_state.pop('approved_file', None)
            except (ValueError, OSError) as exc:
                st.error(str(exc))
    if not ready:
        st.caption('Загрузите историю продаж, чтобы запустить расчёт.')

st.title('Рекомендованные заказы поставщикам')

if 'result_dir' not in st.session_state:
    st.markdown('Загрузите выгрузку продаж в боковой панели или посмотрите, как работает сервис, на демо-данных.')
    if st.button('Показать на демо-данных', type='primary', key='demo_start'):
        with st.spinner('Расчёт на демо-данных… до минуты'):
            st.session_state.result_dir = demo_result()
            st.session_state.calculated_from = 'демо-данные'
        st.rerun()
    st.stop()

result_dir = st.session_state.result_dir
try:
    orders, daily, future = load_results(str(result_dir), (result_dir / 'outputs' / 'supplier_orders.csv').stat().st_mtime)
except FileNotFoundError:
    st.session_state.pop('result_dir', None)
    st.session_state.pop('approved_file', None)
    if result_dir.name.startswith('demo_orders_'):
        demo_result.clear()
    st.warning('Файлы предыдущего расчёта недоступны. Проверьте склад и категорию '
               '(это названия, а не артикул товара) и нажмите «Запустить расчёт». '
               'Для расчёта по всем товарам оставьте оба поля пустыми.')
    st.stop()
st.caption(f"Расчёт по: {st.session_state.get('calculated_from', '—')}. Заказ — черновик, поставщику ничего не отправляется.")

active = orders[orders.recommended_qty > 0]
cols = st.columns(5)
cols[0].metric('Позиций к заказу', len(active))
cols[1].metric('Критичных', int((active.urgency == 'критично').sum()))
cols[2].metric('Поставщиков', active.supplier.nunique())
cols[3].metric('Сумма', f"{active.order_value.sum():,.0f}".replace(',', ' '))
cols[4].metric('Нет остатков', int((orders.status == STATUS_MISSING_STOCK).sum()),
               help='Рекомендация заблокирована: загрузите актуальные остатки')

tab_orders, tab_card, tab_trends = st.tabs(['Заказы по поставщикам', 'Карточка артикула', 'Тренды по категориям'])

with tab_orders:
    supplier = st.selectbox('Поставщик', sorted(orders.supplier.unique()))
    show_all = st.checkbox('Показать позиции без заказа', False)
    view = orders[orders.supplier == supplier]
    if not show_all:
        view = view[(view.recommended_qty > 0) | (view.status == STATUS_MISSING_STOCK)]
    view = view.assign(urgency=view.urgency.map(lambda u: f"{URGENCY_COLORS.get(u, '')} {u}"))
    shown = ['sku', 'product_name', 'urgency', 'recommended_qty', 'approved_qty', 'days_of_cover',
             'lead_time_days', 'demand_for_coverage', 'safety_stock', 'stock', 'in_transit', 'reason']
    edited = st.data_editor(
        view[shown].rename(columns=EXPORT_COLUMNS | {'urgency': 'Срочность'}),
        disabled=[EXPORT_COLUMNS.get(c, c) for c in shown if c != 'approved_qty'],
        column_config={'Обоснование': st.column_config.TextColumn(width='large')},
        hide_index=True, width='stretch', key=f'editor_{supplier}')
    approver = st.text_input('Ответственный', '', key='approver')
    if st.button(f'Утвердить заказ: {supplier}', disabled=not approver.strip(), key='approve'):
        try:
            approved = view.copy()
            approved['approved_qty'] = edited['Утверждённое количество'].to_numpy()
            blocked_untouched = approved.status.eq(STATUS_MISSING_STOCK) & approved.approved_qty.isna()
            approved.loc[blocked_untouched, 'approved_qty'] = 0.
            approved = validate_approved_orders(approved)
            approved = approved[approved.approved_qty > 0]
            if approved.empty:
                raise ValueError('Нет позиций с количеством больше 0: утверждать нечего.')
            approved['urgency'] = approved.urgency.str.split(' ', n=1).str[-1]
            approved['order_value'] = approved.approved_qty * approved.price
            approved_at = datetime.now()
            approved['status'] = f'утверждён: {approver.strip()} {approved_at:%Y-%m-%d %H:%M:%S}'
            table = approved[list(EXPORT_COLUMNS)].rename(columns=EXPORT_COLUMNS)
            stem = f"{''.join(c if c.isalnum() else '_' for c in supplier)}_{approved_at:%Y%m%d_%H%M%S}"
            # Утверждение только готовит файлы для ответственного — поставщику заказ автоматически не отправляется.
            st.session_state.approved_file = (stem, to_excel(table), to_1c_csv(table))
        except ValueError as exc:
            st.session_state.pop('approved_file', None)
            st.error(str(exc))
    if 'approved_file' in st.session_state:
        stem, xlsx_bytes, csv_bytes = st.session_state.approved_file
        st.success(f'Заказ утверждён: {stem}. Скачайте файл и передайте поставщику.')
        left, right = st.columns(2)
        left.download_button('Утверждённый заказ (XLSX)', xlsx_bytes, f'{stem}.xlsx', key='dl_approved_xlsx')
        right.download_button('Утверждённый заказ для 1С (CSV)', csv_bytes, f'{stem}_1c.csv', key='dl_approved_csv')
    all_xlsx = result_dir / 'outputs' / 'supplier_orders.xlsx'
    if all_xlsx.exists():
        st.download_button('Скачать все заказы (XLSX)', all_xlsx.read_bytes(), 'supplier_orders.xlsx', key='dl_all')

with tab_card:
    ranked = orders.assign(_rank=orders.urgency.map(URGENCY_ORDER)).sort_values(['_rank', 'days_of_cover'])
    labels = {row.sku: f"{URGENCY_COLORS.get(row.urgency, '')} {row.sku} — {row.product_name} ({row.supplier})"
              for row in ranked.itertuples()}
    sku = st.selectbox('Артикул', list(labels), format_func=labels.get, key='card_sku')
    item = orders.set_index('sku').loc[sku]

    cols = st.columns(4)
    blocked = item.status == STATUS_MISSING_STOCK
    cols[0].metric('Рекомендуемый заказ', '— нет остатка' if blocked else f'{item.recommended_qty:,.0f} ед.'.replace(',', ' '))
    cols[1].metric('Срочность', f"{URGENCY_COLORS.get(item.urgency, '')} {item.urgency}")
    cols[2].metric('Запаса хватит на', f'{item.days_of_cover:.1f} дн.' if pd.notna(item.days_of_cover) else '—',
                   help=f'Срок поставки {item.lead_time_days} дн.')
    stock_text = f'{item.stock:.0f}' if item.stock_known else 'нет данных'
    cols[3].metric('Доступно', 'нет данных' if blocked else f'{item.stock + item.in_transit:,.0f} ед.'.replace(',', ' '),
                   help=f'Остаток {stock_text} + в пути {item.in_transit:.0f}')

    history = daily[daily.sku == sku].tail(120).copy()
    history['excluded'] = (history.quantity - history.quantity_clean).where(history.is_large_client_order | history.is_outlier, 0)
    start = history.date.max() + pd.Timedelta(days=1)
    forecast = future[(future.sku == sku) & (future.date < start + pd.Timedelta(days=int(item.coverage_days)))]
    st.subheader('Спрос: история и прогноз на период покрытия')
    st.altair_chart(demand_chart(history, forecast), width='stretch')
    st.caption('Серая линия — фактические продажи. Синяя — спрос, по которому считается заказ: '
               'без разовых крупных заказов (оранжевые точки с объёмом; пики выше шкалы обрезаны) и с упущенным '
               'спросом в дни отсутствия товара (красная заливка). Пунктир — прогноз на срок поставки + период пересмотра.')

    left, right = st.columns([3, 2])
    with left:
        st.subheader('Как получилось количество')
        fmt = lambda v, unit='ед.': f'{v:,.0f} {unit}'.replace(',', ' ') if pd.notna(v) else '—'
        steps = [
            (f"Прогноз модели на {item.coverage_days} дн.", fmt(item.model_demand)),
            ('× годовая сезонность', f'{item.seasonal_factor:.2f}' if pd.notna(item.seasonal_factor) else '— (история < года)'),
            ('× устойчивый тренд', f'{item.trend_factor:.2f}'),
            ('× плановый прирост', f'{1 + item.planned_growth:.2f}'),
            ('= Спрос на период', fmt(item.demand_for_coverage)),
            ('+ Страховой запас', fmt(item.safety_stock)),
            ('− Остаток', 'нет данных' if blocked else fmt(item.stock)),
            ('− В пути', fmt(item.in_transit)),
            ('= Потребность', fmt(item.net_need)),
            (f'→ Заказ (кратность {item.order_multiple:.0f}, мин. партия {item.min_order_qty:.0f})', fmt(item.recommended_qty)),
            ('Для сравнения: по «сырым» продажам', fmt(item.raw_sales_need)),
        ]
        st.dataframe(pd.DataFrame(steps, columns=['Шаг', 'Значение']), hide_index=True, width='stretch',
                     height=36 * (len(steps) + 1) + 3)
    with right:
        st.subheader('События за 120 дней')
        events = pd.concat([
            history[history.excluded > .5].assign(
                Событие='Разовый заказ исключён', Единиц=lambda d: -d.excluded.round()),
            history[history.stockout_flag].assign(
                Событие='Нет в наличии: добавлен спрос', Единиц=lambda d: d.estimated_lost_demand.round()),
        ])
        if events.empty:
            st.caption('Разовых заказов и дней отсутствия товара не было.')
        else:
            events = events.sort_values('date', ascending=False).assign(Дата=lambda d: d.date.dt.strftime('%d.%m.%Y'))
            st.dataframe(events[['Дата', 'Событие', 'Единиц']], hide_index=True, width='stretch',
                         height=min(36 * (len(events) + 1) + 3, 420))
    st.info(item.reason)

with tab_trends:
    if 'category' not in daily:
        daily = daily.merge(orders[['sku', 'category']], on='sku', how='left')
    weekly = (daily.groupby([pd.Grouper(key='date', freq='W'), 'category']).adjusted_demand.sum().unstack())
    st.caption('Скорректированный спрос (без разовых заказов, с упущенным спросом), ед./неделю')
    st.line_chart(weekly)
