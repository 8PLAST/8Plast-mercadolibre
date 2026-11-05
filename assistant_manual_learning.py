"""Exact external-answer observations and conservative example-based learning.

API proves an answer exists, not which person/client authored it. Answers with
no matching confirmed outbox entry are labelled outside the assistant. Nothing
here mutates the owner catalog or promotes a single example into a global rule.
"""
import difflib
import hashlib
import json
import re
from datetime import datetime,timezone


def initialize_connection(con):
    con.executescript('''
      CREATE TABLE IF NOT EXISTS assistant_response_observations (
        id INTEGER PRIMARY KEY, question_id TEXT NOT NULL, item_id TEXT NOT NULL,
        answer_hash TEXT NOT NULL, answer_text TEXT NOT NULL, suggested_text TEXT NOT NULL,
        suggestion_version INTEGER NOT NULL, origin TEXT NOT NULL, source TEXT NOT NULL,
        published_at TEXT, observed_at TEXT NOT NULL, comparison TEXT NOT NULL,
        UNIQUE(question_id,answer_hash));
      CREATE TABLE IF NOT EXISTS assistant_answer_checks (
        question_id TEXT PRIMARY KEY, checked_at REAL NOT NULL DEFAULT 0);
    ''')


def comparison(suggestion,answer):
    old=re.findall(r'\S+',suggestion);new=re.findall(r'\S+',answer)
    added=[];removed=[]
    for tag,a,b,c,d in difflib.SequenceMatcher(None,old,new,autojunk=False).get_opcodes():
        if tag in ('replace','insert'):added.append(' '.join(new[c:d]))
        if tag in ('replace','delete'):removed.append(' '.join(old[a:b]))
    text=' '.join(added+removed).lower()
    topics={label for label,pattern in {
        'medidas':r'\bcm\b|ancho|largo|\d+[x×]\d+', 'espesor':r'micron|espesor',
        'material_aptitud':r'virgen|recuperado|alimento|freez|certific',
        'precio_condiciones':r'\$|precio|descuento|mínim|minim|envío|envio|stock|entrega',
        'carga':r'\bkg\b|kilo|resist|peso'}.items() if re.search(pattern,text)}
    return {'has_suggestion':bool(suggestion),'exact_match':bool(suggestion) and suggestion==answer,
            'similarity':round(difflib.SequenceMatcher(None,old,new,autojunk=False).ratio(),4) if suggestion else None,
            'added':added,'omitted':removed,'suggestion_characters':len(suggestion),'answer_characters':len(answer),
            'review_topics':sorted(topics),'style':{'starts_with_greeting':bool(re.match(r'(?i)^[¡\s]*(hola|buen)',answer)),'has_closing':bool(re.search(r'(?i)saludos|gracias|8plast',answer)),'word_count':len(new)},'learning_scope':'example_only',
            'warning':'Diferencias textuales, no validación de veracidad. No modifica catálogo ni crea reglas globales.'}


def observe(con,q,answer,status='ANSWERED',source='API oficial Mercado Libre',published_at=None):
    """Called within caller's transaction before any draft/evidence refresh."""
    if status!='ANSWERED' or not isinstance(answer,str) or not answer.strip():return None
    exists=con.execute("SELECT 1 FROM sqlite_master WHERE name='assistant_publications_outbox'").fetchone()
    outbox=con.execute('SELECT * FROM assistant_publications_outbox WHERE question_id=?',(q['id'],)).fetchone() if exists else None
    if outbox and outbox['answer']==answer:
        origin='ASSISTANT_CONFIRMED' if outbox['state']=='PUBLISHED' else 'ORIGIN_UNCERTAIN'
    else:origin='OUTSIDE_ASSISTANT'
    drafts_exist=con.execute("SELECT 1 FROM sqlite_master WHERE name='assistant_drafts'").fetchone()
    generated=con.execute("SELECT state FROM assistant_drafts WHERE question_id=?",(q['id'],)).fetchone() if drafts_exist else None
    # Do not call a deterministic fallback an AI suggestion in comparison reports.
    suggestion=(q['edited'] or q['original']) if (generated and generated['state']=='GENERATED') or q['approved'] or q['edited'] else ''
    if published_at:
        con.execute('UPDATE questions SET external_answer_created=? WHERE id=?',(published_at,q['id']))
    stamp=datetime.now(timezone.utc).isoformat();digest=hashlib.sha256(answer.encode('utf-8')).hexdigest()
    inserted=con.execute('INSERT OR IGNORE INTO assistant_response_observations(question_id,item_id,answer_hash,answer_text,suggested_text,suggestion_version,origin,source,published_at,observed_at,comparison) VALUES(?,?,?,?,?,?,?,?,?,?,?)',
                (q['id'],q['item_id'],digest,answer,suggestion,q['version'],origin,source,published_at,stamp,json.dumps(comparison(suggestion,answer),ensure_ascii=False))).rowcount
    if q['external_status']!='ANSWERED' or q['external_answer']!=answer:
        con.execute('UPDATE questions SET external_status=?,external_answer=?,status=?,updated=?,version=version+1 WHERE id=?',
                    ('ANSWERED',answer,'APROBADA' if origin=='ASSISTANT_CONFIRMED' else 'RESPONDIDA_MANUALMENTE' if origin=='OUTSIDE_ASSISTANT' else 'RESPONDIDA_ORIGEN_INCIERTO',stamp,q['id']))
    elif inserted and origin=='OUTSIDE_ASSISTANT' and q['status']=='PENDIENTE':
        con.execute("UPDATE questions SET status='RESPONDIDA_MANUALMENTE' WHERE id=?",(q['id'],))
    return origin


def reconcile(store,qid,external):
    """Targeted GET response: preserve publication attributes and generated text."""
    if str(external.get('id'))!=str(qid):raise ValueError('Respuesta API no corresponde a la pregunta')
    with store.connect() as con:
        con.execute('BEGIN IMMEDIATE')
        q=con.execute('SELECT * FROM questions WHERE id=?',(qid,)).fetchone()
        if not q or external.get('item_id')!=q['item_id']:raise ValueError('Publicación API no coincide')
        answer=(external.get('answer') or {}).get('text','')
        origin=observe(con,q,answer,external.get('status'),published_at=(external.get('answer') or {}).get('date_created'))
        if origin:
            con.execute('INSERT OR IGNORE INTO assistant_answer_history(question_id,answer,source,recorded) VALUES(?,?,?,?)',
                        (qid,answer,'API oficial: respuesta externa detectada',datetime.now(timezone.utc).isoformat()))
        return origin
