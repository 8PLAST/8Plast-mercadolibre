"""Analítica comercial de Mercado Libre sobre órdenes reales persistidas."""
from __future__ import annotations

import json
from collections import defaultdict
from datetime import datetime, timedelta, timezone

from stock_rules import stock_facts

WINDOWS=(7,30,90,180)
VELOCITY_WEIGHTS={7:0.35,30:0.35,90:0.20,180:0.10}
COVERAGE_THRESHOLDS={"critical":7,"low":15,"medium":30,"overstock":120}
TREND_THRESHOLDS={"strong_growth":0.60,"growth":0.20,"decline":-0.20,"strong_decline":-0.60,"minimum_recent_units":3}
PRIORITY_ORDER={"URGENTE":0,"ALTA":1,"MEDIA":2,"BAJA":3,"DATOS INCOMPLETOS":4}

def parse_date(value):
    if not value:return None
    try:return datetime.fromisoformat(str(value).replace("Z","+00:00")).astimezone(timezone.utc)
    except (TypeError,ValueError):return None

def coverage_label(days):
    if days is None:return "SIN DATOS"
    if days<COVERAGE_THRESHOLDS["critical"]:return "CRÍTICA"
    if days<COVERAGE_THRESHOLDS["low"]:return "BAJA"
    if days<=COVERAGE_THRESHOLDS["medium"]:return "MEDIA"
    return "SALUDABLE"

def adjusted_velocity(sales):
    return sum((sales.get(days,0)/days)*weight for days,weight in VELOCITY_WEIGHTS.items())

def trend_for(sales):
    recent=((sales.get(7,0)/7)+(sales.get(30,0)/30))/2
    historic=((sales.get(90,0)/90)+(sales.get(180,0)/180))/2
    if sales.get(30,0)<TREND_THRESHOLDS["minimum_recent_units"] or historic<=0:
        return (("ESTABLE",0.0) if recent<=0 else ("EN CRECIMIENTO",1.0))
    change=(recent-historic)/historic
    if change>=TREND_THRESHOLDS["strong_growth"]:label="FUERTE CRECIMIENTO"
    elif change>=TREND_THRESHOLDS["growth"]:label="EN CRECIMIENTO"
    elif change<=TREND_THRESHOLDS["strong_decline"]:label="FUERTE CAÍDA"
    elif change<=TREND_THRESHOLDS["decline"]:label="EN CAÍDA"
    else:label="ESTABLE"
    return label,change

def commercial_classification(sales,velocity,trend,rank_ratio=1.0):
    if sales.get(30,0)==0:return "SIN VENTAS RECIENTES"
    consistent=sum(1 for d in WINDOWS if sales.get(d,0)>0)>=3
    if rank_ratio<=0.10 and consistent and sales.get(180,0)>0:return "BEST SELLER"
    if rank_ratio<=0.30 and velocity>0:return "ALTA ROTACIÓN"
    if rank_ratio<=0.65:return "ROTACIÓN MEDIA"
    return "BAJA ROTACIÓN"

def traditional_priority(product):
    state=stock_facts(product)["state"]
    if state=="SIN STOCK":return "URGENTE"
    if state=="REPONER":return "ALTA"
    if state=="BAJO":return "MEDIA"
    return "BAJA"

def replenishment_priority_v2(product,coverage,trend,classification,data_complete=True):
    """Cobertura manda; tendencia y clasificación sólo elevan un nivel."""
    if not data_complete:return "DATOS INCOMPLETOS"
    if product["stock"]<=0 or (coverage is not None and coverage<7):return "URGENTE"
    if coverage is not None and coverage<15:return "ALTA"
    if coverage is not None and coverage<=30:return "MEDIA"
    base=traditional_priority(product)
    if trend in {"FUERTE CRECIMIENTO","EN CRECIMIENTO"} or classification in {"BEST SELLER","ALTA ROTACIÓN"}:
        return {"BAJA":"MEDIA","MEDIA":"ALTA","ALTA":"URGENTE"}.get(base,base)
    return base

def _table_exists(connection,name):
    return bool(connection.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name=?",(name,)).fetchone())

def marketplace_statistics(connection,now=None):
    now=(now or datetime.now(timezone.utc)).astimezone(timezone.utc)
    products={r["id"]:dict(r) for r in connection.execute("SELECT * FROM products WHERE active=1")}
    associations=list(connection.execute("SELECT * FROM marketplace_listings WHERE marketplace='MERCADOLIBRE' AND active=1"))
    by_item={(str(r["listing_id"]).upper(),str(r["variation_id"] or "")):r for r in associations}
    metrics={pid:{"listings":[],"published_stock":0,"published_known":False,"monthly":{},**{f"sales_{d}":0 for d in WINDOWS}} for pid in products}
    for row in associations:
        metric=metrics.get(row["product_id"])
        if not metric:continue
        metric["listings"].append(dict(row))
        if row["last_published_quantity"] is not None:
            metric["published_known"]=True; metric["published_stock"]+=int(row["last_published_quantity"])*int(row["units_consumed"])
    orders={}
    if _table_exists(connection,"marketplace_audit_orders"):
        for row in connection.execute("SELECT order_id,date_created,payload_json FROM marketplace_audit_orders WHERE marketplace='MERCADOLIBRE'"):orders[str(row["order_id"])]=row
    for row in connection.execute("SELECT order_id,date_created,payload_json FROM marketplace_order_inbox WHERE marketplace='MERCADOLIBRE'"):orders[str(row["order_id"])]=row
    state=dict(connection.execute("SELECT * FROM marketplace_audit_state WHERE id=1").fetchone() or {}) if _table_exists(connection,"marketplace_audit_state") else {}
    period_start=now-timedelta(days=180); unlinked={}; publications=set(); involved_products=set()
    report={"orders_found":0,"items_found":0,"associated_items":0,"unassociated_items":0,"factor_missing":0,"publications":0,"products":0,
        "period_start":period_start.isoformat(),"period_end":now.isoformat(),"status":state.get("status","PENDING"),"last_error":state.get("last_error"),
        "last_update":state.get("completed_at") or state.get("updated_at")}
    monthly=defaultdict(lambda:defaultdict(int))
    for stored in orders.values():
        created=parse_date(stored["date_created"])
        if created is None or created<period_start or created>now:continue
        try:order=json.loads(stored["payload_json"])
        except (TypeError,json.JSONDecodeError):continue
        if str(order.get("status","")).lower() not in {"paid","confirmed"}:continue
        report["orders_found"]+=1; age=(now-created).total_seconds()/86400
        for line in order.get("order_items") or []:
            report["items_found"]+=1; item=line.get("item") or {}; listing=str(item.get("id") or "").upper(); variation=str(item.get("variation_id") or "")
            publications.add((listing,variation)); association=by_item.get((listing,variation)); quantity=int(line.get("quantity") or 0)
            if not association:
                report["unassociated_items"]+=1; unlinked[(listing,variation)]={"listing_id":listing,"variation_id":variation,"title":str(item.get("title") or ""),"seller_sku":str(item.get("seller_sku") or item.get("seller_custom_field") or "").strip()}; continue
            factor=int(association["units_consumed"] or 0)
            if factor<=0:report["factor_missing"]+=1;continue
            physical=quantity*factor; pid=association["product_id"]; involved_products.add(pid); report["associated_items"]+=1
            metric=metrics.get(pid)
            if not metric:continue
            for days in WINDOWS:
                if age<=days:metric[f"sales_{days}"]+=physical
            month=created.strftime("%Y-%m"); metric["monthly"][month]=metric["monthly"].get(month,0)+physical
            monthly[created.strftime("%Y-%m")][products[pid].get("kind","OTRA")]+=physical
    report["publications"]=len(publications); report["products"]=len(involved_products)
    audit_start=parse_date(state.get("period_start")); audit_end=parse_date(state.get("period_end"))
    report["complete"]=bool(state.get("status")=="COMPLETE" and audit_start and audit_start<=period_start+timedelta(hours=2) and audit_end and audit_end>=now-timedelta(hours=2) and not state.get("last_error") and report["factor_missing"]==0)
    velocities={pid:adjusted_velocity({d:m[f"sales_{d}"] for d in WINDOWS}) for pid,m in metrics.items()}
    ranked=sorted(velocities,key=lambda pid:(velocities[pid],metrics[pid]["sales_180"]),reverse=True); rank_pos={pid:(i+1)/max(1,len(ranked)) for i,pid in enumerate(ranked)}
    for pid,metric in metrics.items():
        sales={d:metric[f"sales_{d}"] for d in WINDOWS}; velocity=velocities[pid]; trend,change=trend_for(sales); classification=commercial_classification(sales,velocity,trend,rank_pos[pid]); coverage=products[pid]["stock"]/velocity if velocity>0 else None; data_complete=report["complete"] and bool(metric["listings"])
        metric.update({"velocity":sales[30]/30,"adjusted_velocity":velocity,"velocity_7":sales[7]/7,"velocity_30":sales[30]/30,"velocity_90":sales[90]/90,"velocity_180":sales[180]/180,"coverage":coverage,"coverage_state":coverage_label(coverage),"trend":trend,"trend_change":change,"classification":classification,"priority":replenishment_priority_v2(products[pid],coverage,trend,classification,data_complete),"traditional_priority":traditional_priority(products[pid]),"data_complete":data_complete,"linked":bool(metric["listings"])})
    skus={str(p["sku"]).strip().casefold():p for p in products.values()}
    for item in unlinked.values():item["suggested_product"]=skus.get(item["seller_sku"].casefold()) if item["seller_sku"] else None
    return metrics,list(unlinked.values()),report,{month:dict(values) for month,values in sorted(monthly.items())}

def marketplace_metrics(connection,now=None):
    metrics,unlinked,_,_=marketplace_statistics(connection,now); return metrics,unlinked

def replenishment_priority(product,velocity,coverage):
    return replenishment_priority_v2(product,coverage,"ESTABLE","ROTACIÓN MEDIA",True)
