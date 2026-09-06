"""Prueba aislada de arranque, reinicio, standby y SQLite. No utiliza credenciales reales."""
import json
import os
import signal
import socket
import subprocess
import sys
import tempfile
import time
import urllib.request
from pathlib import Path
ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT))
from cryptography.fernet import Fernet
from db import Database

def wait_for(check):
    deadline=time.time()+40
    while time.time()<deadline:
        try:
            value=check()
            if value: return value
        except (OSError,ValueError,KeyError): pass
        time.sleep(.3)
    raise AssertionError('Timeout esperando al servicio de prueba')

def main():
    with tempfile.TemporaryDirectory(prefix='8plast_cloud_smoke_') as folder:
        directory=Path(folder); path=directory/'stock.db'; log=directory/'service.log'
        with socket.socket() as sock:
            sock.bind(('127.0.0.1',0)); port=sock.getsockname()[1]
        env={**os.environ,'APP_ENV':'cloud','DATABASE_PATH':str(path),'WEBHOOK_DATABASE_PATH':str(path),
            'PORTAL_DATABASE_PATH':str(path),'PORT':str(port),'MELI_SYNC_ENABLED':'0',
            'PORTAL_PASSWORD':'smoke-test-password-123','PORTAL_SECRET_KEY':'test-secret-key-'*4,'MELI_TOKEN_KEY':Fernet.generate_key().decode()}
        def health(): return json.load(urllib.request.urlopen(f'http://127.0.0.1:{port}/health',timeout=2))
        def status(): return json.loads((directory/'worker_status.json').read_text())
        def events():
            rows=[]
            for line in log.read_text(errors='replace').splitlines():
                try: rows.append(json.loads(line))
                except ValueError: pass
            return rows
        with log.open('w') as stream:
            proc=subprocess.Popen([sys.executable,str(ROOT/'cloud_start.py')],cwd=ROOT,env=env,stdout=stream,stderr=stream,
                creationflags=subprocess.CREATE_NO_WINDOW if os.name=='nt' else 0)
            try:
                first=wait_for(health); assert first['worker']=='standby' and not first['sync_enabled']
                initial_pid=status()['pid']
                db=Database(path); pid=db.add_product({'kind':'BAG','sku':'SMOKE','name':'Persistencia','width_cm':10,'length_cm':20,'microns':35},5)
                os.kill(initial_pid,signal.SIGTERM)
                new=wait_for(lambda: status() if status()['pid']!=initial_pid else None)
                wait_for(health)
                web=next(e['pid'] for e in reversed(events()) if e.get('component')=='web')
                os.kill(web,signal.SIGTERM)
                wait_for(lambda: any(e.get('component')=='web' and e['pid']!=web for e in events()))
                wait_for(health)
                assert Database(path).product(pid)['stock']==5
                assert not any(e.get('event')=='poll_completed' for e in events())
                print(json.dumps({'HTTP':'OK','worker_restart':'OK','web_restart':'OK','database_persistence':'OK','standby_no_sync':'OK'}))
            except Exception:
                print(log.read_text(errors='replace')[-4000:])
                raise
            finally:
                proc.terminate()
                try: proc.wait(timeout=10)
                except subprocess.TimeoutExpired: proc.kill(); proc.wait()
                for event in events():
                    if event.get('event')=='supervisor_started':
                        try: os.kill(event['pid'],signal.SIGTERM)
                        except OSError: pass
                time.sleep(1)

if __name__=='__main__': main()
