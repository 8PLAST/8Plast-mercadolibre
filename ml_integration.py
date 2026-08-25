from __future__ import annotations

import base64
import ctypes
import json
import secrets
import time
import urllib.error
import urllib.parse
import urllib.request
from ctypes import wintypes
from pathlib import Path


ROOT = Path(__file__).resolve().parent
PUBLIC_CONFIG = ROOT / "mercadolibre_config.json"
SECURE_CONFIG = ROOT / "mercadolibre_secure.dat"
AUTH_URL = "https://auth.mercadolibre.com.ar/authorization"
TOKEN_URL = "https://api.mercadolibre.com/oauth/token"
API_URL = "https://api.mercadolibre.com"


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
        if not PUBLIC_CONFIG.exists(): return {"client_id": "", "redirect_uri": ""}
        return json.loads(PUBLIC_CONFIG.read_text(encoding="utf-8"))

    def secure_config(self):
        if not SECURE_CONFIG.exists(): return {}
        try: return json.loads(unprotect(base64.b64decode(SECURE_CONFIG.read_bytes())).decode("utf-8"))
        except Exception as exc: raise MercadoLibreError("No se pudo abrir la configuración segura de MercadoLibre.") from exc

    def save_configuration(self, client_id: str, client_secret: str, redirect_uri: str):
        PUBLIC_CONFIG.write_text(json.dumps({"client_id": client_id.strip(), "redirect_uri": redirect_uri.strip()}, indent=2), encoding="utf-8")
        secure = self.secure_config(); secure["client_secret"] = client_secret.strip()
        SECURE_CONFIG.write_bytes(base64.b64encode(protect(json.dumps(secure).encode("utf-8"))))

    def configured(self):
        public, secure = self.public_config(), self.secure_config()
        return bool(public.get("client_id") and public.get("redirect_uri") and secure.get("client_secret"))

    def authorization_url(self):
        if not self.configured(): raise MercadoLibreError("Primero guardá Client ID, Client Secret y Redirect URI.")
        public = self.public_config(); secure = self.secure_config(); state = secrets.token_urlsafe(24)
        secure["oauth_state"] = state
        SECURE_CONFIG.write_bytes(base64.b64encode(protect(json.dumps(secure).encode("utf-8"))))
        return AUTH_URL + "?" + urllib.parse.urlencode({"response_type":"code","client_id":public["client_id"],"redirect_uri":public["redirect_uri"],"state":state})

    def _request(self, url: str, method="GET", data=None, token=None):
        headers={"Accept":"application/json","User-Agent":"8PlastStock/1.0"}
        if token: headers["Authorization"] = f"Bearer {token}"
        encoded = urllib.parse.urlencode(data).encode() if data is not None else None
        if encoded is not None: headers["Content-Type"]="application/x-www-form-urlencoded"
        try:
            with urllib.request.urlopen(urllib.request.Request(url,data=encoded,headers=headers,method=method),timeout=30) as response:
                return json.loads(response.read().decode("utf-8"))
        except urllib.error.HTTPError as exc:
            detail=exc.read().decode("utf-8",errors="replace")
            raise MercadoLibreError(f"MercadoLibre respondió {exc.code}: {detail[:300]}") from exc
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
        SECURE_CONFIG.write_bytes(base64.b64encode(protect(json.dumps(secure).encode("utf-8"))))

    def access_token(self):
        public, secure=self.public_config(),self.secure_config()
        if not secure.get("access_token"): raise MercadoLibreError("La cuenta todavía no fue autorizada.")
        if int(secure.get("expires_at",0)) <= int(time.time()):
            payload=self._request(TOKEN_URL,"POST",{"grant_type":"refresh_token","client_id":public["client_id"],
                "client_secret":secure["client_secret"],"refresh_token":secure["refresh_token"]})
            self._save_tokens(secure,payload); secure=self.secure_config()
        return secure["access_token"]

    def connection_status(self):
        secure=self.secure_config()
        if secure.get("access_token"): return f"Cuenta autorizada · usuario {secure.get('user_id','')}"
        return "Credenciales guardadas; falta autorizar" if self.configured() else "Sin configurar"

    def get_order(self, order_id): return self._request(f"{API_URL}/orders/{order_id}", token=self.access_token())

    def sync_recent_orders(self):
        secure=self.secure_config(); user_id=secure.get("user_id")
        if not user_id: self.access_token(); user_id=self.secure_config().get("user_id")
        query=urllib.parse.urlencode({"seller":user_id,"sort":"date_desc","limit":50})
        response=self._request(f"{API_URL}/orders/search?{query}",token=self.access_token())
        summary={"orders":0,"processed":0,"duplicates":0,"unassociated":set()}
        for order in reversed(response.get("results") or []):
            result=self.db.process_marketplace_order(order); summary["orders"]+=1
            summary["processed"]+=result["processed"]; summary["duplicates"]+=result["duplicates"]
            summary["unassociated"].update(result["unassociated"])
        summary["unassociated"]=sorted(summary["unassociated"]); return summary
