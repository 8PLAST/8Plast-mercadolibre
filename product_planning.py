"""Familias y recomendaciones operativas de producción, sin modificar stock."""
from __future__ import annotations

import math

TARGET_COVERAGE_DAYS=30
MAX_COVERAGE_DAYS=45
SPIKE_RATIO=2.5
FAMILY_LABELS={"TODOS":"Todos","CONSORCIO":"Consorcio","ROLLOS":"Rollos","LEÑA_CARBON":"Leña y carbón","POLIETILENO":"Polietileno","ESCOMBRO":"Escombro","SIN_CLASIFICAR":"Sin clasificar"}
PRODUCTION_ORDER={"FABRICAR AHORA":0,"PRIORIDAD ALTA":1,"PRIORIDAD MEDIA":2,"PUEDE ESPERAR":3,"NO REQUIERE PRODUCCIÓN":4,"DATOS INCOMPLETOS":5}

def product_family(product):
    """Clasifica primero por campos estructurados; SKU sólo confirma la categoría."""
    kind=str(product.get("kind","")).upper(); category=str(product.get("category","")).strip().casefold(); sku=str(product.get("sku","")).upper()
    if kind=="ROLL":return "ROLLOS"
    if category=="consorcio" and sku.startswith("CON-"):return "CONSORCIO"
    if category in {"leña","lena","carbón","carbon"} and sku.startswith(("LENA-","LEÑA-","CARB-")):return "LEÑA_CARBON"
    if category=="cristal" and sku.startswith("CRI-"):return "POLIETILENO"
    if category=="escombro" and sku.startswith("ESC-"):return "ESCOMBRO"
    return "SIN_CLASIFICAR"

def production_recommendation(product,metric):
    stock=int(product.get("stock",0)); minimum=int(product.get("minimum_stock",0)); target=int(product.get("target_stock",0))
    velocity=float(metric.get("adjusted_velocity",0) or 0); v7=float(metric.get("velocity_7",0) or 0); v90=float(metric.get("velocity_90",0) or 0); v180=float(metric.get("velocity_180",0) or 0)
    coverage=metric.get("coverage"); trend=metric.get("trend","ESTABLE"); classification=metric.get("classification","SIN VENTAS RECIENTES"); complete=bool(metric.get("data_complete"))
    family=product_family(product); unit="rollos" if product.get("kind")=="ROLL" else "bolsas"
    history=max((v90+v180)/2,0); spike=bool(history>0 and v7>history*SPIKE_RATIO)
    effective=velocity
    if spike:effective=min(effective,history*1.75)
    if trend=="FUERTE CAÍDA":effective*=.70
    elif trend=="EN CAÍDA":effective*=.85
    desired=max(0,math.ceil(effective*TARGET_COVERAGE_DAYS-stock)); target_gap=max(0,target-stock)
    suggested=max(desired,target_gap)
    cap=max(target_gap,math.ceil(effective*MAX_COVERAGE_DAYS),target)
    suggested=min(suggested,cap)
    review=False
    if not complete:suggested=0;review=True
    elif coverage is not None and coverage>=TARGET_COVERAGE_DAYS:suggested=0
    elif classification=="SIN VENTAS RECIENTES":suggested=target_gap if stock<minimum else 0
    elif classification=="BAJA ROTACIÓN" and trend in {"EN CAÍDA","FUERTE CAÍDA"}:
        suggested=min(suggested,target_gap);review=bool(suggested)
    if not complete:level="DATOS INCOMPLETOS"
    elif suggested<=0:level="NO REQUIERE PRODUCCIÓN"
    elif stock<=0 or (coverage is not None and coverage<7):level="FABRICAR AHORA"
    elif coverage is not None and coverage<15:level="PRIORIDAD ALTA"
    elif coverage is not None and coverage<30 or stock<minimum:level="PRIORIDAD MEDIA"
    else:level="PUEDE ESPERAR"
    reasons=[]
    if not complete:reasons.append("Datos incompletos; revisar asociaciones")
    else:
        if coverage is not None and coverage<7:reasons.append(f"Cobertura crítica: {coverage:.1f} días")
        elif coverage is not None and coverage<15:reasons.append(f"Cobertura baja: {coverage:.1f} días")
        elif coverage is not None and coverage>=30:reasons.append("Cobertura saludable; puede esperar")
        if classification=="BEST SELLER" and coverage is not None and coverage<30:reasons.append("Best Seller con cobertura limitada")
        if trend in {"EN CRECIMIENTO","FUERTE CRECIMIENTO"}:reasons.append("Demanda acelerándose")
        elif trend in {"EN CAÍDA","FUERTE CAÍDA"}:reasons.append("Demanda desacelerándose")
        if stock<minimum:reasons.append("Stock debajo del mínimo")
        if spike:reasons.append("Pico reciente limitado por historial")
        if not reasons:reasons.append("Demanda y stock en niveles saludables")
    variability=(abs(v7-v180)/v180) if v180>0 else 9
    if not complete:confidence="BAJA"
    elif metric.get("sales_180",0)>=30 and variability<=1.5:confidence="ALTA"
    else:confidence="MEDIA"
    calculation=f"Cobertura objetivo {TARGET_COVERAGE_DAYS} días: {effective:.2f}/día × {TARGET_COVERAGE_DAYS} − stock {stock}; comparado con faltante al objetivo {target_gap}."
    return {"family":family,"family_label":FAMILY_LABELS[family],"production_priority":level,"suggested":int(max(0,suggested)),"unit":unit,
        "reasons":reasons[:3],"reason":" + ".join(reasons[:2]),"confidence":confidence,"effective_velocity":effective,"spike_limited":spike,
        "review_recommended":review,"calculation":calculation,"target_coverage_days":TARGET_COVERAGE_DAYS}

def build_production_plan(products,metrics):
    plan=[]
    for product in products:
        row=dict(product); row.update(metrics.get(product["id"],{})); row.update(production_recommendation(row,metrics.get(product["id"],{}))); plan.append(row)
    plan.sort(key=lambda p:(PRODUCTION_ORDER[p["production_priority"]],p["coverage"] if p.get("coverage") is not None else 10**9,-p.get("adjusted_velocity",0),p["name"].casefold()))
    for index,row in enumerate(plan,1):row["production_order"]=index
    return plan
