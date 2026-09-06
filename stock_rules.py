"""Reglas centrales de estado y reposición compartidas por escritorio y portal."""

STATUS_ORDER={"SIN STOCK":0,"REPONER":1,"BAJO":2,"OK":3,"SIN CONFIGURAR":4}


def stock_facts(product):
    stock=int(product["stock"] or 0)
    minimum=int(product["minimum_stock"] or 0)
    target=int(product["target_stock"] or 0)
    if minimum<=0 or target<=0:
        state="SIN CONFIGURAR"; missing=None
    elif stock<=0:
        state="SIN STOCK"; missing=max(target-stock,0)
    elif stock<=minimum:
        state="REPONER"; missing=max(target-stock,0)
    elif stock<target:
        state="BAJO"; missing=max(target-stock,0)
    else:
        state="OK"; missing=0
    return {"state":state,"missing":missing,"priority":STATUS_ORDER[state]}


def stock_state(product):
    return stock_facts(product)["state"]


def production_needed(product):
    return stock_facts(product)["missing"]


def state_sql(alias="p"):
    return f"""CASE
      WHEN {alias}.minimum_stock<=0 OR {alias}.target_stock<=0 THEN 'SIN CONFIGURAR'
      WHEN {alias}.stock<=0 THEN 'SIN STOCK'
      WHEN {alias}.stock<={alias}.minimum_stock THEN 'REPONER'
      WHEN {alias}.stock<{alias}.target_stock THEN 'BAJO'
      ELSE 'OK' END"""


def priority_sql(alias="p"):
    return f"""CASE {state_sql(alias)}
      WHEN 'SIN STOCK' THEN 0 WHEN 'REPONER' THEN 1 WHEN 'BAJO' THEN 2
      WHEN 'OK' THEN 3 ELSE 4 END"""
