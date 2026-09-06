"""Carga inicial transaccional, nunca reemplaza un inventario existente."""
import sqlite3
from datetime import datetime
from runtime_config import file_lock

TABLES=('products','bag_presentations','marketplace_listings','stock_movements','marketplace_events',
 'marketplace_listing_exclusions','marketplace_processing_config','marketplace_order_reviews',
 'marketplace_order_inbox','marketplace_order_checkpoints','marketplace_audit_orders','marketplace_audit_state')

def import_snapshot(db,path):
    source=sqlite3.connect(path.resolve().as_uri()+'?mode=ro',uri=True); source.row_factory=sqlite3.Row
    try:
        if source.execute('PRAGMA integrity_check').fetchone()[0]!='ok': raise ValueError('Backup dañado.')
        for table in TABLES: source.execute('SELECT * FROM '+table+' LIMIT 0')
        with file_lock(str(db.path)+'.import.lock',timeout=30):
            db.backup(str(db.path)+'.pre_import_'+datetime.now().strftime('%Y%m%d_%H%M%S_%f')+'.db')
            with db.transaction() as con:
                if con.execute('SELECT COUNT(*) FROM products').fetchone()[0] or con.execute('SELECT COUNT(*) FROM stock_movements').fetchone()[0]:
                    raise ValueError('Esta base ya tiene inventario. No se reemplazó ningún dato.')
                for table in TABLES:
                    rows=source.execute('SELECT * FROM '+table).fetchall()
                    columns=[r[1] for r in source.execute('PRAGMA table_info('+table+')')]
                    known={r[1] for r in con.execute('PRAGMA table_info('+table+')')}
                    if not set(columns)<=known: raise ValueError('La estructura del backup no es compatible: '+table)
                    verb='INSERT OR REPLACE' if table in ('bag_presentations','marketplace_processing_config','marketplace_audit_state') else 'INSERT'
                    sql=verb+' INTO '+table+' ('+','.join('"'+c+'"' for c in columns)+') VALUES ('+','.join('?' for _ in columns)+')'
                    con.executemany(sql,[tuple(r) for r in rows])
                if con.execute('PRAGMA foreign_key_check').fetchone(): raise ValueError('El backup tiene relaciones inválidas.')
            # Notificaciones locales pueden volver a descargarse por checkpoints; las cloud permanecen intactas.
    finally: source.close()
