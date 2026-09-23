"""Plotly visualizations that render only diagnostics providers supply."""
import plotly.express as px
import streamlit as st

def priority_chart(frame):
    counts=frame.priority.value_counts().reindex(["HIGH","MEDIUM","LOW"],fill_value=0).rename_axis("priority").reset_index(name="sku_count")
    fig=px.bar(counts,x="priority",y="sku_count",color="priority",color_discrete_map={"HIGH":"#c2413b","MEDIUM":"#c58a24","LOW":"#43836f"})
    fig.update_layout(showlegend=False,height=260,margin=dict(l=8,r=8,t=12,b=8),xaxis_title="Приоритет",yaxis_title="SKU")
    st.plotly_chart(fig,use_container_width=True)

def sku_chart(history,forecast,sku):
    import pandas as pd
    h=history[history.sku.astype(str)==str(sku)].copy() if not history.empty and "sku" in history else history.iloc[0:0]
    f=forecast[forecast.sku.astype(str)==str(sku)].copy() if not forecast.empty and "sku" in forecast else forecast.iloc[0:0]
    if h.empty and f.empty: st.info("История и прогноз для SKU не предоставлены поставщиком."); return
    traces=[]
    if not h.empty and "actual_sales" in h: traces.append(pd.DataFrame({"date":pd.to_datetime(h.date),"value":h.actual_sales,"series":"Фактические продажи"}))
    if not h.empty and "adjusted_demand" in h: traces.append(pd.DataFrame({"date":pd.to_datetime(h.date),"value":h.adjusted_demand,"series":"Скорректированный спрос"}))
    if not f.empty and "predicted_demand" in f: traces.append(pd.DataFrame({"date":pd.to_datetime(f.date),"value":f.predicted_demand,"series":"Прогноз"}))
    if traces:
        fig=px.line(pd.concat(traces),x="date",y="value",color="series",labels={"date":"Дата","value":"Количество","series":"Ряд"})
        st.plotly_chart(fig,use_container_width=True)

