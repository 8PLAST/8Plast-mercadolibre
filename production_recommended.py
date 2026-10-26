"""Planificación de solo lectura. Una simulación nunca registra producción."""
import math
from product_planning import product_family, FAMILY_LABELS

PRIORITIES = ('CRÍTICO', 'ALTA', 'NORMAL', 'SIN NECESIDAD', 'REVISAR DATOS')


def production_row(product, objective, reserved=None, receipts=None):
    """reserved: compromisos adicionales aún NO descontados del stock.

    receipts: ingresos aún NO registrados, {id, quantity, days}; días desde hoy.
    Entradas con el mismo id se cuentan una vez; conflictos se rechazan.
    None indica fuente desconocida; [] confirma ausencia de pendientes.
    """
    warnings = list(objective.get('issues', []))
    provisional = reserved is None or receipts is None
    if reserved is None: warnings.append('Reservas adicionales no registradas: escenario sin descuento adicional')
    if receipts is None: warnings.append('Producción pendiente no registrada: escenario sin ingresos pendientes')
    reserved_value = 0 if reserved is None else reserved
    if type(reserved_value) is not int or reserved_value < 0:
        raise ValueError('Reservas: ingresá unidades enteras no negativas')
    unique = {}
    for receipt in receipts or []:
        key = str(receipt.get('id', '')).strip()
        qty, days = receipt.get('quantity'), receipt.get('days')
        if not key or type(qty) is not int or qty < 0 or type(days) is not int or days < 0:
            raise ValueError('Ingreso pendiente inválido')
        value = (qty, days)
        if key in unique and unique[key] != value:
            raise ValueError('La misma producción pendiente tiene datos contradictorios')
        unique[key] = value
    lead = objective['lead_days']
    due = sum(q for q, day in unique.values() if day <= lead)
    later = sum(q for q, day in unique.values() if day > lead)
    family = product_family(product)
    forecast = objective.get('forecast_daily')
    target = objective.get('target_recommended')
    result = dict(product, objective=objective, family=family, family_label=FAMILY_LABELS[family],
                  reserved=reserved, pending_known=receipts is not None,
                  pending_due=due, pending_later=later, projected_stock=None,
                  available_stock=product['stock']-reserved_value, monthly_average=objective['sales'][90]/3,
                  suggested=None, packs=None, days_to_stockout=None,
                  priority='REVISAR DATOS', replenishment_state='Revisar datos',
                  warnings=warnings, provisional=provisional or objective.get('provisional',False))
    if product['stock'] < 0 or target is None or forecast is None:
        return result
    available = product['stock']-reserved_value
    # No truncar la proyección negativa: el faltante al recibir es demanda
    # pendiente que debe contemplarse. Pendientes se restan exactamente UNA vez.
    projected = available-forecast*lead
    suggested = max(0, math.ceil(target-projected-due))
    balance = available
    previous_day = 0
    exhaustion = None
    for day in sorted({d for _,d in unique.values() if d <= lead} | {lead}):
        if forecast > 0 and balance <= forecast*(day-previous_day):
            exhaustion = previous_day+max(0,balance)/forecast
            break
        balance -= forecast*(day-previous_day)
        balance += sum(q for q,d in unique.values() if d == day)
        previous_day = day
    if exhaustion is None and forecast > 0:
        exhaustion = lead+max(0,balance)/forecast
    if available < 0 or (exhaustion is not None and exhaustion <= lead):
        priority = 'CRÍTICO'
    elif suggested == 0: priority = 'SIN NECESIDAD'
    elif available <= objective['reorder_point']+forecast: priority = 'ALTA'
    else: priority = 'NORMAL'
    if due and suggested == 0:
        state = 'Ingreso pendiente cubre el objetivo'
    elif due: state = 'Ingreso pendiente parcial; fabricar saldo'
    elif later: state = 'Pendiente posterior a 5 días; no cubre esta reposición'
    elif suggested: state = 'Reposición recomendada'
    else: state = 'Stock suficiente'
    if receipts is None: state += ' · pendientes sin verificar'
    if later: warnings.append('Ingresos posteriores al día 5 excluidos de esta reposición; revisar antes de fabricar')
    pack_size = product.get('storage_pack') if product['kind']=='BAG' else None
    result.update(available_stock=available, projected_stock=projected,
                  suggested=suggested, packs=math.ceil(suggested/pack_size) if pack_size else None,
                  days_to_stockout=exhaustion, priority=priority, replenishment_state=state)
    return result


def build_recommended_plan(products, objectives, contexts=None):
    contexts = contexts or {}
    rows = [production_row(p, objectives[p['id']], **contexts.get(p['id'],{})) for p in products]
    rows.sort(key=lambda r:(PRIORITIES.index(r['priority']),
                           r['days_to_stockout'] if r['days_to_stockout'] is not None else math.inf,
                           -r['monthly_average'],r['name'].casefold(),r['sku']))
    return rows


def spreadsheet_safe(value):
    """Evitar ejecución de fórmulas al abrir texto de catálogo en Excel."""
    text = str(value) if value is not None else ''
    if text.lstrip().startswith(('=', '+', '-', '@')) or text.startswith(('\t','\r','\n')):
        return "'"+text
    return text
