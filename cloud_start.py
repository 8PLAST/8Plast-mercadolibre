"""Un único arranque supervisa HTTP y el trabajador independiente."""
import json
import os
import signal
import subprocess
import sys
import time
from runtime_config import ROOT, data_dir, validate_railway_storage

def main():
    os.environ['APP_ENV']='cloud'
    from cloud_web import listen_port
    port = listen_port()
    print(json.dumps({'event':'cloud_boot','host':'0.0.0.0','port':port}),flush=True)
    validate_railway_storage()
    for key in ('PORTAL_PASSWORD','PORTAL_SECRET_KEY','MELI_TOKEN_KEY'):
        if not os.getenv(key): raise RuntimeError('Falta variable requerida: '+key)
    if len(os.environ['PORTAL_PASSWORD'])<16 or len(os.environ['PORTAL_SECRET_KEY'])<32:
        raise RuntimeError('Usá contraseña de al menos 16 caracteres y clave de sesión de al menos 32.')
    from cloud_tokens import cipher
    cipher()
    from db import Database
    Database()
    commands={'web':[sys.executable,str(ROOT/'cloud_web.py')],
              'worker':[sys.executable,str(ROOT/'mercadolibre_worker.py')]}
    children={}; started={}; stopping=False
    def stop(*args):
        nonlocal stopping
        stopping=True
    signal.signal(signal.SIGTERM,stop); signal.signal(signal.SIGINT,stop)
    try:
        while not stopping:
            for name,command in commands.items():
                child=children.get(name)
                if child is None or child.poll() is not None:
                    children[name]=subprocess.Popen(command,cwd=ROOT); started[name]=time.time()
                    print(json.dumps({'event':'supervisor_started','component':name,'pid':children[name].pid}),flush=True)
            try: last=json.loads((data_dir()/'worker_status.json').read_text())['time']
            except (OSError,ValueError,KeyError): last=started['worker']
            if time.time()-max(last,started['worker'])>900:
                children['worker'].terminate()
                try: children['worker'].wait(timeout=10)
                except subprocess.TimeoutExpired: children['worker'].kill()
            time.sleep(5)
    finally:
        for child in children.values():
            if child.poll() is None: child.terminate()
        for child in children.values():
            try: child.wait(timeout=10)
            except subprocess.TimeoutExpired: child.kill()

if __name__=='__main__': main()
