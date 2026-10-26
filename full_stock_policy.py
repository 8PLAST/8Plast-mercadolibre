"""Excepción Full prospectiva, sin inventarios ni ajustes históricos."""
import json
from datetime import datetime, timezone
from pathlib import Path
from runtime_config import file_lock


def policy_path(database_path):
    return Path(database_path).parent/'full_stock_policy.json'


def activation(database_path):
    path=policy_path(database_path)
    if not path.exists(): return None
    data=json.loads(path.read_text(encoding='utf-8'))
    value=datetime.fromisoformat(data['effective_from'])
    if value.tzinfo is None: raise ValueError('La vigencia Full requiere zona horaria')
    return value.astimezone(timezone.utc)


def activate_once(database_path):
    """La primera ejecución actualizada fija vigencia; reinicios la conservan."""
    path=policy_path(database_path)
    with file_lock(path.with_suffix('.lock'),timeout=10):
        if not path.exists():
            value=datetime.now(timezone.utc).isoformat(timespec='microseconds')
            temporary=path.with_suffix('.tmp')
            temporary.write_text(json.dumps({'effective_from':value,'rule':'FULL_NO_STOCK_FUTURE_ORDERS'},indent=2),encoding='utf-8')
            temporary.replace(path)
        return activation(database_path)
