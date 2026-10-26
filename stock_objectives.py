"""Etapa 1: recomendaciones explicables, de solo lectura y sin órdenes de fabricación.

Los días se cierran a medianoche de Buenos Aires. None significa desconocido,
nunca cero. La evidencia operativa debe provenir de una fuente verificada.
"""
from __future__ import annotations

import json
import math
import statistics
from datetime import datetime, timedelta, timezone
from collections import defaultdict

LOCAL = timezone(timedelta(hours=-3))
WINDOWS = (7, 30, 60, 90)
LEAD_DAYS = 5
TARGET_DAYS = 15  # Incluye fabricación; no sumar otros cinco días.
SERVICE_Z = 1.645  # Propuesta inicial: nivel unilateral del 95%.


def date_time(value):
    try:
        result = datetime.fromisoformat(str(value).replace('Z', '+00:00'))
        return result if result.tzinfo else result.replace(tzinfo=LOCAL)
    except (ValueError, TypeError):
        return None


def recommend(product, daily, available_days, reserved=None, errors=(), observed_only=False):
    """daily y available_days: 90 días completos, del más antiguo al reciente.

    available_days contiene True/False/None. Un día parcialmente agotado debe
    marcarse False. reserved contiene compromisos NO descontados del stock.
    No se incorporan ingresos pendientes al riesgo: falta verificar sus fechas.
    """
    issues = list(errors)
    if len(daily) != 90 or len(available_days) != 90:
        raise ValueError('Se necesitan exactamente 90 días, incluyendo ceros verificados')
    if any(not isinstance(x, (int, float)) or not math.isfinite(x) or x < 0 for x in daily):
        raise ValueError('Demanda diaria inválida')
    if any(x is not None and type(x) is not bool for x in available_days):
        raise ValueError('Disponibilidad inválida')
    if any(x is None for x in available_days):
        issues.append('Falta historial verificado de disponibilidad diaria')
    if reserved is None:
        issues.append('Reservas y compromisos sin verificar; no se asume cero')
    elif not isinstance(reserved, (int, float)) or not math.isfinite(reserved) or reserved < 0:
        issues.append('Reservas inválidas')
    stock = product['stock']
    if stock < 0:
        issues.append('Stock negativo: revisar saldo físico y compromisos')
    result = dict(sales={d: sum(daily[-d:]) for d in WINDOWS},
                  daily_averages={d: sum(daily[-d:])/d for d in WINDOWS},
                  target_recommended=None, reorder_point=None, coverage_days=None,
                  target_difference=None, safety_stock=None, forecast_daily=None,
                  risk='NO EVALUABLE', priority='REVISAR DATOS', issues=issues,
                  trend_change=None, available_daily_averages={},
                  lead_days=LEAD_DAYS, target_days=TARGET_DAYS)
    rates = {}
    for days in WINDOWS:
        valid = [x for x, a in zip(daily[-days:], available_days[-days:]) if a is True]
        if len(valid) < math.ceil(days * .5):
            issues.append(f'Disponibilidad insuficiente para estimar {days} días')
        rates[days] = statistics.mean(valid) if valid else None
    result['available_daily_averages'] = rates
    result['estimate_basis'] = 'Ventas verificadas de 90 días; sin corrección por faltantes ni reservas' if observed_only else 'Disponibilidad verificada'
    result['provisional'] = observed_only
    result['issues'] = list(dict.fromkeys(issues))
    if observed_only and not sum(daily):
        result['issues'].append('Sin ventas verificadas en 90 días: no equivale a demanda cero')
        return result
    if issues and not observed_only:
        return result
    # Ventanas solapadas son señales, no ventas adicionales. Sin multiplicador
    # de tendencia ni piso en el objetivo manual, que duplicarían márgenes.
    forecast = statistics.mean(daily) if observed_only else sum(rates[d]*w for d, w in ((7,.4),(30,.3),(60,.2),(90,.1)))
    valid = daily if observed_only else [x for x, a in zip(daily, available_days) if a is True]
    sigma = statistics.stdev(valid) if len(valid) > 1 else 0
    safety = SERVICE_Z * sigma * math.sqrt(LEAD_DAYS)
    target = math.ceil(forecast*TARGET_DAYS + safety)
    reorder = math.ceil(forecast*LEAD_DAYS + safety)
    available = max(0, stock) if observed_only else max(0, stock-reserved)
    coverage = available/forecast if forecast > 0 else None
    previous = [x for x,a in zip(daily[-60:-30], available_days[-60:-30]) if a is True]
    baseline = statistics.mean(previous) if previous else 0
    change = rates[30]/baseline-1 if baseline > 0 else None
    risk = 'ALTO' if forecast > 0 and available <= forecast*LEAD_DAYS else 'BAJO'
    if risk == 'ALTO': priority = 'CRÍTICO'
    elif forecast > 0 and available <= reorder + forecast: priority = 'ALTA'
    elif available < target: priority = 'NORMAL'
    else: priority = 'SIN NECESIDAD'
    result.update(target_recommended=target, reorder_point=reorder,
                  target_difference=target-product['target_stock'],
                  coverage_days=coverage, safety_stock=math.ceil(safety),
                  forecast_daily=forecast, trend_change=change, risk=risk,
                  priority=('REVISAR DATOS' if stock < 0 else priority))
    if stock < 0:
        result['risk'] = 'SALDO NEGATIVO: REVISAR'
        result['coverage_days'] = None
    return result


def stock_objectives(connection, now=None):
    """Adaptador del esquema actual. No abre conexiones de escritura.

    El sistema actual no registra disponibilidad ni reservas verificadas:
    se calcula una propuesta orientativa solo con ventas vinculadas de 90 días.
    La falta de reservas y disponibilidad se informa, sin inventar correcciones.
    """
    now = (now or datetime.now(LOCAL)).astimezone(LOCAL)
    cutoff = now.replace(hour=0, minute=0, second=0, microsecond=0)
    start = cutoff-timedelta(days=90)
    tables = {r[0] for r in connection.execute("SELECT name FROM sqlite_master WHERE type='table'")}
    products = {r['id']:dict(r) for r in connection.execute('SELECT * FROM products WHERE active=1')}
    errors = []
    associations = {}
    linked = set()
    for r in connection.execute("SELECT * FROM marketplace_listings WHERE marketplace='MERCADOLIBRE' AND active=1"):
        key = (str(r['listing_id']).upper(), str(r['variation_id'] or ''))
        if key in associations: errors.append('Vinculación duplicada de publicación y variante')
        associations[key] = dict(r)
        linked.add(r['product_id'])
    orders = {}
    # La versión más reciente gana, aunque venga de la auditoría y no del inbox.
    for table in ('marketplace_audit_orders', 'marketplace_order_inbox'):
        if table not in tables: continue
        for r in connection.execute(f"SELECT * FROM {table} WHERE marketplace='MERCADOLIBRE'"):
            r = dict(r)
            stamp = date_time(r.get('date_last_updated')) or date_time(r.get('fetched_at'))
            key = str(r['order_id'])
            old = orders.get(key)
            oldstamp = old[0] if old else None
            if old is None or (stamp or datetime.min.replace(tzinfo=LOCAL)) >= (oldstamp or datetime.min.replace(tzinfo=LOCAL)):
                orders[key] = (stamp, r)
    audit = {}
    if 'marketplace_audit_state' in tables:
        audit = dict(connection.execute('SELECT * FROM marketplace_audit_state WHERE id=1').fetchone() or {})
    audit_start, audit_end = date_time(audit.get('period_start')), date_time(audit.get('period_end'))
    if not (audit.get('status') == 'COMPLETE' and audit_start and audit_start <= start
            and audit_end and audit_end >= cutoff and not audit.get('last_error')):
        errors.append('Auditoría histórica incompleta o sin cobertura hasta el último día cerrado')
    sync = None
    if 'marketplace_order_checkpoints' in tables:
        checkpoints = [dict(r) for r in connection.execute("SELECT * FROM marketplace_order_checkpoints WHERE marketplace='MERCADOLIBRE'")]
        stamps = [date_time(r.get('last_success_at')) for r in checkpoints if r.get('checkpoint_type') == 'UPDATED' and r.get('last_run_status') != 'ERROR']
        sync = max((d for d in stamps if d and d <= now), default=None)
    if not sync or now-sync > timedelta(days=1):
        errors.append('Sin sincronización confiable de actualizaciones en las últimas 24 horas')
    daily = defaultdict(lambda:[0]*90)
    unlinked = malformed = 0
    for _, stored in orders.values():
        created = date_time(stored.get('date_created'))
        if not created:
            malformed += 1
            continue
        if not start <= created < cutoff: continue
        try:
            order = json.loads(stored['payload_json'])
            if not isinstance(order, dict): raise ValueError()
            if str(order.get('status','')).lower() not in {'paid','confirmed'}: continue
            lines = order.get('order_items')
            if not isinstance(lines,list) or not lines: raise ValueError()
            for line in lines:
                item = line.get('item') or {}
                association = associations.get((str(item.get('id','')).upper(), str(item.get('variation_id') or '')))
                if not association:
                    unlinked += 1
                    continue
                quantity = line.get('quantity')
                factor = association['units_consumed']
                if type(quantity) is not int or quantity <= 0 or type(factor) is not int or factor <= 0:
                    raise ValueError()
                index = (created.astimezone(LOCAL).date()-start.date()).days
                daily[association['product_id']][index] += quantity*factor
        except (ValueError, TypeError, AttributeError, KeyError):
            malformed += 1
    if unlinked: errors.append(f'{unlinked} líneas sin asociación excluidas del cálculo; no se asignan a ningún producto')
    if malformed: errors.append(f'{malformed} órdenes con fechas o contenido inválidos')
    # El esquema separa revisiones de devoluciones, pero no contiene un libro
    # completo de devoluciones netas; no descontar ajustes de stock como demanda.
    errors.append('Cancelaciones excluidas; devoluciones netas sin conciliación verificada')
    results = {}
    for pid, product in products.items():
        local_errors = errors + ([] if pid in linked else ['Producto sin vinculación exacta a Mercado Libre'])
        result = recommend(product, daily[pid], [None]*90, errors=local_errors, observed_only=True)
        result.update(calculated_at=now.isoformat(), sales_through=(cutoff-timedelta(days=1)).date().isoformat(),
                      last_sync=sync.isoformat() if sync else None,
                      audit_update=audit.get('completed_at') or audit.get('updated_at'))
        results[pid] = result
    return results
