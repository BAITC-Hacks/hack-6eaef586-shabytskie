# StockPilot demo walkthrough

1. In the sidebar, click **Загрузить демонстрационные данные**. Point out the `SYNTHETIC DATA` and `DEMO ENGINE` badges.
2. Review the validated sales, inventory, supplier and availability-history samples. Explain that this data is deterministic and its scenario flags are synthetic fixtures.
3. Click **Рассчитать рекомендации**. On the overview, inspect SKU count, purchase lines, priorities, and supplier grouping. Cost is shown only when prices and a single currency are present.
4. Open the SKU view and select a HIGH-priority product. Walk through the trailing demand, forecast horizon, current stock, transit, safety stock and recommendation reason. The demo has no stockout correction/seasonal model, so these are explicitly unavailable.
5. In recommendations, filter by supplier and search for the chosen SKU. Export the visible rows as CSV if useful.
6. Open **Проверка заказа**. Deselect lines or adjust a final quantity and add a comment, then click **Подтвердить состав черновика**.
7. Download the Excel review workbook and the approved-lines CSV. Explain that the workbook records the run metadata and the approved CSV is a local demo artifact.
8. Close with the boundary: **no purchase order was sent**. Supplier submission and ERP writes are intentionally absent.

