"""Validación reproducible de cinco productos contra órdenes ML persistidas."""
import json
import sys
from collections import defaultdict
from datetime import datetime, timedelta, timezone
from pathlib import Path

sys.path.insert(0,str(Path(__file__).resolve().parents[1]))

from db import Database
from marketplace_analytics import marketplace_statistics, parse_date

db=Database()
with db.session() as con:
    metrics,_,_,_=marketplace_statistics(con)
    products={row["id"]:dict(row) for row in con.execute("SELECT * FROM products")}
    associations={(row["listing_id"].upper(),str(row["variation_id"] or "")):dict(row)
                  for row in con.execute("SELECT * FROM marketplace_listings WHERE active=1")}
    selected=sorted(metrics,key=lambda pid:metrics[pid]["sales_180"],reverse=True)[:5]
    raw={pid:defaultdict(lambda:{"ml_quantity":0,"factor":0,"physical":0}) for pid in selected}
    start=datetime.now(timezone.utc)-timedelta(days=180)
    for row in con.execute("SELECT date_created,payload_json FROM marketplace_audit_orders"):
        created=parse_date(row["date_created"])
        if not created or created<start:continue
        order=json.loads(row["payload_json"])
        if str(order.get("status","")).lower() not in {"paid","confirmed"}:continue
        for line in order.get("order_items") or []:
            item=line.get("item") or {}; key=(str(item.get("id") or "").upper(),str(item.get("variation_id") or "")); assoc=associations.get(key)
            if not assoc or assoc["product_id"] not in raw:continue
            quantity=int(line.get("quantity") or 0); factor=int(assoc["units_consumed"])
            entry=raw[assoc["product_id"]][key]; entry["ml_quantity"]+=quantity; entry["factor"]=factor; entry["physical"]+=quantity*factor
    for pid in selected:
        calculated=sum(item["physical"] for item in raw[pid].values())
        print(json.dumps({"sku":products[pid]["sku"],"product":products[pid]["name"],
            "publications":[{"listing":key[0],"variation":key[1],**value} for key,value in raw[pid].items()],
            "physical_total":calculated,"analytics_total":metrics[pid]["sales_180"],"matches":calculated==metrics[pid]["sales_180"]},ensure_ascii=False))
