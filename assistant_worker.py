"""Dedicated Railway supervisor child; never depends on a PC or sales settings."""
import json
import os
import time
from assistant_center import ReviewStore, integration_token_provider
from assistant_automation import initialize, tick, INTERVAL
from runtime_config import cloud_mode, data_dir, file_lock, sync_owner
from ml_integration import MercadoLibreClient


def run():
    if not cloud_mode(): raise RuntimeError('El monitor requiere Railway.')
    with file_lock(data_dir()/'assistant_worker.lock'):
        store = ReviewStore(data_dir()/'assistant_reviews.db')
        initialize(store)
        with store.connect() as con:
            # A killed worker may have sent Telegram but missed the acknowledgement.
            con.execute("UPDATE assistant_alerts SET state='UNKNOWN',error='WORKER_INTERRUPTED' WHERE state='CLAIMED'")
        provider = integration_token_provider(MercadoLibreClient(None))
        next_query = 0
        while True:
            try:
                if sync_owner():
                    due = time.monotonic() >= next_query
                    tick(store,provider,query=due)
                    if due: next_query = time.monotonic()+INTERVAL
                heartbeat = {'time':time.time(),'pid':os.getpid(),'state':'running' if sync_owner() else 'standby'}
                temporary = data_dir()/'assistant_worker_status.tmp'
                temporary.write_text(json.dumps(heartbeat),encoding='utf-8')
                os.replace(temporary,data_dir()/'assistant_worker_status.json')
            except Exception:
                # Avoid dumping requests, credentials, private question text or traceback.
                print(json.dumps({'event':'assistant_worker_error','code':'INTERNAL'}),flush=True)
            time.sleep(5)


if __name__=='__main__': run()
