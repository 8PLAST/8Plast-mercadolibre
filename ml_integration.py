from __future__ import annotations

import base64
import ctypes
import json
import os
import tempfile
import secrets
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime, timedelta, timezone
from ctypes import wintypes
from pathlib import Path
from contextlib import contextmanager
from runtime_config import cloud_mode, data_dir, file_lock, sync_owner


ROOT = Path(__file__).resolve().parent
PUBLIC_CONFIG = ROOT / "mercadolibre_config.json"
SECURE_CONFIG = ROOT / "mercadolibre_secure.dat"
AUTH_URL = "https://auth.mercadolibre.com.ar/authorization"
TOKEN_URL = "https://api.mercadolibre.com/oauth/token"
API_URL = "https://api.mercadolibre.com"
TOKEN_THREAD_LOCK = threading.RLock()


@contextmanager
def token_lock():
    """Serializa renovaciones entre procesos del mismo usuario de Windows."""
    with TOKEN_THREAD_LOCK:
        with file_lock(data_dir()/'mercadolibre_token.lock', timeout=60):
            yield


class MercadoLibreError(RuntimeError):
    pass


class DATA_BLOB(ctypes.Structure):
    _fields_ = [("cbData", wintypes.DWORD), ("pbData", ctypes.POINTER(wintypes.BYTE))]


if hasattr(ctypes, "windll"):
    ctypes.windll.crypt32.CryptProtectData.argtypes = [ctypes.POINTER(DATA_BLOB), wintypes.LPCWSTR,
        ctypes.POINTER(DATA_BLOB), ctypes.c_void_p, ctypes.c_void_p, wintypes.DWORD, ctypes.POINTER(DATA_BLOB)]
    ctypes.windll.crypt32.CryptProtectData.restype = wintypes.BOOL
    ctypes.windll.crypt32.CryptUnprotectData.argtypes = [ctypes.POINTER(DATA_BLOB), ctypes.c_void_p,
        ctypes.POINTER(DATA_BLOB), ctypes.c_void_p, ctypes.c_void_p, wintypes.DWORD, ctypes.POINTER(DATA_BLOB)]
    ctypes.windll.crypt32.CryptUnprotectData.restype = wintypes.BOOL
    ctypes.windll.kernel32.LocalFree.argtypes = [ctypes.c_void_p]
    ctypes.windll.kernel32.LocalFree.restype = ctypes.c_void_p


def _blob(data: bytes):
    buf = ctypes.create_string_buffer(data)
    return DATA_BLOB(len(data), ctypes.cast(buf, ctypes.POINTER(wintypes.BYTE))), buf


def protect(data: bytes) -> bytes:
    """Cifra para el usuario actual de Windows mediante DPAPI."""
    if not hasattr(ctypes, "windll"):
        raise MercadoLibreError("El almacenamiento seguro requiere Windows.")
    source, keep = _blob(data); output = DATA_BLOB()
    if not ctypes.windll.crypt32.CryptProtectData(ctypes.byref(source), "8Plast MercadoLibre", None, None, None, 1, ctypes.byref(output)):
        raise ctypes.WinError()
    try: return ctypes.string_at(output.pbData, output.cbData)
    finally: ctypes.windll.kernel32.LocalFree(output.pbData)


def unprotect(data: bytes) -> bytes:
    source, keep = _blob(data); output = DATA_BLOB()
    if not ctypes.windll.crypt32.CryptUnprotectData(ctypes.byref(source), None, None, None, None, 1, ctypes.byref(output)):
        raise ctypes.WinError()
    try: return ctypes.string_at(output.pbData, output.cbData)
    finally: ctypes.windll.kernel32.LocalFree(output.pbData)


class MercadoLibreClient:
    def __init__(self, database):
        self.db = database

    def public_config(self):
        if cloud_mode():
            return {'client_id':os.getenv('MELI_CLIENT_ID',''), 'redirect_uri':os.getenv('MELI_REDIRECT_URI','')}
        if not PUBLIC_CONFIG.exists(): return {"client_id": "", "redirect_uri": ""}
        return json.loads(PUBLIC_CONFIG.read_text(encoding="utf-8"))

    def secure_config(self):
        if cloud_mode():
            from cloud_tokens import read_tokens
            result = read_tokens()
            result['client_secret'] = os.getenv('MELI_CLIENT_SECRET','')
            return result
        if not SECURE_CONFIG.exists(): return {}
        try: return json.loads(unprotect(base64.b64decode(SECURE_CONFIG.read_bytes())).decode("utf-8"))
        except Exception as exc: raise MercadoLibreError("No se pudo abrir la configuración segura de MercadoLibre.") from exc

    def save_configuration(self, client_id: str, client_secret: str, redirect_uri: str):
        if cloud_mode(): raise MercadoLibreError('Configurá las credenciales en Railway.')
        PUBLIC_CONFIG.write_text(json.dumps({"client_id": client_id.strip(), "redirect_uri": redirect_uri.strip()}, indent=2), encoding="utf-8")
        secure = self.secure_config(); secure["client_secret"] = client_secret.strip()
        self._write_secure(secure)

    def _write_secure(self, secure):
        if cloud_mode():
            from cloud_tokens import write_tokens
            write_tokens({k:v for k,v in secure.items() if k != 'client_secret'})
            return
        encrypted = base64.b64encode(protect(json.dumps(secure).encode("utf-8")))
        fd, temporary = tempfile.mkstemp(prefix=".ml_secure_", dir=SECURE_CONFIG.parent)
        try:
            with os.fdopen(fd, "wb") as stream:
                stream.write(encrypted)
                stream.flush()
                os.fsync(stream.fileno())
            os.replace(temporary, SECURE_CONFIG)
        finally:
            if os.path.exists(temporary):
                os.unlink(temporary)

    def configured(self):
        public, secure = self.public_config(), self.secure_config()
        return bool(public.get("client_id") and public.get("redirect_uri") and secure.get("client_secret"))

    def authorization_url(self):
        if not self.configured(): raise MercadoLibreError("Primero guardá Client ID, Client Secret y Redirect URI.")
        public = self.public_config(); secure = self.secure_config(); state = secrets.token_urlsafe(24)
        secure["oauth_state"] = state
        self._write_secure(secure)
        return AUTH_URL + "?" + urllib.parse.urlencode({"response_type":"code","client_id":public["client_id"],"redirect_uri":public["redirect_uri"],"state":state})

    def _request(self, url: str, method="GET", data=None, token=None, _retried=False):
        if cloud_mode() and not sync_owner():
            raise MercadoLibreError('Railway está en espera: la sincronización sigue siendo local.')
        headers={"Accept":"application/json","User-Agent":"8PlastStock/1.0"}
        if token: headers["Authorization"] = f"Bearer {token}"
        encoded = urllib.parse.urlencode(data).encode() if data is not None else None
        if encoded is not None: headers["Content-Type"]="application/x-www-form-urlencoded"
        try:
            with urllib.request.urlopen(urllib.request.Request(url,data=encoded,headers=headers,method=method),timeout=30) as response:
                return json.loads(response.read().decode("utf-8"))
        except urllib.error.HTTPError as exc:
            if exc.code == 401 and token and method == 'GET' and not _retried:
                refreshed = self.access_token(rejected_token=token)
                return self._request(url, method, data, refreshed, _retried=True)
            exc.read()
            raise MercadoLibreError(f"MercadoLibre respondió HTTP {exc.code}") from exc
        except urllib.error.URLError as exc: raise MercadoLibreError(f"No se pudo conectar con MercadoLibre: {exc.reason}") from exc

    def exchange_code(self, redirected_url_or_code: str):
        public, secure = self.public_config(), self.secure_config()
        value=redirected_url_or_code.strip(); parsed=urllib.parse.urlparse(value)
        query=urllib.parse.parse_qs(parsed.query) if parsed.scheme else {}
        code=(query.get("code") or [value])[0]; returned_state=(query.get("state") or [""])[0]
        if returned_state and returned_state != secure.get("oauth_state"): raise MercadoLibreError("La respuesta de autorización no coincide con esta solicitud.")
        payload=self._request(TOKEN_URL,"POST",{"grant_type":"authorization_code","client_id":public["client_id"],
            "client_secret":secure["client_secret"],"code":code,"redirect_uri":public["redirect_uri"]})
        self._save_tokens(secure,payload); return payload

    def _save_tokens(self, secure, payload):
        secure.update({"access_token":payload["access_token"],"refresh_token":payload.get("refresh_token",secure.get("refresh_token")),
            "user_id":payload.get("user_id",secure.get("user_id")),"expires_at":int(time.time())+int(payload.get("expires_in",0))-120})
        secure.pop("oauth_state",None)
        self._write_secure(secure)

    def access_token(self, rejected_token=None):
        with token_lock():
            return self._access_token_locked(rejected_token)

    def _access_token_locked(self, rejected_token=None):
        public, secure=self.public_config(),self.secure_config()
        if not secure.get("access_token"): raise MercadoLibreError("La cuenta todavía no fue autorizada.")
        if int(secure.get("expires_at",0)) <= int(time.time()) or (rejected_token is not None and secure.get("access_token") == rejected_token):
            payload=self._request(TOKEN_URL,"POST",{"grant_type":"refresh_token","client_id":public["client_id"],
                "client_secret":secure["client_secret"],"refresh_token":secure["refresh_token"]})
            self._save_tokens(secure,payload); secure=self.secure_config()
        return secure["access_token"]

    def connection_status(self):
        secure=self.secure_config()
        if secure.get("access_token"): return f"Cuenta autorizada · usuario {secure.get('user_id','')}"
        return "Credenciales guardadas; falta autorizar" if self.configured() else "Sin configurar"

    def get_order(self, order_id): return self._request(f"{API_URL}/orders/{order_id}", token=self.access_token())

    def _seller_id(self):
        secure=self.secure_config(); user_id=secure.get("user_id")
        if not user_id: self.access_token(); user_id=self.secure_config().get("user_id")
        return user_id

    @staticmethod
    def _iso(value: datetime):
        return value.astimezone(timezone.utc).isoformat(timespec="seconds")

    def _search_page(self, date_field: str, start: datetime, end: datetime, offset: int, limit: int = 50):
        query=urllib.parse.urlencode({"seller":self._seller_id(),"sort":"date_asc","limit":limit,
            "offset":offset,f"order.{date_field}.from":self._iso(start),f"order.{date_field}.to":self._iso(end)})
        return self._request(f"{API_URL}/orders/search?{query}",token=self.access_token())

    def _recover_stream(self, checkpoint_type: str, date_field: str, cutoff: datetime,
                        window_hours: int = 24) -> dict:
        checkpoint=self.db.marketplace_checkpoint(checkpoint_type)
        config=self.db.marketplace_processing_config()
        activation=self.db._parse_external_date(config["activation_at"]) if config and config["activation_at"] else cutoff
        covered=self.db._parse_external_date(checkpoint["covered_through"]) if checkpoint and checkpoint["covered_through"] else activation
        # ML trunca minutos/segundos en filtros: repetir una hora es deliberado y seguro por ID único.
        cursor=max(activation, covered-timedelta(hours=1))
        pages=orders=0
        try:
            while cursor < cutoff:
                window_end=min(cursor+timedelta(hours=window_hours),cutoff)
                offset=0; window_pages=window_orders=0; last_id=None
                while True:
                    response=self._search_page(date_field,cursor,window_end,offset,50)
                    results=response.get("results") or []
                    self.db.store_marketplace_orders(results)
                    window_pages += 1; window_orders += len(results)
                    if results: last_id=str(results[-1].get("id") or "")
                    paging=response.get("paging") or {}
                    total=int(paging.get("total",len(results)))
                    offset += len(results)
                    if not results and offset < total:
                        raise MercadoLibreError("Página vacía antes de completar el total de órdenes; checkpoint conservado.")
                    if offset >= total: break
                pages += window_pages; orders += window_orders
                self.db.update_marketplace_checkpoint(checkpoint_type,self._iso(window_end),last_id,
                                                       pages=window_pages,orders=window_orders)
                cursor=window_end
            return {"pages":pages,"orders":orders,"covered_through":self._iso(cutoff)}
        except Exception as exc:
            self.db.update_marketplace_checkpoint(checkpoint_type,
                checkpoint["covered_through"] if checkpoint else None, status="ERROR",
                error=f"{type(exc).__name__}: {exc}",pages=pages,orders=orders)
            raise

    def sync_orders_recovery(self, cutoff: datetime | None = None):
        """Recupera paginado, persiste primero y luego descuenta. Sólo hace GET a MercadoLibre."""
        cutoff=(cutoff or datetime.now(timezone.utc)).astimezone(timezone.utc)
        created=self._recover_stream("CREATED","date_created",cutoff)
        updated=self._recover_stream("UPDATED","date_last_updated",cutoff)
        self.db.retry_marketplace_inbox_errors()
        processing=self.db.process_marketplace_inbox()
        return {"cutoff":self._iso(cutoff),"created":created,"updated":updated,"processing":processing}

    def sync_recent_orders(self):
        """Compatibilidad: ahora ejecuta la recuperación completa y paginada."""
        return self.sync_orders_recovery()

    def audit_historical_orders(self, days: int = 180, cutoff: datetime | None = None,
                                window_days: int = 7):
        """Descarga un historial analítico separado. Nunca procesa ni modifica stock."""
        cutoff=(cutoff or datetime.now(timezone.utc)).astimezone(timezone.utc)
        start=cutoff-timedelta(days=days)
        cursor=start; pages=orders=0
        self.db.update_marketplace_audit(status="RUNNING",started_at=datetime.now().astimezone().isoformat(timespec="seconds"),
            completed_at=None,period_start=self._iso(start),period_end=self._iso(cutoff),pages_count=0,orders_count=0,last_error=None)
        try:
            while cursor < cutoff:
                window_end=min(cursor+timedelta(days=window_days),cutoff)
                offset=0
                while True:
                    response=self._search_page("date_created",cursor,window_end,offset,50)
                    results=response.get("results") or []
                    self.db.store_marketplace_audit_orders(results)
                    pages+=1; orders+=len(results)
                    self.db.update_marketplace_audit(pages_count=pages,orders_count=orders)
                    total=int((response.get("paging") or {}).get("total",len(results)))
                    offset+=len(results)
                    if not results or offset>=total:break
                cursor=window_end
            self.db.update_marketplace_audit(status="COMPLETE",completed_at=datetime.now().astimezone().isoformat(timespec="seconds"),
                pages_count=pages,orders_count=orders,last_error=None)
            return {"period_start":self._iso(start),"period_end":self._iso(cutoff),"pages":pages,"orders":orders}
        except Exception as exc:
            self.db.update_marketplace_audit(status="ERROR",pages_count=pages,orders_count=orders,
                last_error=f"{type(exc).__name__}: {exc}")
            raise


class MercadoLibreOrderPoller:
    """Consulta órdenes periódicamente. No se inicia al crear la instancia."""

    def __init__(self, client: MercadoLibreClient, on_result=None, on_error=None):
        self.client = client
        self.on_result = on_result
        self.on_error = on_error
        self._stop = threading.Event()
        self._thread = None

    @property
    def running(self):
        return bool(self._thread and self._thread.is_alive())

    def start(self):
        config = self.client.db.marketplace_processing_config()
        if not config or not config["enabled"]:
            raise MercadoLibreError("El procesamiento automático no está activado.")
        if self.running:
            return False
        self._stop.clear()
        self._thread = threading.Thread(target=self._loop, name="8Plast-ML-Orders", daemon=True)
        self._thread.start()
        return True

    def stop(self):
        self._stop.set()
        if self._thread and self._thread is not threading.current_thread():
            self._thread.join(timeout=5)

    def _loop(self):
        while not self._stop.is_set():
            try:
                result = self.client.sync_recent_orders()
                if self.on_result:
                    self.on_result(result)
            except Exception as exc:
                if self.on_error:
                    self.on_error(exc)
            config = self.client.db.marketplace_processing_config()
            if not config or not config["enabled"]:
                break
            self._stop.wait(int(config["interval_seconds"] or 300))
