"""Auditoría sin datos de compradores; recuperación mediante el pipeline normal."""
import sys
from pathlib import Path
ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
import json
import sqlite3
from datetime import datetime, timedelta, timezone
from db import Database
from ml_integration import MercadoLibreClient

OUT = ROOT / 'outputs' / 'reparacion_ml_20260906'
OUT.mkdir(parents=True, exist_ok=True)

def save(name, value):
    (OUT / (name + '.json')).write_text(json.dumps(value, ensure_ascii=False, indent=2), encoding='utf-8')

def snapshot(con):
    return {t: [dict(r) for r in con.execute('SELECT * FROM '+t)] for t in
            ('products', 'marketplace_listings', 'stock_movements', 'marketplace_order_checkpoints')}

def main():
    con = sqlite3.connect('file:'+str(ROOT/'8plast_stock.db')+'?mode=ro', uri=True)
    con.row_factory = sqlite3.Row
    if sys.argv[1] == 'diagnose':
        save('before', snapshot(con))
        records = [json.loads(line) for line in (ROOT/'mercadolibre_worker.log').read_text(encoding='utf-8').splitlines() if line.strip()]
        records = [r for r in records if r['time'] >= '2026-09-01']
        first = next((i for i,r in enumerate(records) if r.get('event')=='poll_error' or r.get('summary',{}).get('processing',{}).get('errors')), None)
        report = {'first_error': records[first] if first is not None else None,
                  'previous_cycle': records[first-1] if first else None,
                  'last_record': records[-1],
                  'latest_persisted': dict(con.execute('SELECT order_id,date_created,date_last_updated,processing_status FROM marketplace_order_inbox ORDER BY date_created DESC LIMIT 1').fetchone()),
                  'latest_sale': dict(con.execute("SELECT m.*,p.name FROM stock_movements m JOIN products p ON p.id=m.product_id WHERE source='MERCADOLIBRE' ORDER BY m.id DESC LIMIT 1").fetchone())}
        save('diagnosis', report)
        print(json.dumps(report, ensure_ascii=True))
        return
    client = MercadoLibreClient(None)
    start = datetime.fromisoformat('2026-09-02T00:00:00-03:00')
    cutoff = datetime.now(timezone.utc)
    orders = {}
    pages = []
    for field in ('date_created', 'date_last_updated'):
        cursor = start
        while cursor < cutoff:
            end = min(cursor+timedelta(days=1), cutoff)
            offset = 0
            while True:
                response = client._search_page(field, cursor, end, offset)
                batch = response.get('results', [])
                total = int(response['paging']['total'])
                pages.append({'field':field,'from':cursor.isoformat(),'to':end.isoformat(),'offset':offset,'total':total,'count':len(batch)})
                if not batch and offset < total: raise RuntimeError('Incomplete pagination')
                for order in batch: orders[str(order['id'])] = order
                offset += len(batch)
                if offset >= total: break
            cursor = end
    audit = []
    for oid,o in orders.items():
        row = con.execute('SELECT processing_status FROM marketplace_order_inbox WHERE order_id=?',(oid,)).fetchone()
        lines = []
        for i,line in enumerate(o.get('order_items',[])):
            item = line['item']; lid = item['id']; vid = str(item.get('variation_id') or '')
            key = f'ML:SALE:{oid}:{lid}:{vid}:{i}'
            movement = con.execute('SELECT id,quantity_delta FROM stock_movements WHERE external_key=?',(key,)).fetchone()
            lines.append({'item_id':lid,'variation_id':vid,'quantity':line['quantity'],'movement_before':dict(movement) if movement else None})
        audit.append({'order_id':oid,'date_created':o.get('date_created'),'date_last_updated':o.get('date_last_updated'),'status':o['status'],'inbox_before':row[0] if row else None,'items':lines})
    save('api_orders', audit); save('api_pages',pages)
    print(json.dumps({'unique_orders_created_or_updated':len(audit),'created_in_period':sum(start<=datetime.fromisoformat(o['date_created'])<=cutoff for o in audit),'pages':len(pages)}),flush=True)
    if sys.argv[1] != 'recover': return
    db = Database(); client.db = db
    db.store_marketplace_orders(list(orders.values()))
    first = client.sync_orders_recovery(cutoff)
    after = snapshot(con)
    second = client.sync_orders_recovery(cutoff)
    final = snapshot(con)
    save('recovery_first',first); save('recovery_second',second); save('after',final)
    assert after['stock_movements'] == final['stock_movements'], 'Second pass generated movements'
    assert [p['stock'] for p in after['products']] == [p['stock'] for p in final['products']]
    print(json.dumps({'first':first,'second':second},ensure_ascii=True),flush=True)

if __name__ == '__main__': main()
