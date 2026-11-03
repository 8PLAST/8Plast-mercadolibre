"""Consistent SQLite backup/verification only. Never performs a production restore.

python assistant_backup.py --source /data/assistant_reviews.db --destination /secure/review-backup.db
Use an authorized environment; contains private business data. No credential output.
"""
import argparse
import hashlib
import json
import os
import sqlite3
from pathlib import Path


def backup(source,destination):
    source=Path(source).resolve();destination=Path(destination).resolve()
    if source==destination or destination.exists():raise ValueError('El destino debe ser nuevo y distinto del origen')
    destination.parent.mkdir(parents=True,exist_ok=True)
    # Create private exclusive file before SQLite opens it; never overwrite a prior backup.
    fd=os.open(destination,os.O_WRONLY|os.O_CREAT|os.O_EXCL,0o600);os.close(fd)
    try:
        original=sqlite3.connect(source.as_uri()+'?mode=ro',uri=True)
        target=sqlite3.connect(destination)
        try:
            original.backup(target,pages=128)
            result=target.execute('PRAGMA integrity_check').fetchone()[0]
            if result!='ok':raise ValueError('La copia no superó integrity_check')
        finally:target.close();original.close()
        digest=hashlib.sha256(destination.read_bytes()).hexdigest()
        manifest=destination.with_suffix(destination.suffix+'.manifest.json')
        fd=os.open(manifest,os.O_WRONLY|os.O_CREAT|os.O_EXCL,0o600)
        with os.fdopen(fd,'w') as f:json.dump({'sha256':digest,'integrity':'ok','filename':destination.name},f)
        return {'integrity':'ok','sha256':digest}
    except Exception:
        # Preserve failed copy for investigation; don't destroy data automatically.
        raise


if __name__=='__main__':
    p=argparse.ArgumentParser(description=__doc__);p.add_argument('--source',required=True);p.add_argument('--destination',required=True)
    args=p.parse_args();print(json.dumps(backup(args.source,args.destination)))
