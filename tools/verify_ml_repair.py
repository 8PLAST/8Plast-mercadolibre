import json
import sqlite3
from pathlib import Path
ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT/'outputs'/'reparacion_ml_20260906'
before = json.loads((OUT/'before.json').read_text(encoding='utf-8'))
api = json.loads((OUT/'api_orders.json').read_text(encoding='utf-8'))
con = sqlite3.connect('file:'+str(ROOT/'8plast_stock.db')+'?mode=ro',uri=True)
con.row_factory = sqlite3.Row
last = max(m['id'] for m in before['stock_movements'])
movements = [dict(r) for r in con.execute('SELECT * FROM stock_movements WHERE id>?',(last,))]
old = [dict(r) for r in con.execute('SELECT * FROM stock_movements WHERE id<=?',(last,))]
assert old == before['stock_movements']
associations = [dict(r) for r in con.execute('SELECT * FROM marketplace_listings')]
assert [{k:v for k,v in r.items() if k!='last_synced_at'} for r in associations] == [{k:v for k,v in r.items() if k!='last_synced_at'} for r in before['marketplace_listings']]
details = []
for m in movements:
    assert m['source']=='MERCADOLIBRE' and m['movement_type']=='Venta'
    _,_,oid,lid,vid,index = m['external_key'].split(':')
    order = json.loads(con.execute('SELECT payload_json FROM marketplace_order_inbox WHERE order_id=?',(oid,)).fetchone()[0])
    line = order['order_items'][int(index)]
    assoc = next(a for a in associations if a['listing_id']==lid and a['variation_id']==vid)
    expected = int(line['quantity'])*assoc['units_consumed']
    assert m['quantity_delta']==-expected and m['product_id']==assoc['product_id']
    details.append({'order_id':oid,'date_created':order['date_created'],'item_id':lid,'variation_id':vid,'product_id':m['product_id'],'quantity':line['quantity'],'units_per_sale':assoc['units_consumed'],'physical_units':expected,'movement_id':m['id']})
for p in before['products']:
    current = con.execute('SELECT stock FROM products WHERE id=?',(p['id'],)).fetchone()[0]
    assert current == p['stock']+sum(m['quantity_delta'] for m in movements if m['product_id']==p['id'])
pending = []
for r in con.execute("SELECT * FROM marketplace_order_inbox WHERE processing_status IN ('ERROR','UNASSOCIATED')"):
    o=json.loads(r['payload_json'])
    pending.append({'order_id':r['order_id'],'status':r['processing_status'],'error':r['last_error'],'items':[{'item_id':i['item']['id'],'variation_id':i['item'].get('variation_id'),'quantity':i['quantity']} for i in o['order_items']]})
duplicates = con.execute('SELECT external_key FROM stock_movements WHERE external_key IS NOT NULL GROUP BY external_key HAVING COUNT(*)>1').fetchall()
assert not duplicates
report={'movements':len(movements),'recovered_orders':len({d['order_id'] for d in details}),'physical_units':sum(d['physical_units'] for d in details),'latest_sale':details[-1],
        'integrity':con.execute('PRAGMA integrity_check').fetchone()[0], 'foreign_key_errors':len(con.execute('PRAGMA foreign_key_check').fetchall()),
        'associations_preserved':True,'existing_movements_preserved':True,'stocks_match_movements':True,'pack_equivalences_verified':True,'duplicates_created':len(duplicates),
        'pending':pending,'api_existing_sales':sum(any(i['movement_before'] for i in a['items']) for a in api),
        'api_missing_inbox_before':sum(a['inbox_before'] is None for a in api)}
(OUT/'verified_movements.json').write_text(json.dumps(details,ensure_ascii=False,indent=2),encoding='utf-8')
(OUT/'verification.json').write_text(json.dumps(report,ensure_ascii=False,indent=2),encoding='utf-8')
print(json.dumps(report,ensure_ascii=True))
