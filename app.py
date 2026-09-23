"""StockPilot purchasing dashboard: streamlit run app.py"""
from __future__ import annotations
import hashlib
from datetime import date
import pandas as pd
import streamlit as st
from contracts import AnalysisSettings, InputBundle
from frontend.charts import priority_chart, sku_chart
from frontend.components import hero, metric_row
from frontend.data_io import read_table, read_workbook, templates_xlsx
from frontend.exports import csv_bytes, workbook_bytes
from frontend.validation import validate_bundle
from integration.adapter import run_analysis
from data.generate_demo import generate_demo

st.set_page_config(page_title="StockPilot · закупки",page_icon="📦",layout="wide")
st.markdown("""<style>
.stApp{font-family:system-ui,sans-serif;background:#f5f7f6}.block-container{max-width:1440px;padding-top:1.6rem}
.status-row{display:flex;gap:8px;margin:0 0 1rem}.badge{padding:5px 10px;border-radius:99px;background:#173e36;color:#fff;font-size:11px;font-weight:800;letter-spacing:.08em}.badge.soft{background:#e2ede8;color:#285c4c}
div[data-testid=stMetric]{background:#fff;padding:14px 17px;border:1px solid #e4e9e6;border-radius:12px}
h1,h2,h3{letter-spacing:-.025em}.stButton>button{border-radius:9px;font-weight:700}.stDownloadButton>button{border-radius:9px}
</style>""",unsafe_allow_html=True)
st.title("StockPilot")
st.caption("Рекомендации по пополнению запасов · Электрокомплект")

for k,v in {"bundle":None,"result":None,"input_fingerprint":None,"approved":False,"approved_signature":None,"review":{}}.items(): st.session_state.setdefault(k,v)

def fingerprint(bundle,settings,mode,origin):
    h=hashlib.sha256()
    for df in (bundle.sales,bundle.inventory,bundle.suppliers,bundle.stock_history):
        h.update(b"none" if df is None else pd.util.hash_pandas_object(df,index=True).values.tobytes())
    h.update(repr(settings).encode()); h.update(mode.encode()); h.update(origin.encode()); return h.hexdigest()

with st.sidebar:
    st.header("Данные и расчёт")
    if st.button("Загрузить демонстрационные данные",use_container_width=True):
        st.session_state.bundle=generate_demo(); st.session_state.data_origin="synthetic"; st.session_state.result=None; st.session_state.approved=False; st.session_state.force_demo=True
    if st.button("Использовать загруженные файлы",use_container_width=True): st.session_state.force_demo=False
    xlsx=st.file_uploader("Книга Excel (.xlsx)",type=["xlsx"],key="xlsx")
    st.caption("Или загрузите CSV отдельно")
    sf=st.file_uploader("Продажи CSV",type=["csv"],key="sales")
    inf=st.file_uploader("Остатки CSV",type=["csv"],key="inventory")
    supf=st.file_uploader("Поставщики CSV",type=["csv"],key="suppliers")
    shf=st.file_uploader("История наличия CSV · необязательно",type=["csv"],key="stock_history")
    if not st.session_state.get("force_demo",False) and xlsx:
        try: st.session_state.bundle=read_workbook(xlsx); st.session_state.data_origin="uploaded"
        except Exception as exc: st.error(f"Не удалось прочитать книгу: {exc}")
    elif not st.session_state.get("force_demo",False) and sf and inf and supf:
        try:
            st.session_state.bundle=InputBundle(read_table(sf,"sales"),read_table(inf,"inventory"),read_table(supf,"suppliers"),read_table(shf,"stock_history") if shf else None)
            st.session_state.data_origin="uploaded"
        except Exception as exc: st.error(f"Не удалось прочитать CSV: {exc}")
    st.download_button("Скачать Excel-шаблоны",templates_xlsx(),"stockpilot_templates.xlsx",use_container_width=True)
    st.divider()
    mode=st.selectbox("Режим движка",["demo","integrated"],format_func=lambda x:"Демо расчёт" if x=="demo" else "Интегрированный")
    st.caption("Интеграция вызывает Person 1 forecast() и затем Person 2 recommend(). Автоматического переключения в демо нет.")
    as_of=st.date_input("Дата расчёта",value=date.today())
    review_days=st.number_input("Период между проверками, дней",1,90,7)
    safety_days=st.number_input("Страховой запас, дней",0,90,7,disabled=mode=="integrated",help="Демо настройка. Интегрированный backend использует собственную политику страхового запаса.")
    growth_override=st.number_input("Ожидаемый рост (опционально), %",-90,300,0)
    override_enabled=st.checkbox("Применить общий рост",False)
    anomaly=st.checkbox("Коррекция выбросов",mode=="integrated",disabled=True,help="Интегрированный backend применяет её всегда; демо provider её не поддерживает.")
    stockout=st.checkbox("Коррекция дефицита",mode=="integrated",disabled=True,help="Интегрированный backend применяет её при наличии stockout данных; демо provider её не поддерживает.")
    settings=AnalysisSettings(as_of,int(review_days),int(safety_days),growth_override/100 if override_enabled else None,anomaly,stockout)

bundle=st.session_state.bundle
if bundle is None:
    st.info("Загрузите файлы или выберите демонстрационные данные, чтобы начать.")
    st.markdown("**Поддерживаются:** одна книга с листами `sales`, `inventory`, `suppliers` и необязательным `stock_history`, либо четыре CSV.")
    st.stop()
origin=st.session_state.get("data_origin","uploaded")
hero(mode,origin)
problems=validate_bundle(bundle)
with st.expander("Проверка данных и предпросмотр",expanded=bool(problems)):
    if problems:
        for problem in problems: st.error(problem)
    else: st.success("Схемы и связи таблиц корректны. Загрузчик сохраняет строки; очистка спроса принадлежит forecasting provider.")
    for name,df in [("Продажи",bundle.sales),("Остатки",bundle.inventory),("Поставщики",bundle.suppliers),("История наличия",bundle.stock_history)]:
        if df is not None: st.markdown(f"**{name}** · {len(df):,} строк"); st.dataframe(df.head(8),use_container_width=True,hide_index=True)
current_fp=fingerprint(bundle,settings,mode,origin)
outdated=st.session_state.result is not None and st.session_state.input_fingerprint!=current_fp
if outdated: st.warning("Данные или настройки изменились. Результат устарел; пересчитайте анализ для сброса подтверждения.")
if st.button("Рассчитать рекомендации",type="primary",disabled=bool(problems)):
    with st.spinner("Подготавливаем рекомендации…"):
        try:
            result=run_analysis(bundle,settings,mode); result.metadata["data_origin"]=origin
            st.session_state.result=result; st.session_state.input_fingerprint=current_fp; st.session_state.review={}; st.session_state.approved=False; st.session_state.approved_signature=None
            st.success("Расчёт завершён.")
        except Exception as exc: st.error(f"Расчёт не выполнен: {exc}")
result=st.session_state.result
if result is None: st.stop()
if outdated: st.stop()
rec=result.recommendations.copy(); buy=rec[rec.recommended_order>0]; high=buy[buy.priority.astype(str).str.upper()=="HIGH"]
hero(result.metadata.get("engine_mode",mode),result.metadata.get("data_origin",origin))
st.caption(f"Расчёт {result.metadata.get('run_id')} · {result.metadata.get('calculated_at')} · {result.metadata.get('description',result.metadata.get('provider',''))}")
for warning in result.metadata.get("warnings",[]): st.warning(warning)
cost_ok=rec.estimated_order_cost.notna() if "estimated_order_cost" in rec else pd.Series(False,index=rec.index)
currencies=rec.loc[cost_ok,"currency"].dropna().unique() if "currency" in rec else []
cost_text=f"{rec.loc[cost_ok,'estimated_order_cost'].sum():,.0f} {currencies[0]}" if cost_ok.any() and len(currencies)==1 else "—"
metric_row([("SKU в расчёте",f"{rec.sku.nunique():,}","Уникальные SKU"),("Нужна закупка",f"{len(buy):,}","Количество больше нуля"),("Высокий приоритет",f"{len(high):,}","Категориальная метка, не вероятность"),("Поставщиков",f"{buy.supplier.nunique():,}","С предложенными закупками"),("Оценка стоимости",cost_text,"Только при известной цене и одной валюте")])
overview,recommendations,detail,quality_tab,review_tab=st.tabs(["Обзор","Рекомендации","SKU","Качество и метод","Проверка заказа"])
with overview:
    left,right=st.columns([1,1.4])
    with left: st.subheader("Приоритеты"); priority_chart(rec)
    with right:
        st.subheader("Поставщики")
        summary=buy.groupby("supplier",as_index=False).agg(SKU=("sku","nunique"),Количество=("recommended_order","sum"))
        st.dataframe(summary,use_container_width=True,hide_index=True)
with recommendations:
    st.subheader("Предложения поставщикам")
    c1,c2,c3,c4,c5=st.columns([1.2,1,1,1,1])
    query=c1.text_input("Поиск SKU или названия")
    suppliers=c2.multiselect("Поставщик",sorted(rec.supplier.astype(str).unique()))
    categories=c3.multiselect("Категория",sorted(rec.category.dropna().astype(str).unique()) if "category" in rec else [])
    priorities=c4.multiselect("Приоритет",["HIGH","MEDIUM","LOW"],default=["HIGH","MEDIUM","LOW"])
    only=c5.checkbox("Только к закупке",True)
    visible=rec.copy()
    if query:
        names=visible.product_name.astype(str) if "product_name" in visible else visible.sku.astype(str)
        visible=visible[visible.sku.astype(str).str.contains(query,case=False)|names.str.contains(query,case=False)]
    if suppliers: visible=visible[visible.supplier.astype(str).isin(suppliers)]
    if categories and "category" in visible: visible=visible[visible.category.astype(str).isin(categories)]
    if priorities: visible=visible[visible.priority.astype(str).str.upper().isin(priorities)]
    if only: visible=visible[visible.recommended_order>0]
    st.caption(f"Отображается {len(visible)} из {len(rec)} SKU; KPI рассчитаны по полному набору.")
    cols=[c for c in ["sku","product_name","supplier","category","forecast","horizon_days","current_stock","in_transit","safety_stock","recommended_order","priority","reason"] if c in visible]
    st.dataframe(visible[cols].sort_values(["priority","recommended_order"],ascending=[True,False]),use_container_width=True,hide_index=True)
    visible_export=visible[cols].copy(); visible_export["export_status"]="DRAFT"
    st.download_button("CSV видимых рекомендаций",csv_bytes(visible_export),"stockpilot_recommendations.csv","text/csv")
with detail:
    sku=st.selectbox("Выберите SKU",rec.sku.astype(str).tolist()); row=rec[rec.sku.astype(str)==sku].iloc[0]
    st.markdown(f"### {row.get('product_name',sku)} · {sku}"); st.write(row.reason); sku_chart(result.history,result.forecast_series,sku)
    if "lost_demand" in row and pd.notna(row.lost_demand): st.metric("Оценка потерянного спроса за историю",f"{row.lost_demand:,.1f}")
    else: st.info("Оценка потерянного спроса не предоставлена выбранным provider.")
    if isinstance(row.get("calculation_breakdown"),dict): st.json(row.calculation_breakdown)
    st.write({"Поставщик":row.supplier,"Срок поставки, дней":row.lead_time_days,"Горизонт прогноза, дней":row.horizon_days})
with quality_tab:
    st.subheader("Методология и ограничения")
    st.markdown("Загрузка и валидация → forecasting provider (Person 1) → replenishment provider (Person 2) → проверка руководителем → черновик. Интерфейс не повторяет расчёты интегрированного движка.")
    st.caption("Demo provider: среднее за последние 28 дней и прозрачная арифметика заказа; без ML, stockout correction и сезонной модели.")
    st.caption("Предполагается один поставщик на SKU; агрегированный товар в пути считается прибывающим в пределах горизонта. Это не подтверждает отсутствие раннего риска дефицита.")
    st.dataframe(result.quality_report,use_container_width=True,hide_index=True)
    if bundle.stock_history is None: st.info("История наличия не загружена; stockout-коррекция недоступна и загрузчиком не выдумывается.")
    st.json(result.metadata)
with review_tab:
    st.subheader("Проверка руководителем")
    st.caption("Решения хранятся только в этой сессии. Экспорт создаёт локальный черновик; поставщику заказ не отправляется.")
    review=buy[[c for c in ["sku","product_name","supplier","recommended_order","priority","unit"] if c in buy]].copy(); run_id=result.metadata["run_id"]
    for r in review.itertuples(index=False): st.session_state.review.setdefault((run_id,str(r.sku)),{"selected":True,"quantity":float(r.recommended_order),"comment":""})
    review["selected"]=[st.session_state.review[(run_id,str(s))]["selected"] for s in review.sku]
    review["final_quantity"]=[st.session_state.review[(run_id,str(s))]["quantity"] for s in review.sku]
    review["comment"]=[st.session_state.review[(run_id,str(s))]["comment"] for s in review.sku]
    edited=st.data_editor(review,use_container_width=True,hide_index=True,num_rows="fixed",column_config={"selected":st.column_config.CheckboxColumn("В заказ"),"final_quantity":st.column_config.NumberColumn("Итоговое количество",min_value=0,step=0.1),"comment":st.column_config.TextColumn("Комментарий",max_chars=200)})
    for r in edited.itertuples(index=False): st.session_state.review[(run_id,str(r.sku))]={"selected":bool(r.selected),"quantity":float(r.final_quantity),"comment":str(r.comment)}
    review_signature=repr(sorted((str(s),v["selected"],v["quantity"],v["comment"]) for (rid,s),v in st.session_state.review.items() if rid==run_id))
    if st.session_state.approved and st.session_state.approved_signature!=review_signature:
        st.session_state.approved=False; st.session_state.approved_signature=None
    draft=edited[edited.selected&(edited.final_quantity>0)].copy().rename(columns={"recommended_order":"original_recommended_order","final_quantity":"approved_quantity"})
    if "supplier" in draft: draft=draft.sort_values(["supplier","sku"])
    changed=any(st.session_state.review[(run_id,str(r.sku))]["quantity"]!=float(buy.loc[buy.sku.astype(str)==str(r.sku),"recommended_order"].iloc[0]) for r in edited.itertuples(index=False))
    if changed: st.caption("Ручные изменения сохранены отдельно; исходная рекомендация остаётся неизменной.")
    if st.button("Подтвердить состав черновика",type="primary",key="approve"):
        st.session_state.approved=True; st.session_state.approved_signature=review_signature
    if st.session_state.approved: st.success("Состав черновика подтверждён в текущей сессии.")
    else: st.warning("Черновик не подтверждён. Подтвердите его для экспорта утверждённых строк.")
    draft["status"]="APPROVED_LOCAL" if st.session_state.approved else "DRAFT"
    st.download_button("Excel: все рекомендации и черновик",workbook_bytes(result,draft,result.quality_report),"stockpilot_review.xlsx",disabled=outdated)
    st.download_button("CSV: утверждённые строки",csv_bytes(draft),"stockpilot_approved_draft.csv",disabled=outdated or not st.session_state.approved)

