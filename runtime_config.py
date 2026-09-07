"""Rutas compartidas y bloqueo entre procesos para Windows y Linux."""
import os
import time
from contextlib import contextmanager
from pathlib import Path

ROOT = Path(__file__).resolve().parent

def cloud_mode():
    return os.getenv('APP_ENV') == 'cloud' or bool(os.getenv('RAILWAY_PROJECT_ID'))

def database_path():
    paths = [os.getenv(k) for k in ('DATABASE_PATH', 'WEBHOOK_DATABASE_PATH', 'PORTAL_DATABASE_PATH') if os.getenv(k)]
    if cloud_mode() and len({str(Path(p).resolve()) for p in paths}) > 1:
        raise RuntimeError('Las rutas de base de datos deben coincidir.')
    return Path(paths[0]) if paths else (Path('/data/8plast_stock.db') if cloud_mode() else ROOT/'8plast_stock.db')

def data_dir():
    return database_path().parent

def sync_owner():
    return not cloud_mode() or os.getenv('MELI_SYNC_ENABLED', '0') == '1'

def validate_railway_storage():
    """Falla antes de crear datos si Railway no tiene el volumen esperado."""
    if not os.getenv('RAILWAY_PROJECT_ID'):
        return
    mount = os.getenv('RAILWAY_VOLUME_MOUNT_PATH', '')
    if mount != '/data':
        raise RuntimeError('Montá un Railway Volume en /data antes de iniciar.')
    path = database_path().resolve()
    if not path.is_relative_to(Path('/data').resolve()):
        raise RuntimeError('La base de Railway debe estar dentro de /data.')

@contextmanager
def file_lock(path, timeout=0):
    path = Path(path); path.parent.mkdir(parents=True, exist_ok=True)
    with path.open('a+b') as handle:
        if os.fstat(handle.fileno()).st_size == 0: handle.write(b'0'); handle.flush()
        deadline = time.monotonic() + timeout
        while True:
            try:
                handle.seek(0)
                if os.name == 'nt':
                    import msvcrt
                    msvcrt.locking(handle.fileno(), msvcrt.LK_NBLCK, 1)
                else:
                    import fcntl
                    fcntl.flock(handle, fcntl.LOCK_EX | fcntl.LOCK_NB)
                break
            except OSError:
                if time.monotonic() >= deadline: raise TimeoutError('Otro proceso mantiene el bloqueo.')
                time.sleep(.1)
        try: yield
        finally:
            handle.seek(0)
            if os.name == 'nt':
                import msvcrt
                msvcrt.locking(handle.fileno(), msvcrt.LK_UNLCK, 1)
            else:
                import fcntl
                fcntl.flock(handle, fcntl.LOCK_UN)
