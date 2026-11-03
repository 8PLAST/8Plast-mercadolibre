"""Human-only publication gateway. Never called by polling, AI or Telegram."""
import json
import os
import re
import urllib.request
import urllib.error
from assistant_center import ReadOnlyMercadoLibre, now


def initialize(store):
    with store.connect() as con:
        con.execute('''CREATE TABLE IF NOT EXISTS assistant_publications_outbox (
          question_id TEXT PRIMARY KEY, answer TEXT NOT NULL, state TEXT NOT NULL,
          approved_at TEXT NOT NULL, updated TEXT NOT NULL, error TEXT)''')


def publish(store, qid, text, version, provider):
    if os.getenv('ASSISTANT_PUBLISH_ENABLED')!='1' or provider is None:
        raise ValueError('Publicación deshabilitada. Requiere habilitación explícita al desplegar.')
    if not re.fullmatch(r'\d+',qid) or not text.strip() or len(text)>2000:
        raise ValueError('Respuesta inválida: máximo 2000 caracteres.')
    initialize(store)
    token=provider()
    client=ReadOnlyMercadoLibre(token,provider)
    me=client.request('/users/me')
    external=client.request('/questions/'+qid)
    if external.get('status')=='ANSWERED' and str(external.get('seller_id'))==str(me.get('id')):
        from assistant_manual_learning import reconcile
        reconcile(store,qid,external)
        raise ValueError('La pregunta ya fue respondida en Mercado Libre. Se recuperó la respuesta; no se publicará otra.')
    with store.connect() as con:
        con.execute('BEGIN IMMEDIATE')
        q=con.execute('SELECT * FROM questions WHERE id=?',(qid,)).fetchone()
        if not q or q['version']!=version or q['external_status']!='UNANSWERED':
            raise ValueError('La pregunta cambió o ya fue respondida. Actualizá antes de aprobar.')
        if str(external.get('id'))!=qid or external.get('item_id')!=q['item_id'] or str(external.get('seller_id'))!=str(me.get('id')) or external.get('status')!='UNANSWERED':
            raise ValueError('Mercado Libre no confirma una pregunta pendiente de tu cuenta.')
        if con.execute('SELECT 1 FROM assistant_publications_outbox WHERE question_id=?',(qid,)).fetchone():
            raise ValueError('Ya existe un intento de publicación. Revisá su estado antes de volver a responder.')
        stamp=now()
        con.execute('INSERT INTO assistant_publications_outbox VALUES(?,?,?,?,?,NULL)',(qid,text,'SENDING',stamp,stamp))
        con.execute('INSERT INTO decisions(question_id,action,before_text,after_text,created) VALUES(?,?,?,?,?)',
                    (qid,'APROBADA',q['edited'] or q['original'],text,stamp))
        con.execute("UPDATE questions SET status='APROBADA',edited=?,approved=?,version=version+1 WHERE id=?",(text,text,qid))
    # Exactly one POST attempt. No replay after timeout, restart or ambiguous result.
    class NoRedirect(urllib.request.HTTPRedirectHandler):
        def redirect_request(self,*args,**kwargs): return None
    req=urllib.request.Request('https://api.mercadolibre.com/answers',
        data=json.dumps({'question_id':int(qid),'text':text},ensure_ascii=False).encode('utf-8'),
        headers={'Authorization':'Bearer '+token,'Content-Type':'application/json'},method='POST')
    state='UNKNOWN';error='Verificar en Mercado Libre; no se reintenta automáticamente.'
    try:
        with urllib.request.build_opener(NoRedirect()).open(req,timeout=25) as response:
            result=json.loads(response.read(1_000_000))
        if str(result.get('id'))==qid and result.get('status')=='ANSWERED' and (result.get('answer') or {}).get('text')==text:
            state='PUBLISHED';error=None
    except urllib.error.HTTPError as exc:
        state='FAILED';error='HTTP_'+str(exc.code)
    except (OSError,ValueError,TypeError): pass
    with store.connect() as con:
        con.execute('UPDATE assistant_publications_outbox SET state=?,error=?,updated=? WHERE question_id=?',(state,error,now(),qid))
        if state=='PUBLISHED':
            con.execute("UPDATE questions SET external_status='ANSWERED',external_answer=? WHERE id=?",(text,qid))
            con.execute('INSERT OR IGNORE INTO assistant_answer_history(question_id,answer,source,recorded) VALUES(?,?,?,?)',(qid,text,'Aprobación explícita en panel',now()))
    return state
