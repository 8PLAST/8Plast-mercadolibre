"""Read-only historical replay. Generates no customer replies or database writes.

python assistant_evaluate.py --db /copy/assistant_reviews.db --limit 10
Add --live only with an API key supplied securely through the environment.
Review output locally: it contains business question/answer text. Never commit it.
"""
import argparse
import json
import sqlite3
from pathlib import Path
from assistant_knowledge import retrieve
from assistant_automation import ai_draft, ServiceFailure


class Snapshot:
    def __init__(self,con):self.con=con
    def connect(self):
        from contextlib import nullcontext
        return nullcontext(self.con)


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--db',required=True)
    parser.add_argument('--limit',type=int,default=10)
    parser.add_argument('--live',action='store_true')
    args=parser.parse_args()
    # SQLite backup to memory, source opened read-only. Migrations only affect memory.
    source=sqlite3.connect('file:'+Path(args.db).resolve().as_posix()+'?mode=ro',uri=True)
    con=sqlite3.connect(':memory:');source.backup(con);source.close();con.row_factory=sqlite3.Row
    store=Snapshot(con)
    from assistant_knowledge import initialize
    initialize(store)
    for name in ('buyer_id','external_answer_created','external_created'):
        if name not in {r[1] for r in con.execute('PRAGMA table_info(questions)')}:
            con.execute(f'ALTER TABLE questions ADD COLUMN {name} TEXT')
    con.commit()
    rows=con.execute("SELECT * FROM questions WHERE external_answer IS NOT NULL AND trim(external_answer)<>'' ORDER BY updated DESC LIMIT ?",(max(1,min(100,args.limit)),)).fetchall()
    for q in rows:
        isolated=sqlite3.connect(':memory:');con.backup(isolated);isolated.row_factory=sqlite3.Row
        isolated_store=Snapshot(isolated)
        from assistant_conversation import blind_snapshot
        try:
            blind_snapshot(isolated_store,q['id'])
        except ValueError as exc:
            print(json.dumps({'question_id':q['id'],'skipped':str(exc)},ensure_ascii=False))
            isolated.close()
            continue
        context=retrieve(isolated_store,q['id'],q['item_id'],q['question'])
        result={'question_id':q['id'],'item_id':q['item_id'],'question':q['question'],
                'published_answer':q['external_answer'],'context':context,'live':args.live}
        if args.live:
            try:
                facts=json.loads(q['facts'])
                if context.get('catalog',{}).get('identity_confirmed'):
                    facts={k:v for k,v in facts.items() if k in ('unidades_pack','presentacion')}
                answer,used,warnings,meta=ai_draft(q['question'],facts,context)
                result.update(suggested=answer,warnings=warnings,meta=meta)
            except ServiceFailure as exc:result['error']=exc.code
        else:result['note']='Solo recuperación. No es una respuesta generada por IA.'
        print(json.dumps(result,ensure_ascii=False))
        isolated.close()
    con.close()


if __name__=='__main__':main()
