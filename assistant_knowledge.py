"""Local retrieval over the complete review database. No model training or external index."""
import json
import re
import unicodedata
from datetime import datetime, timezone


def initialize(store):
    with store.connect() as con:
        con.executescript('''
        CREATE TABLE IF NOT EXISTS assistant_question_versions (
          id INTEGER PRIMARY KEY, question_id TEXT NOT NULL, version INTEGER NOT NULL,
          snapshot TEXT NOT NULL, recorded TEXT NOT NULL, UNIQUE(question_id,version));
        CREATE TABLE IF NOT EXISTS assistant_knowledge (
          id INTEGER PRIMARY KEY, scope TEXT NOT NULL, field TEXT NOT NULL,
          value TEXT NOT NULL, source TEXT NOT NULL, updated TEXT NOT NULL,
          supersedes INTEGER, active INTEGER NOT NULL DEFAULT 1);
        CREATE TABLE IF NOT EXISTS assistant_answer_history (
          id INTEGER PRIMARY KEY, question_id TEXT NOT NULL, answer TEXT NOT NULL,
          source TEXT NOT NULL, recorded TEXT NOT NULL,
          UNIQUE(question_id, answer));
        CREATE TABLE IF NOT EXISTS assistant_attempts (
          id INTEGER PRIMARY KEY, question_id TEXT NOT NULL, started TEXT NOT NULL,
          duration REAL NOT NULL, state TEXT NOT NULL, error TEXT,
          input_tokens INTEGER DEFAULT 0, output_tokens INTEGER DEFAULT 0,
          cost_usd REAL, evidence TEXT NOT NULL DEFAULT '{}');
        ''')
        if 'draft_text' not in {r[1] for r in con.execute('PRAGMA table_info(assistant_attempts)')}:
            con.execute('ALTER TABLE assistant_attempts ADD COLUMN draft_text TEXT')
        from assistant_manual_learning import initialize_connection
        initialize_connection(con)
        # Backfill every stored published answer, not only the latest API window.
        con.execute('''INSERT OR IGNORE INTO assistant_answer_history(question_id,answer,source,recorded)
          SELECT id,external_answer,'Mercado Libre: respuesta publicada',updated FROM questions
          WHERE external_answer IS NOT NULL AND trim(external_answer)<>'' ''')
        from assistant_manual_learning import observe
        historical=con.execute("SELECT q.* FROM questions q WHERE q.external_status='ANSWERED' AND trim(COALESCE(q.external_answer,''))<>'' AND NOT EXISTS(SELECT 1 FROM assistant_response_observations o WHERE o.question_id=q.id AND o.answer_text=q.external_answer)").fetchall()
        for q in historical:observe(con,q,q['external_answer'],source='Historial almacenado: respuesta publicada')


def save(store, scope, field, value, source, supersedes=None):
    if scope != 'global' and not re.fullmatch(r'MLA\d+', scope):
        raise ValueError('Usá global o el ID MLA exacto; no se mezclan productos por nombre.')
    if not all(isinstance(x,str) and x.strip() and len(x)<=4000 for x in (field,value,source)):
        raise ValueError('Completá campo, valor y fuente (máximo 4000 caracteres).')
    with store.connect() as con:
        if supersedes:
            old=con.execute('SELECT * FROM assistant_knowledge WHERE id=?',(supersedes,)).fetchone()
            if not old or old['scope']!=scope or old['field']!=field: raise ValueError('Versión incompatible.')
            con.execute('UPDATE assistant_knowledge SET active=0 WHERE id=?',(supersedes,))
        con.execute('INSERT INTO assistant_knowledge(scope,field,value,source,updated,supersedes) VALUES(?,?,?,?,?,?)',
                    (scope,field.strip(),value.strip(),source.strip(),datetime.now(timezone.utc).isoformat(),supersedes))


def words(text):
    text=unicodedata.normalize('NFKD',text.lower()).encode('ascii','ignore').decode()
    return set(re.findall(r'[a-z0-9]+',text))-{'hola','de','la','el','que','para','en','y','si','un','una','las','los','tenes'}


def retrieve(store, qid, item_id, question, limit=8):
    """Scan all history; bounded prompt. Cross-item examples are style only."""
    query=words(question)
    with store.connect() as con:
        entries=[dict(r) for r in con.execute("SELECT * FROM assistant_knowledge WHERE active=1 AND scope IN ('global',?) ORDER BY id DESC",(item_id,))]
        # Latest decision wins. A rejection excludes that question from positive examples.
        rows=con.execute('''SELECT q.*, d.action,d.after_text,d.created AS decision_date,
          h.answer AS archived_answer,h.recorded AS archived_date, o.answer_text AS manual_answer,o.origin AS answer_origin,o.comparison AS manual_comparison,o.observed_at AS manual_observed_at,o.source AS manual_source
          FROM questions q
          LEFT JOIN decisions d ON d.id=(SELECT max(id) FROM decisions WHERE question_id=q.id)
          LEFT JOIN assistant_answer_history h ON h.id=(SELECT max(id) FROM assistant_answer_history WHERE question_id=q.id)
          LEFT JOIN assistant_response_observations o ON o.id=(SELECT max(id) FROM assistant_response_observations WHERE question_id=q.id)
          WHERE q.id<>?''',(qid,)).fetchall()
    from technical_catalog import evidence, load
    with store.connect() as con:
        current=con.execute('SELECT facts,title FROM questions WHERE id=?',(qid,)).fetchone()
    technical=evidence(store,item_id,json.loads(current['facts']) if current else {})
    candidates=[]
    for r in rows:
        overlap=len(query & words(r['question']))/max(1,len(query | words(r['question'])))
        exact=r['item_id']==item_id
        if not exact and overlap<0.25: continue
        manual=(not (r['manual_source'] and r['manual_source'].startswith('Historial almacenado') and r['action'] in ('EDITADA','APROBADA','RECHAZADA')) and r['answer_origin']=='OUTSIDE_ASSISTANT' and r['manual_answer']==r['external_answer'] and (not r['decision_date'] or r['decision_date']<r['manual_observed_at']))
        rejected=r['action']=='RECHAZADA' and not manual
        answer=r['manual_answer'] if manual else r['after_text'] if r['action'] in ('EDITADA','APROBADA') else r['external_answer'] or r['archived_answer']
        if not answer: continue
        priority=3 if manual else 2 if r['action'] in ('EDITADA','APROBADA') else 1
        candidates.append(((int(exact),priority,overlap,r['decision_date'] or r['updated']),{
            'id':'history:'+r['id'],'item_id':r['item_id'],'question':r['question'][:1200],
            'answer':answer[:2000],'source':'MANUAL_MERCADO_LIBRE' if manual else r['action'] or 'PUBLICADA',
            'date':r['decision_date'] or r['updated'],'same_publication':exact,
            'manual_comparison':json.loads(r['manual_comparison']) if manual else None,
            'rejected':rejected,'authority':'negative_example' if rejected else 'historical_reference' if exact else 'style_only'}))
    if technical['identity_confirmed']:
        expected=technical['knowledge'][0]['value']['real_microns_per_face']
        for _,example in candidates:
            if not example['same_publication']:continue
            stated=re.findall(r'(\d+)\s*micrones?\s*reales',example['answer'],re.I)
            if any(int(x)!=expected for x in stated):
                example['authority']='potentially_outdated_technical_reference'
                example['technical_warning']='Micraje real histórico distinto del catálogo; no usar como hecho vigente.'
    candidates.sort(key=lambda v:v[0],reverse=True)
    evidence=technical['knowledge']+[{**e,'id':'kb:'+str(e['id']),'authority':'commercial_rule_only' if technical['identity_confirmed'] else 'owner_note'} for e in entries[:50]]
    examples=[x[1] for x in candidates[:limit]]
    budget=18000
    bounded=[]
    for e in evidence:
        size=len(json.dumps(e,ensure_ascii=False))
        if size<=budget: bounded.append(e);budget-=size
    evidence=bounded
    # Alternatives are catalog products, never an implicit identity or live offer.
    catalog=load()
    alternatives=[{'id':'catalog:'+v['id'],'authority':'owner_catalog_alternative',
                   'source':catalog['source'],
                   'value':{**{k:value for k,value in family.items() if k!='variants'},**v,'family':family['id']}}
                  for family in catalog['families'] for v in family['variants']]
    from assistant_reasoning import reasoning_context
    reasoning=reasoning_context(question, alternatives, technical)
    bounded=[]
    for e in examples:
        size=len(json.dumps(e,ensure_ascii=False))
        if size<=budget:bounded.append(e);budget-=size
    examples=bounded
    from assistant_conversation import conversation
    thread=conversation(store,qid)
    return {'conversation':thread,'publication':{'item_id':item_id,'title':current['title'] if current else ''},
            'knowledge':evidence,'catalog_alternatives':alternatives,'reasoning':reasoning,'examples':examples,'history_scanned':len(rows),'catalog':technical,
            'warnings':technical['warnings']}


def metrics(store):
    with store.connect() as con:
        total=con.execute('SELECT count(*) FROM questions').fetchone()[0]
        attempts=dict(con.execute('''SELECT count(*) attempts, sum(state='GENERATED') generated,
          sum(state='FALLBACK') errors, avg(CASE WHEN state='GENERATED' THEN duration END) seconds,
          sum(cost_usd) estimated_usd, sum(cost_usd IS NULL) unpriced FROM assistant_attempts''').fetchone())
        latest=con.execute('''SELECT d.*,q.original FROM decisions d JOIN questions q ON q.id=d.question_id
          WHERE d.id=(SELECT max(id) FROM decisions WHERE question_id=d.question_id)''').fetchall()
        month=con.execute("SELECT sum(cost_usd) FROM assistant_attempts WHERE substr(started,1,7)=strftime('%Y-%m','now')").fetchone()[0]
        manual=con.execute("SELECT count(*) FROM assistant_response_observations WHERE origin='OUTSIDE_ASSISTANT'").fetchone()[0]
        recovered=con.execute("SELECT count(DISTINCT question_id) FROM assistant_attempts a WHERE state='GENERATED' AND EXISTS(SELECT 1 FROM assistant_attempts b WHERE b.question_id=a.question_id AND b.state='FALLBACK')").fetchone()[0]
        generated_questions=con.execute("SELECT count(DISTINCT question_id) FROM assistant_attempts WHERE state='GENERATED'").fetchone()[0]
    decisions=[r for r in latest if r['action'] in ('APROBADA','EDITADA','RECHAZADA')]
    denominator=len(decisions)
    return {'questions':total,'manual_observations':manual,**attempts,'coverage':round(100*generated_questions/max(total,1),1),
            'reviewed':denominator,'approved_unchanged':sum(r['action']=='APROBADA' and r['after_text']==r['original'] for r in decisions),
            'edited':sum(r['action']=='EDITADA' or (r['action']=='APROBADA' and r['after_text']!=r['original']) for r in decisions),
            'rejected':sum(r['action']=='RECHAZADA' for r in decisions),'month_usd':month,'recovered':recovered}


def audit(store):
    """Read all questions and decisions, flag missing attributes and candidate conflicts."""
    groups={}
    with store.connect() as con:
        rows=con.execute('SELECT * FROM questions').fetchall()
        entries=[dict(r) for r in con.execute('SELECT * FROM assistant_knowledge WHERE active=1')]
    categories={'espesor':r'micron','alimentos_freezer':r'alimento|freez|carne|comida',
                'carga':r'peso|kilo|\bkg\b|resist','personalizados':r'encargo|mínim|minim|personaliz|logo|impre',
                'facturacion':r'factura','precios':r'precio|descuento|promo'}
    counts={key:0 for key in categories}
    for r in rows:
        answer=r['edited'] or r['approved'] or r['external_answer'] or ''
        text=r['question']+' '+answer
        for key,pattern in categories.items():counts[key]+=int(bool(re.search(pattern,text,re.I)))
        group=groups.setdefault(r['item_id'],{'item_id':r['item_id'],'title':r['title'],'questions':0,'attributes':json.loads(r['facts']),'thickness':set(),'sources':[]})
        group['questions']+=1
        group['thickness'].update(re.findall(r'(\d+(?:[.,]\d+)?)\s*micron',answer,re.I))
        if answer:group['sources'].append(r['id'])
    gaps=[]
    for group in groups.values():
        notes=[]
        if not group['attributes']:notes.append('Sin atributos públicos recuperados')
        if len(group['thickness'])>1:notes.append('Varios micrajes históricos: puede ser comparación o contradicción; revisar contexto')
        if not any(e['scope']==group['item_id'] for e in entries):notes.append('Sin conocimiento validado para esta publicación')
        if notes:gaps.append({**group,'thickness':sorted(group['thickness']),'notes':notes})
    return {'questions':len(rows),'products':len(groups),'categories':counts,'gaps':gaps}
