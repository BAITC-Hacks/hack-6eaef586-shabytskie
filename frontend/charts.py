"""Plotly visualizations that render only diagnostics providers supply."""
import plotly.express as px
import plotly.graph_objects as go
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
    fig=go.Figure()
    if not h.empty and "actual_sales" in h:
        fig.add_trace(go.Scatter(x=pd.to_datetime(h.date),y=h.actual_sales,name="Фактические продажи",line=dict(color="#53645f",width=1.5)))
    if not h.empty and "adjusted_demand" in h:
        fig.add_trace(go.Scatter(x=pd.to_datetime(h.date),y=h.adjusted_demand,name="Скорректированный спрос",line=dict(color="#2a7a64",width=2)))
    if not f.empty and "predicted_demand" in f:
        dates=pd.to_datetime(f.date)
        if {"lower_bound","upper_bound"} <= set(f.columns) and f[["lower_bound","upper_bound"]].notna().all().all():
            fig.add_trace(go.Scatter(x=list(dates)+list(dates[::-1]),y=list(f.upper_bound)+list(f.lower_bound[::-1]),fill="toself",fillcolor="rgba(42,122,100,.12)",line=dict(color="rgba(255,255,255,0)"),hoverinfo="skip",name="Интервал прогноза"))
        fig.add_trace(go.Scatter(x=dates,y=f.predicted_demand,name="Прогноз",line=dict(color="#d48a2d",width=2.5,dash="dash")))
    if not h.empty and "is_anomaly" in h and h.is_anomaly.fillna(False).any():
        marked=h[h.is_anomaly.fillna(False)]
        y=marked.adjusted_demand if "adjusted_demand" in marked else marked.actual_sales
        fig.add_trace(go.Scatter(x=pd.to_datetime(marked.date),y=y,mode="markers",name="Аномалия",marker=dict(color="#c54d48",size=9,symbol="x")))
    if not h.empty and "is_stockout" in h and h.is_stockout.fillna(False).any():
        marked=h[h.is_stockout.fillna(False)]
        y=marked.adjusted_demand if "adjusted_demand" in marked else marked.actual_sales
        fig.add_trace(go.Scatter(x=pd.to_datetime(marked.date),y=y,mode="markers",name="Дефицит",marker=dict(color="#7b61a8",size=8,symbol="diamond-open")))
    fig.update_layout(height=390,hovermode="x unified",legend=dict(orientation="h",yanchor="bottom",y=1.02,x=0),margin=dict(l=8,r=8,t=52,b=8),xaxis_title=None,yaxis_title="Количество",plot_bgcolor="#ffffff")
    st.plotly_chart(fig,use_container_width=True)

