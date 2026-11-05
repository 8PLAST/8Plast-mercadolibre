"""Only connect turns with exact buyer/product identity and verified chronology."""
from datetime import datetime, timezone


def instant(value):
    try:
        result = datetime.fromisoformat((value or '').replace('Z', '+00:00'))
        return result.astimezone(timezone.utc) if result.tzinfo else None
    except (ValueError, TypeError, AttributeError):
        return None


def conversation(store, qid):
    with store.connect() as con:
        current = con.execute('SELECT * FROM questions WHERE id=?', (qid,)).fetchone()
        if not current or not current['buyer_id'] or not instant(current['external_created']):
            return []
        rows = con.execute('SELECT * FROM questions WHERE buyer_id=? AND item_id=? AND id<>?',
                           (current['buyer_id'], current['item_id'], qid)).fetchall()
    cutoff = instant(current['external_created'])
    prior = [r for r in rows if instant(r['external_created']) and
             instant(r['external_created']) < cutoff and instant(r['external_answer_created']) and
             instant(r['external_answer_created']) <= cutoff and r['external_answer']]
    prior.sort(key=lambda r: instant(r['external_created']))
    return [{'id':'conversation:'+r['id'], 'authority':'same_buyer_same_publication',
             'question':r['question'][:1200], 'answer':r['external_answer'][:2000],
             'answered_at':r['external_answer_created']} for r in prior[-6:]]


def blind_snapshot(store, qid):
    """Mask target/future answers in an evaluation COPY, never the live database.

    Uses the current confirmed catalog, but only historically available answers.
    Rows without a reliable answer timestamp are excluded conservatively.
    """
    with store.connect() as con:
        target = con.execute('SELECT external_created FROM questions WHERE id=?', (qid,)).fetchone()
        cutoff = instant(target['external_created']) if target else None
        if cutoff is None:
            raise ValueError('Evaluación sin fecha verificable: no puede garantizarse aislamiento temporal')
        rows = con.execute('SELECT id,external_answer_created FROM questions').fetchall()
        for row in rows:
            at = instant(row['external_answer_created'])
            if row['id']==qid or at is None or at>cutoff:
                con.execute("UPDATE questions SET external_answer='',edited=NULL,approved=NULL WHERE id=?", (row['id'],))
                for table in ('assistant_answer_history','assistant_response_observations','decisions'):
                    con.execute('DELETE FROM '+table+' WHERE question_id=?', (row['id'],))
        for table, field in (('decisions','created'),('assistant_answer_history','recorded'),
                             ('assistant_response_observations','observed_at')):
            for row in con.execute('SELECT id,'+field+' FROM '+table).fetchall():
                at = instant(row[field])
                if at is None or at>cutoff:
                    con.execute('DELETE FROM '+table+' WHERE id=?', (row['id'],))
