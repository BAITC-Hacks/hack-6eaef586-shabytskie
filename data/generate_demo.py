"""Create deterministic synthetic electrical-goods sales and inventory."""
import numpy as np
import pandas as pd
from contracts import InputBundle

def generate_demo(seed: int = 23, skus: int = 40, days: int = 210) -> InputBundle:
    rng = np.random.default_rng(seed)
    suppliers = pd.DataFrame({"supplier": [f"SUP-{i:02}" for i in range(1, 5)], "lead_time_days": [5, 9, 14, 21]})
    categories = ["Кабель", "Автоматика", "Освещение", "Инструмент"]
    dates = pd.date_range("2025-01-01", periods=days)
    sales, stock_hist, inventory = [], [], []
    for i in range(skus):
        sku=f"SKU-{i+1:03}"; cat=categories[i%4]; base=0 if i==skus-1 else 5+(i%9)*1.7
        trend=.0015 if i%7==1 else 0.0
        seasonal=.35*np.sin(np.arange(days)*2*np.pi/90) if i%8==2 else 0
        q=np.maximum(0,rng.poisson(np.maximum(.1,base*(1+trend*np.arange(days)+seasonal))))
        if i%7==1: q[100:]=(q[100:]*1.25).astype(int)
        if i%11==3: q[130:139]=0
        if i%13==4: q[155]+=500
        stock=float(rng.integers(15,240)); stock=900.0 if i%9==5 else stock
        transit=float(rng.integers(0,70)); transit=250.0 if i%10==6 else transit
        unit_cost=round(float(rng.uniform(80,14000)),2)
        for day_index,(d,n) in enumerate(zip(dates,q)):
            client=f"CLIENT-{(i*17+day_index)%120+1:04}"
            if i%13==4 and day_index==155: client=f"CLIENT-ONEOFF-{i+1:03}"
            sales.append({"date":d,"sku":sku,"quantity":float(n),"client_id":client,"price":round(unit_cost*1.22,2),"warehouse":"WH-01"})
        stock_hist.extend({"date":d,"sku":sku,"in_stock":not(i%11==3 and dates[130]<=d<dates[139])} for d in dates)
        inventory.append({"sku":sku,"product_name":f"{cat} {i+1:03}","category":cat,"supplier":f"SUP-{i%4+1:02}","current_stock":stock,"in_transit":transit,"growth_rate":.12 if i%7==1 else 0.0,"unit":"шт.","unit_cost":unit_cost,"currency":"KZT","moq":1.0,"order_multiple":1.0})
    return InputBundle(pd.DataFrame(sales),pd.DataFrame(inventory),suppliers,pd.DataFrame(stock_hist))

