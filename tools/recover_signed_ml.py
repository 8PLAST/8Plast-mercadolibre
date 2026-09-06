"""Recuperación autorizada de ventas reales con saldo negativo; sin ajustes manuales."""
import sys
from pathlib import Path
ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT))
import json, sqlite3, msvcrt
from datetime import datetime, timezone
from db import Database
from ml_integration import MercadoLibreClient

def main():
    with (ROOT/'mercadolibre_worker.lock').open('r+b') as lock:
        msvcrt.locking(lock.fileno(),msvcrt.LK_NBLCK,1)
        con=sqlite3.connect('file:'+str(ROOT/'8plast_stock.db')+'?mode=ro',uri=True)
        con.row_factory=sqlite3.Row
        previous=[dict(r) for r in con.execute('SELECT * FROM stock_movements ORDER BY id')]
        products=[dict(r) for r in con.execute('SELECT * FROM products ORDER BY id')]
        listings=[dict(r) for r in con.execute('SELECT * FROM marketplace_listings ORDER BY id')]
        db=Database()
        assert [dict(r) for r in con.execute('SELECT * FROM stock_movements ORDER BY id')]==previous
        assert [dict(r) for r in con.execute('SELECT * FROM products ORDER BY id')]==products
        assert [dict(r) for r in con.execute('SELECT * FROM marketplace_listings ORDER BY id')]==listings
        client=MercadoLibreClient(db); cutoff=datetime.now(timezone.utc)
        first=client.sync_orders_recovery(cutoff)
        movements=[dict(r) for r in con.execute('SELECT * FROM stock_movements WHERE id>? ORDER BY id',(max(r['id'] for r in previous),))]
        second=client.sync_orders_recovery(cutoff)
        assert [dict(r) for r in con.execute('SELECT * FROM stock_movements ORDER BY id')]==previous+movements
        details=[]
        for m in movements:
            assert m['source']=='MERCADOLIBRE' and m['movement_type']=='Venta'
            _,_,oid,lid,vid,index=m['external_key'].split(':')
            order=json.loads(con.execute('SELECT payload_json FROM marketplace_order_inbox WHERE order_id=?',(oid,)).fetchone()[0])
            quantity=int(order['order_items'][int(index)]['quantity'])
            assoc=next(a for a in listings if a['listing_id']==lid and a['variation_id']==vid)
            assert -m['quantity_delta']==quantity*assoc['units_consumed']
            details.append({**m,'order_id':oid,'order_date':order['date_created'],'units_per_sale':assoc['units_consumed'],'quantity':quantity})
        for p in products:
            assert con.execute('SELECT stock FROM products WHERE id=?',(p['id'],)).fetchone()[0]==p['stock']+sum(m['quantity_delta'] for m in movements if m['product_id']==p['id'])
        current=[dict(r) for r in con.execute('SELECT * FROM marketplace_listings ORDER BY id')]
        strip=lambda a:[{k:v for k,v in r.items() if k!='last_synced_at'} for r in a]
        assert strip(current)==strip(listings)
        report={'cutoff':cutoff.isoformat(),'first':first,'second':second,'movements':details,
          'negative_products':[dict(r) for r in con.execute('SELECT id,name,sku,stock FROM products WHERE stock<0')],
          'pending':[dict(r) for r in con.execute("SELECT order_id,processing_status,last_error FROM marketplace_order_inbox WHERE processing_status IN ('ERROR','PENDING','UNASSOCIATED')")],
          'integrity':con.execute('PRAGMA integrity_check').fetchone()[0],
          'foreign_keys':[tuple(r) for r in con.execute('PRAGMA foreign_key_check')],
          'previous_data_preserved':True,'pack_equivalences_verified':True,'second_pass_new_movements':0}
        target=ROOT/'outputs'/('saldos_negativos_ml_'+datetime.now().strftime('%Y%m%d_%H%M%S')+'.json')
        target.write_text(json.dumps(report,ensure_ascii=False,indent=2),encoding='utf-8')
        print(json.dumps({'report':str(target),'new_movements':len(movements),'units':-sum(m['quantity_delta'] for m in movements),'second_pass_new_movements':0,'negative_products':report['negative_products'],'pending':report['pending'],'integrity':report['integrity']},ensure_ascii=True))

if __name__=='__main__': main()
