"""Review-only assistants. No operational database or external write capability."""
import json
import re
import sqlite3
import unicodedata
import urllib.request
import urllib.error
import socket
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path
from flask import Blueprint, abort, flash, redirect, render_template, request


def now():
    return datetime.now(timezone.utc).isoformat()


FAILURES = {
    'CONFIGURATION_MISSING': 'La vista previa local no tiene acceso a la autenticación de Railway. Abrí el Centro de asistentes en el portal activo; no se consultó Mercado Libre.',
    'AUTHORIZATION_MISSING': 'La integración no tiene una cuenta autorizada. Completá la conexión desde Gestión en el portal activo.',
    'TOKEN_EXPIRED': 'El token fue rechazado y no pudo renovarse. Revisá la conexión OAuth desde Gestión; puede requerir volver a autorizar la cuenta.',
    'AUTH_CONFIGURATION': 'No se pudo leer la configuración segura o faltan variables de autenticación. Revisá la configuración de la integración en Railway.',
    'SYNC_STANDBY': 'La integración de Railway está en espera. Un administrador debe revisar quién es responsable de sincronizar antes de habilitar consultas.',
    'PERMISSION_DENIED': 'Mercado Libre respondió HTTP 403. Revisá los permisos de lectura y el acceso de esta cuenta al recurso indicado; la API rechazó la consulta.',
    'NOT_FOUND': 'Mercado Libre respondió HTTP 404: el recurso no está disponible para esta cuenta.',
    'RATE_LIMIT': 'Mercado Libre respondió HTTP 429: límite de consultas alcanzado. Esperá unos minutos antes de volver a consultar.',
    'API_UNAVAILABLE': 'Mercado Libre tuvo un error de servicio. Volvé a consultar más tarde.',
    'API_REQUEST': 'Mercado Libre rechazó los parámetros de consulta. Revisá el estado HTTP y el recurso registrados.',
    'NETWORK': 'No se pudo conectar con Mercado Libre. Revisá DNS, salida a internet y conexión del servidor Railway.',
    'TIMEOUT': 'La consulta a Mercado Libre superó el tiempo de espera. Volvé a intentar.',
    'INVALID_RESPONSE': 'La API devolvió datos con un formato inesperado. No se registró la consulta como exitosa.',
    'STORAGE': 'No se pudo guardar en la base de revisiones. Revisá espacio, permisos y bloqueos del volumen.',
    'INTERNAL': 'Ocurrió un error interno. Revisá el código de diagnóstico en los registros del servidor.',
}


class QueryFailure(RuntimeError):
    def __init__(self, code, resource='autenticación', status=None):
        self.code, self.resource, self.status = code, resource, status
        super().__init__(FAILURES[code])

    def public_message(self):
        return f'{self} [Código: {self.code}; recurso: {self.resource}' + (f'; HTTP {self.status}' if self.status else '') + ']'


def integration_token_provider(client):
    """Authentication capability only: reuse the owner's token lock and OAuth rotation."""
    def provide(rejected_token=None):
        from ml_integration import MercadoLibreError
        from runtime_config import cloud_mode, sync_owner
        if not cloud_mode():
            raise QueryFailure('CONFIGURATION_MISSING')
        if not sync_owner():
            raise QueryFailure('SYNC_STANDBY')
        try:
            secure = client.secure_config()
            if not secure.get('access_token'):
                raise QueryFailure('AUTHORIZATION_MISSING')
            if not client.configured():
                raise QueryFailure('AUTH_CONFIGURATION')
            return client.access_token(rejected_token=rejected_token)
        except QueryFailure:
            raise
        except (KeyError, ValueError, OSError):
            raise QueryFailure('AUTH_CONFIGURATION') from None
        except MercadoLibreError as exc:
            # The existing client returns sanitized messages. Never forward raw exceptions.
            message = str(exc)
            if 'HTTP 400' in message or 'HTTP 401' in message:
                code = 'TOKEN_EXPIRED'
            elif 'HTTP 429' in message:
                code = 'RATE_LIMIT'
            elif 'HTTP 5' in message:
                code = 'API_UNAVAILABLE'
            elif 'conectar' in message:
                code = 'NETWORK'
            else:
                code = 'AUTH_CONFIGURATION'
            raise QueryFailure(code) from None
        except Exception:
            raise QueryFailure('AUTH_CONFIGURATION') from None
    return provide


class ReadOnlyMercadoLibre:
    """GET-only resources; OAuth rotation is delegated to the existing token owner."""
    def __init__(self, token, token_provider=None):
        self.__token = token
        self.__token_provider = token_provider

    def request(self, path, method='GET', data=None):
        if method != 'GET' or data is not None or not re.fullmatch(
                r'/users/me|/questions/search\?seller_id=\d+&api_version=4&limit=50&offset=\d+(?:&sort_fields=date_created&sort_types=DESC)?|/items/MLA\d+', path):
            raise PermissionError('Esta herramienta solo permite consultas autorizadas.')
        return self._get(path)

    def _get(self, path, retried=False):
        req = urllib.request.Request('https://api.mercadolibre.com' + path,
            headers={'Authorization': 'Bearer ' + self.__token, 'Accept': 'application/json'}, method='GET')
        # Refuse redirects so credentials cannot reach another origin.
        class NoRedirect(urllib.request.HTTPRedirectHandler):
            def redirect_request(self, *args, **kwargs):
                return None
        try:
            with urllib.request.build_opener(NoRedirect).open(req, timeout=20) as response:
                return json.load(response)
        except urllib.error.HTTPError as exc:
            status = exc.code
            exc.close()
            if status == 401 and self.__token_provider and not retried:
                self.__token = self.__token_provider(rejected_token=self.__token)
                return self._get(path, retried=True)
            code = {401:'TOKEN_EXPIRED',403:'PERMISSION_DENIED',404:'NOT_FOUND',429:'RATE_LIMIT'}.get(status,
                    'API_UNAVAILABLE' if status >= 500 else 'API_REQUEST')
            raise QueryFailure(code, path.split('?')[0], status) from None
        except (TimeoutError, socket.timeout):
            raise QueryFailure('TIMEOUT', path.split('?')[0]) from None
        except urllib.error.URLError as exc:
            code = 'TIMEOUT' if isinstance(exc.reason, (TimeoutError, socket.timeout)) else 'NETWORK'
            raise QueryFailure(code, path.split('?')[0]) from None
        except (ValueError, UnicodeError):
            raise QueryFailure('INVALID_RESPONSE', path.split('?')[0]) from None


def fetch_questions(token_provider):
    """No operational DB dependency. Failed item enrichment cannot hide valid questions."""
    if token_provider is None:
        raise QueryFailure('CONFIGURATION_MISSING')
    client = ReadOnlyMercadoLibre(token_provider(), token_provider)
    me = client.request('/users/me')
    if not isinstance(me, dict) or not str(me.get('id', '')).isdigit():
        raise QueryFailure('INVALID_RESPONSE', '/users/me')
    seller = me['id']
    questions, publications, warnings = [], {}, []
    for offset in range(0, 200, 50):
        response = client.request(f'/questions/search?seller_id={seller}&api_version=4&limit=50&offset={offset}&sort_fields=date_created&sort_types=DESC')
        if not isinstance(response, dict) or not isinstance(response.get('questions'), list):
            raise QueryFailure('INVALID_RESPONSE', '/questions/search')
        batch = response['questions']
        for q in batch:
            if not isinstance(q, dict) or not re.fullmatch(r'MLA\d+', str(q.get('item_id',''))):
                raise QueryFailure('INVALID_RESPONSE', '/questions/search')
            if q.get('text') is None and q.get('status') in ('DELETED', 'BANNED'):
                warnings.append('Pregunta oculta por Mercado Libre: texto no disponible.')
                continue
            questions.append(q)
        if len(batch) < 50: break
    for q in questions:
        item_id = q['item_id']
        if item_id in publications: continue
        try:
            item = client.request('/items/' + item_id)
            if not isinstance(item, dict): raise QueryFailure('INVALID_RESPONSE', '/items/' + item_id)
            publications[item_id] = item
        except QueryFailure as exc:
            if exc.code not in ('NOT_FOUND', 'PERMISSION_DENIED'): raise
            publications[item_id] = {}
            warnings.append(exc.public_message() + ' La pregunta se cargó sin atributos; requiere confirmar el producto.')
    return {'questions': questions, 'publications': publications}, warnings


def normalized(text):
    return ''.join(c for c in unicodedata.normalize('NFD', text.lower()) if unicodedata.category(c) != 'Mn')


def suggest(question, facts):
    """Conservative, deterministic drafting until an AI provider is configured."""
    q = normalized(question)
    topics = {
        'presentacion': ('rollo', 'suelta', 'presentacion', 'tubo'),
        'unidades_pack': ('cantidad', 'cuantas', 'cuantos', 'unidades', 'pack', 'trae'),
        'medida': ('medida', 'ancho', 'largo', 'tamano'),
        'espesor_publicado': ('micron', 'espesor'),
        'color': ('color', 'roja', 'negra', 'verde', 'azul', 'amarilla', 'cristal'),
        'peso': ('peso', 'pesa', 'kilo'),
        'usos': ('uso', 'sirve', 'resiste', 'resistencia', 'patologic', 'certifica'),
        'logistica': ('entrega', 'envio', 'llega', 'full', 'flex', 'retiro', 'stock', 'disponib'),
    }
    selected = [k for k, words in topics.items() if any(w in q for w in words)]
    # Dynamic promises and technical suitability always require human confirmation.
    missing = [k for k in selected if k not in facts or k in ('usos', 'logistica')]
    used = {k: facts[k] for k in selected if k in facts and k not in ('usos', 'logistica')}
    if not selected:
        missing = ['Interpretación de la consulta e información necesaria']
    labels = {'presentacion': 'Presentación', 'unidades_pack': 'Unidades por pack', 'medida': 'Medida',
              'espesor_publicado': 'Espesor publicado', 'color': 'Color', 'peso': 'Peso publicado'}
    answer = 'Hola. ' + ' '.join(f'{labels[k]}: {v}.' for k, v in used.items())
    if missing:
        answer += ' Necesito confirmar la información de tu consulta antes de darte una respuesta precisa.'
    return answer.strip(), used, missing


class ReviewStore:
    def __init__(self, path):
        self.path = str(path)
        Path(path).parent.mkdir(parents=True, exist_ok=True)
        with self.connect() as con:
            con.executescript('''
                CREATE TABLE IF NOT EXISTS questions (
                    id TEXT PRIMARY KEY, item_id TEXT NOT NULL, title TEXT NOT NULL,
                    question TEXT NOT NULL, external_status TEXT NOT NULL, external_answer TEXT,
                    facts TEXT NOT NULL, original TEXT NOT NULL, edited TEXT, approved TEXT,
                    missing TEXT NOT NULL, used TEXT NOT NULL, status TEXT NOT NULL DEFAULT 'PENDIENTE',
                    updated TEXT NOT NULL, version INTEGER NOT NULL DEFAULT 1);
                CREATE TABLE IF NOT EXISTS decisions (
                    id INTEGER PRIMARY KEY, question_id TEXT NOT NULL, action TEXT NOT NULL,
                    before_text TEXT NOT NULL, after_text TEXT NOT NULL, created TEXT NOT NULL);
                CREATE TABLE IF NOT EXISTS sync (id INTEGER PRIMARY KEY CHECK(id=1),
                    success TEXT, error TEXT, source TEXT);
            ''')
            columns = {r[1] for r in con.execute('PRAGMA table_info(sync)')}
            for name in ('attempted', 'warning'):
                if name not in columns:
                    con.execute(f'ALTER TABLE sync ADD COLUMN {name} TEXT')

    @contextmanager
    def connect(self):
        con = sqlite3.connect(self.path, timeout=15)
        con.row_factory = sqlite3.Row
        try:
            with con:
                yield con
        finally:
            con.close()

    def ingest(self, payload, source):
        if not isinstance(payload, dict) or not isinstance(payload.get('questions'), list):
            raise ValueError('Se requiere un objeto con questions y publications.')
        publications = payload.get('publications', {})
        if not isinstance(publications, dict) or len(payload['questions']) > 2000:
            raise ValueError('Formato o tamaño no válido.')
        count = 0
        with self.connect() as con:
            for q in payload['questions']:
                if not isinstance(q, dict) or not str(q.get('id', '')).isdigit() or not re.fullmatch(r'MLA\d+', str(q.get('item_id', ''))):
                    raise ValueError('Pregunta sin ID o publicación MLA válida.')
                item = publications.get(q['item_id'], {})
                if not isinstance(item, dict): raise ValueError('Publicación inválida.')
                text = q.get('text', '')
                if not isinstance(text, str) or not text.strip() or len(text) > 10000:
                    raise ValueError('Texto de pregunta inválido.')
                facts = publication_facts(item)
                draft, used, missing = suggest(text, facts)
                answer = (q.get('answer') or {}).get('text', '')
                values = (str(q['id']), q['item_id'], str(item.get('title', q['item_id'])), text,
                    str(q.get('status', 'UNKNOWN')), str(answer), json.dumps(facts), draft,
                    json.dumps(missing), json.dumps(used), now())
                existing = con.execute('SELECT * FROM questions WHERE id=?', (values[0],)).fetchone()
                if existing and (existing['item_id'] != values[1] or existing['question'] != text):
                    raise ValueError('Un ID existente no puede cambiar de pregunta o publicación.')
                if existing:
                    con.execute('UPDATE questions SET external_status=?, external_answer=?, updated=? WHERE id=?',
                        (values[4], values[5], values[-1], values[0]))
                else:
                    con.execute('INSERT INTO questions(id,item_id,title,question,external_status,external_answer,facts,original,missing,used,updated) VALUES (?,?,?,?,?,?,?,?,?,?,?)', values)
                    count += 1
            con.execute('INSERT INTO sync(id,success,error,source) VALUES(1,?,NULL,?) ON CONFLICT(id) DO UPDATE SET success=excluded.success,error=NULL,source=excluded.source', (now(), source))
        return count

    def decide(self, qid, action, text, version):
        if action not in ('APROBADA', 'RECHAZADA', 'EDITADA', 'REVISADA'):
            raise ValueError('Acción inválida.')
        with self.connect() as con:
            con.execute('BEGIN IMMEDIATE')
            q = con.execute('SELECT * FROM questions WHERE id=?', (qid,)).fetchone()
            if not q or q['version'] != version: raise ValueError('La sugerencia cambió. Actualizá la página.')
            if action in ('APROBADA', 'EDITADA') and (not text.strip() or len(text) > 10000):
                raise ValueError('Escribí una respuesta válida.')
            if action == 'APROBADA' and q['external_status'] != 'UNANSWERED':
                raise ValueError('Solo se aprueban preguntas pendientes en Mercado Libre.')
            before = q['edited'] or q['original']
            con.execute('INSERT INTO decisions(question_id,action,before_text,after_text,created) VALUES(?,?,?,?,?)',
                        (qid, action, before, text, now()))
            con.execute('UPDATE questions SET status=?,edited=?,approved=?,version=version+1 WHERE id=?',
                (action, text if action in ('EDITADA','APROBADA') else q['edited'], text if action == 'APROBADA' else None, qid))


def publication_facts(item):
    # Only explicit public attributes; no production thickness, stock or delivery promises.
    allowed = {'UNITS_PER_PACK': 'unidades_pack', 'COLOR': 'color', 'THICKNESS': 'espesor_publicado',
               'SALE_FORMAT': 'presentacion', 'WEIGHT': 'peso'}
    facts = {}
    attrs = {a.get('id'): a.get('value_name') for a in item.get('attributes', []) if isinstance(a, dict)}
    for attr, key in allowed.items():
        if attrs.get(attr): facts[key] = str(attrs[attr])
    if attrs.get('WIDTH') and attrs.get('LENGTH'):
        facts['medida'] = f"{attrs['WIDTH']} × {attrs['LENGTH']}"
    # Variation-specific facts are deliberately not inferred for unqualified questions.
    if item.get('variations'):
        facts = {}
    return facts


def register_center(app, review_path, token_provider=None):
    store = ReviewStore(review_path)
    bp = Blueprint('assistants', __name__)
    app.extensions['assistant_reviews'] = store

    @bp.get('/asistentes')
    def index():
        with store.connect() as con:
            rows = [dict(r) for r in con.execute('SELECT * FROM questions ORDER BY updated DESC')]
            history = [dict(r) for r in con.execute('SELECT d.*,q.item_id,q.question FROM decisions d JOIN questions q ON q.id=d.question_id ORDER BY d.id DESC LIMIT 100')]
            sync = con.execute('SELECT * FROM sync WHERE id=1').fetchone()
        for r in rows:
            for field in ('facts', 'missing', 'used'): r[field] = json.loads(r[field])
            # Corrections are examples scoped to this exact publication, never global rules.
            r['examples'] = [h for h in history if h['item_id'] == r['item_id'] and h['action'] == 'APROBADA'][:3]
        return render_template('assistants.html', rows=rows, history=history, sync=sync,
            connection_ready=token_provider is not None,
            connection_message=None if token_provider else FAILURES['CONFIGURATION_MISSING'])

    @bp.post('/asistentes/importar')
    def import_file():
        try:
            f = request.files.get('file')
            if not f: raise ValueError('Seleccioná un archivo JSON.')
            raw = f.read(2_000_001)
            if len(raw) > 2_000_000: raise ValueError('El máximo es 2 MB.')
            n = store.ingest(json.loads(raw), 'Archivo importado; fecha real de los datos a confirmar')
            flash(f'{n} preguntas nuevas importadas. Las decisiones previas se conservan.')
        except (ValueError, TypeError, KeyError): flash('No se importó: revisá el formato del archivo JSON.')
        return redirect('/asistentes')

    @bp.post('/asistentes/decision/<qid>')
    def decision(qid):
        try:
            store.decide(qid, request.form['action'], request.form.get('text',''), int(request.form['version']))
            flash('Decisión guardada. No se envió ninguna respuesta ni se ejecutaron cambios externos.')
        except (ValueError, KeyError) as exc: flash(str(exc))
        return redirect('/asistentes')

    @bp.post('/asistentes/consultar')
    def synchronize():
        try:
            payload, warnings = fetch_questions(token_provider)
            n = store.ingest(payload, 'API oficial: hasta 200 preguntas recientes; historial parcial')
            with store.connect() as con:
                con.execute('UPDATE sync SET attempted=?,warning=? WHERE id=1', (now(), '\n'.join(warnings) or None))
            flash(f'Consulta finalizada: {len(payload["questions"])} preguntas recibidas, {n} nuevas. Historial limitado a 200 registros.')
        except Exception as exc:
            failure = exc if isinstance(exc, QueryFailure) else QueryFailure(
                'STORAGE' if isinstance(exc, sqlite3.Error) else 'INVALID_RESPONSE' if isinstance(exc, (ValueError, TypeError, KeyError, AttributeError)) else 'INTERNAL')
            message = failure.public_message()
            # Only structured classifications: no token, response body, URL query or raw exception.
            app.logger.warning('assistant_query_failed code=%s resource=%s http=%s',
                               failure.code, failure.resource, failure.status)
            try:
                with store.connect() as con:
                    con.execute('INSERT INTO sync(id,error,attempted) VALUES(1,?,?) ON CONFLICT(id) DO UPDATE SET error=excluded.error,attempted=excluded.attempted', (message, now()))
            except sqlite3.Error:
                app.logger.warning('assistant_query_failed code=STORAGE resource=review_database')
            flash(message)
        return redirect('/asistentes')

    app.register_blueprint(bp)
