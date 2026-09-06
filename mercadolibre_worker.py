from __future__ import annotations

import json
import os
import re
import sys
import time
from datetime import datetime
from pathlib import Path

from db import Database
from ml_integration import MercadoLibreClient
from runtime_config import cloud_mode, data_dir, file_lock, sync_owner


ROOT=Path(__file__).resolve().parent
LOCK=data_dir()/"mercadolibre_worker.lock"
LOG=ROOT/"mercadolibre_worker.log"

def log(event,**data):
    record={"time":datetime.now().astimezone().isoformat(timespec="seconds"),"event":event,**data}
    if cloud_mode():
        print(json.dumps(record,ensure_ascii=False),flush=True)
        return
    with LOG.open("a",encoding="utf-8") as f:
        f.write(json.dumps(record,ensure_ascii=False)+"\n")

def heartbeat(state, **extra):
    directory=data_dir(); directory.mkdir(parents=True,exist_ok=True)
    temporary=directory/'worker_status.tmp'
    temporary.write_text(json.dumps({'time':time.time(),'state':state,'pid':os.getpid(),**extra}),encoding='utf-8')
    os.replace(temporary,directory/'worker_status.json')

def process_notifications(db, client):
    with db.session() as con:
        rows=con.execute("SELECT id,topic,resource,user_id FROM mercadolibre_webhook_events WHERE processing_status IN ('RECEIVED','ERROR') ORDER BY id LIMIT 50").fetchall()
    for row in rows:
        status='IGNORED'
        try:
            if row['topic'] in ('orders','orders_v2') and re.fullmatch(r'/orders/\d+',row['resource'] or '') and str(row['user_id'])==str(client._seller_id()):
                order=client.get_order(row['resource'].rsplit('/',1)[-1])
                if str((order.get('seller') or {}).get('id')) != str(client._seller_id()):
                    raise ValueError('El vendedor de la orden no coincide.')
                db.store_marketplace_orders([order]); status='STORED'
        except Exception:
            status='ERROR'
        with db.session() as con:
            con.execute('UPDATE mercadolibre_webhook_events SET processing_status=? WHERE id=?',(status,row['id']))

def run():
    with file_lock(LOCK):
        db=Database(); client=MercadoLibreClient(db); next_poll=0; next_queue=0
        log('worker_started')
        while True:
            config=db.marketplace_processing_config()
            if not sync_owner() or not config or not config['enabled']:
                heartbeat('standby'); time.sleep(5); continue
            heartbeat('running')
            try:
                if time.monotonic()>=next_poll:
                    audit=db.marketplace_audit_state()
                    if audit and audit['status']=='REQUESTED': client.audit_historical_orders()
                    summary=client.sync_orders_recovery()
                    log('poll_partial' if summary['processing']['errors'] else 'poll_completed',summary=summary)
                    next_poll=time.monotonic()+int(config['interval_seconds'] or 300)
                    heartbeat('running',last_success=time.time())
                if time.monotonic()>=next_queue:
                    process_notifications(db,client)
                    result=db.process_marketplace_inbox()
                    if result['pending']: log('notifications_processed',summary=result)
                    next_queue=time.monotonic()+10
            except Exception as exc:
                log('poll_error',error=type(exc).__name__)
                heartbeat('error',error=type(exc).__name__)
                next_poll=time.monotonic()+60; next_queue=next_poll
            time.sleep(2)

if __name__=='__main__':
    try: run()
    except TimeoutError: raise SystemExit(0)
