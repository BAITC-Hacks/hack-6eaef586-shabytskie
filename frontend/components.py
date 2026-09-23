"""Reusable StockPilot presentation helpers."""
import streamlit as st

def hero(mode: str, origin: str):
    st.markdown(f"<div class='status-row'><span class='badge'>{mode.upper()} ENGINE</span><span class='badge soft'>{origin.upper()} DATA</span></div>", unsafe_allow_html=True)

def metric_row(items):
    cols=st.columns(len(items))
    for col,(label,value,hint) in zip(cols,items): col.metric(label,value,help=hint)

