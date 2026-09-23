from datetime import datetime
from pathlib import Path
import pandas as pd
import streamlit as st
from src.config import Config
from src.ordering import EXPORT_COLUMNS, validate_approved_orders
from src.pipeline import run_pipeline

OUTPUT = Path('outputs')
ORDERS = OUTPUT / 'supplier_orders.csv'
APPROVED = OUTPUT / 'approved'
URGENCY_COLORS = {'требуются данные': '⚫', 'критично': '🔴', 'высокая': '🟠',
                  'плановая': '🟢', 'не требуется': '⚪'}

st.set_page_config(page_title='Заказы поставщикам', layout='wide')
st.title('Рекомендованные заказы поставщикам')

with st.sidebar:
    st.header('Расчёт')
    sales = st.text_input('История продаж', 'data/demo/synthetic_sales.csv')
    suppliers = st.text_input('Справочник поставщиков', 'data/demo/suppliers.csv')
    catalog = st.text_input('Номенклатура', 'data/demo/catalog.csv')
    stock = st.text_input('Текущие остатки (необязательно)', '')
    stockouts = st.text_input('Периоды отсутствия', 'data/demo/stockouts.csv')
    growth = st.text_input('Прогноз прироста', 'data/demo/category_growth.csv')
    warehouse = st.text_input('Склад (пусто = все)', '')
    category = st.text_input('Категория (пусто = все)', '')
    review = st.number_input('Период пересмотра, дн.', 1, 90, 7)
    z = st.number_input('z уровня сервиса', 0., 4., 1.65, .05)
    if st.button('Запустить расчёт', type='primary'):
        optional = lambda value: Path(value) if value.strip() else None
        with st.spinner('Расчёт…'):
            try:
                run_pipeline(Path(sales), Config(review_period_days=int(review), service_level_z=z),
                             suppliers_path=optional(suppliers), catalog_path=optional(catalog),
                             stock_path=optional(stock), stockouts_path=optional(stockouts),
                             growth_path=optional(growth), warehouse=warehouse.strip() or None,
                             category=category.strip() or None)
                st.session_state.pop('edited', None)
                st.success('Готово')
            except (ValueError, OSError) as exc:
                st.error(str(exc))

if not ORDERS.exists():
    st.info('Запустите расчёт в боковой панели.')
    st.stop()

orders = pd.read_csv(ORDERS, dtype={'sku': str})
active = orders[orders.recommended_qty > 0]
cols = st.columns(5)
cols[0].metric('Позиций к заказу', len(active))
cols[1].metric('Критичных', int((active.urgency == 'критично').sum()))
cols[2].metric('Поставщиков', active.supplier.nunique())
cols[3].metric('Сумма', f"{active.order_value.sum():,.0f}".replace(',', ' '))
cols[4].metric('Нет остатков', int((orders.status == 'заблокирован — нет актуального остатка').sum()))

tab_orders, tab_trends = st.tabs(['Заказы по поставщикам', 'Тренды по категориям'])
with tab_orders:
    supplier = st.selectbox('Поставщик', sorted(orders.supplier.unique()))
    show_all = st.checkbox('Показать позиции без заказа', False)
    view = orders[orders.supplier == supplier]
    if not show_all:
        view = view[view.recommended_qty > 0]
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
            approved = validate_approved_orders(approved)
            approved = approved[approved.approved_qty > 0]
            approved['urgency'] = approved.urgency.str.split(' ', n=1).str[-1]
            approved['order_value'] = approved.approved_qty * approved.price
            approved_at = datetime.now()
            approved['status'] = f'утверждён: {approver.strip()} {approved_at:%Y-%m-%d %H:%M:%S}'
            # Утверждение только сохраняет файлы — поставщику заказ автоматически не отправляется.
            APPROVED.mkdir(parents=True, exist_ok=True)
            safe_supplier = ''.join(c if c.isalnum() else '_' for c in supplier)
            stem = APPROVED / f"{safe_supplier}_{approved_at:%Y%m%d_%H%M%S_%f}"
            table = approved[list(EXPORT_COLUMNS)].rename(columns=EXPORT_COLUMNS)
            table.to_excel(f'{stem}.xlsx', index=False)
            table.to_csv(f'{stem}_1c.csv', sep=';', index=False, encoding='utf-8-sig')
            st.success(f'Заказ утверждён и сохранён: {stem}.xlsx (поставщику не отправляется автоматически)')
        except ValueError as exc:
            st.error(str(exc))
    xlsx = OUTPUT / 'supplier_orders.xlsx'
    if xlsx.exists():
        st.download_button('Скачать все заказы (XLSX)', xlsx.read_bytes(), 'supplier_orders.xlsx')

with tab_trends:
    daily_path = Path('data/processed/daily_demand.csv')
    if daily_path.exists():
        daily = pd.read_csv(daily_path, parse_dates=['date'], dtype={'sku': str})
        if 'category' not in daily:
            daily = daily.merge(orders[['sku', 'category']], on='sku', how='left')
        weekly = (daily.groupby([pd.Grouper(key='date', freq='W'), 'category']).adjusted_demand.sum()
                  .unstack())
        st.caption('Скорректированный спрос (без разовых заказов, с упущенным спросом), ед./неделю')
        st.line_chart(weekly)
