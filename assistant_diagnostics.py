"""Connection probe isolated from buyer replies, Telegram and inventory."""
import os
import time
from assistant_center import now


def initialize(store):
    with store.connect() as con:
        con.execute('''CREATE TABLE IF NOT EXISTS assistant_ai_probe (
            id INTEGER PRIMARY KEY CHECK(id=1), attempted TEXT, state TEXT,
            error TEXT, duration REAL, retry_after REAL DEFAULT 0)''')
        con.execute('INSERT OR IGNORE INTO assistant_ai_probe(id) VALUES(1)')


def status(store):
    initialize(store)
    with store.connect() as con:
        result=dict(con.execute('SELECT * FROM assistant_ai_probe WHERE id=1').fetchone())
    result['model']=os.getenv('ASSISTANT_AI_MODEL','gpt-4.1-mini')
    result['build_sha']=os.getenv('RAILWAY_GIT_COMMIT_SHA','unknown')
    result['automatic_publishing']=False  # No automatic publication entry point exists.
    return result


def probe(store):
    from assistant_automation import ai_draft, ServiceFailure, initialize as init_ai
    init_ai(store)
    initialize(store)
    with store.connect() as con:
        con.execute('BEGIN IMMEDIATE')
        row=con.execute('SELECT * FROM assistant_ai_probe WHERE id=1').fetchone()
        if (row['retry_after'] or 0)>time.time():
            return dict(row)
        con.execute("UPDATE assistant_ai_probe SET state='CHECKING',attempted=?,retry_after=? WHERE id=1",
                    (now(),time.time()+60))
    clock=time.monotonic()
    error=None
    try:
        ai_draft('Prueba de conexión interna. Respondé brevemente que recibiste esta prueba. No es una consulta de comprador.',{},
                 {'knowledge':[],'examples':[]})
        state='OK'
    except ServiceFailure as exc:
        state='FAILED';error=exc.code
    with store.connect() as con:
        con.execute('UPDATE assistant_ai_probe SET state=?,error=?,duration=? WHERE id=1',
                    (state,error,round(time.monotonic()-clock,3)))
        if state=='OK':
            con.execute('UPDATE assistant_ai_control SET blocked_error=NULL,retry_at=0 WHERE id=1')
            # Only previously blocked pending drafts; never regenerate completed ones.
            con.execute("UPDATE assistant_drafts SET state='FALLBACK',retry_at=0 WHERE state='BLOCKED' AND error IN ('AI_QUOTA','HTTP_401','HTTP_403','AI_NOT_CONFIGURED')")
    return status(store)
