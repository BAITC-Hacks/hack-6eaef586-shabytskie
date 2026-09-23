import logging
from pathlib import Path
import secrets
import altair as alt
import pandas as pd
import streamlit as st
from src.ordering import EXPORT_COLUMNS, STATUS_MISSING_STOCK, URGENCY_ORDER
from src.security import public_error, security_event
from src.web_auth import require_principal, PERMISSIONS
from src.web_service import WebService

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

st.set_page_config(page_title='Заказы поставщикам', page_icon='📦', layout='wide')
logging.basicConfig(level=logging.INFO, format='[%(levelname)s] %(message)s')
principal = require_principal()


@st.cache_resource(show_spinner=False)
def web_service() -> WebService:
    # One bounded registry/limiter per process; private results are never cached globally.
    return WebService(reap=True)


service = web_service()
scope = (principal.subject, principal.role)
if st.session_state.get('identity_scope') != scope:
    st.session_state.clear()
    st.session_state.identity_scope = scope
st.session_state.setdefault('session_key', secrets.token_urlsafe(32))
session_key = st.session_state.session_key


def calculate(demo: bool, uploads=None, review=7, z=1.65, warehouse=None, category=None):
    token = service.calculate(principal, session_key, demo=demo, uploads=uploads,
                              review=review, z=z, warehouse=warehouse, category=category)
    old = st.session_state.get('result_token')
    st.session_state.result_token = token
    st.session_state.results_data = service.load_results(token, principal, session_key)
    st.session_state.calculated_from = 'демо-данные' if demo else 'загруженные файлы'
    st.session_state.pop('approved_file', None)
    if old:
        try:
            service.discard(old, principal, session_key)
        except ValueError:
            security_event('workspace_already_expired', principal.subject)


with st.sidebar:
    if principal.role != 'demo' and st.button('Выйти', key='logout'):
        if st.session_state.get('result_token'):
            try:
                service.discard(st.session_state.result_token, principal, session_key)
            except ValueError:
                security_event('workspace_already_expired', principal.subject)
        st.session_state.clear()
        security_event('logout', principal.subject)
        st.logout()


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
        alt.Chart(one_off).mark_text(dx=8, align='left', baseline='middle', fontSize=11, color='#52514e').encode(
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
    sources = ['Загрузить свои файлы', 'Демо-данные'] if 'upload' in PERMISSIONS[principal.role] else ['Демо-данные']
    source = st.radio('Источник', sources, key='source')
    uploads = {}
    if source == 'Загрузить свои файлы':
        for key, (label, _, columns) in INPUTS.items():
            uploads[key] = st.file_uploader(label, type=['csv', 'xlsx'], max_upload_size=20,
                                            help=f'Колонки: {columns}. До 20 МБ, один лист XLSX без формул.', key=f'upload_{key}')
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
    warehouse = st.text_input('Склад (пусто = все)', '', max_chars=128).strip() or None
    category = st.text_input('Категория (пусто = все)', '', max_chars=128).strip() or None
    review = int(st.number_input('Период пересмотра, дн.', 1, 90, 7))
    z = float(st.number_input('z уровня сервиса', 0., 4., 1.65, .05, help='1.65 ≈ 95% уровень сервиса'))

    ready = source == 'Демо-данные' or uploads.get('sales') is not None
    if st.button('Запустить расчёт', type='primary', disabled=not ready, key='run', width='stretch'):
        with st.spinner('Расчёт… до минуты'):
            try:
                calculate(source == 'Демо-данные', uploads, review, z, warehouse, category)
            except Exception as exc:
                st.error(public_error(exc, principal.subject))
    if not ready:
        st.caption('Загрузите историю продаж, чтобы запустить расчёт.')

st.title('Рекомендованные заказы поставщикам')

if 'result_token' not in st.session_state:
    st.markdown('Загрузите выгрузку продаж в боковой панели или посмотрите, как работает сервис, на демо-данных.')
    if st.button('Показать на демо-данных', type='primary', key='demo_start'):
        with st.spinner('Расчёт на демо-данных… до минуты'):
            try:
                calculate(True)
            except Exception as exc:
                st.error(public_error(exc, principal.subject))
                st.stop()
        st.rerun()
    st.stop()

result_token = st.session_state.result_token
try:
    service.resolve(result_token, principal, session_key)
except Exception as exc:
    for key in ['result_token', 'results_data', 'approved_file']:
        st.session_state.pop(key, None)
    st.error(public_error(exc, principal.subject))
    st.stop()
orders, daily, future = st.session_state.results_data
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
        hide_index=True, width='stretch', key=f'editor_{result_token}_{supplier}')
    can_approve = bool({'approve', 'approve_demo'} & PERMISSIONS[principal.role])
    st.caption('Ответственный определяется учётной записью; в демо утверждение учебное.')
    if st.button('Утвердить заказ выбранному поставщику', disabled=not can_approve, key='approve'):
        try:
            edits = edited[['Артикул', 'Утверждённое количество']].rename(
                columns={'Артикул': 'sku', 'Утверждённое количество': 'approved_qty'})
            st.session_state.approved_file = service.approve(result_token, principal, session_key, supplier, edits)
        except Exception as exc:
            st.session_state.pop('approved_file', None)
            st.error(public_error(exc, principal.subject))
    if 'approved_file' in st.session_state:
        stem, xlsx_bytes, csv_bytes = st.session_state.approved_file
        st.success(f'Заказ утверждён: {stem}. Скачайте файл и передайте поставщику.')
        left, right = st.columns(2)
        left.download_button('Утверждённый заказ (XLSX)', xlsx_bytes, f'{stem}.xlsx', key='dl_approved_xlsx')
        right.download_button('Утверждённый заказ для 1С (CSV)', csv_bytes, f'{stem}_1c.csv', key='dl_approved_csv')
    try:
        all_xlsx = service.download(result_token, principal, session_key)
        st.download_button('Скачать все заказы (XLSX)', all_xlsx, 'supplier_orders.xlsx', key='dl_all')
    except Exception as exc:
        st.error(public_error(exc, principal.subject))

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
