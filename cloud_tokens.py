"""Tokens rotativos cifrados en el volumen; la clave sólo vive en Variables."""
import json
import os
import tempfile
from runtime_config import data_dir

def cipher():
    from cryptography.fernet import Fernet
    return Fernet(os.environ['MELI_TOKEN_KEY'].encode('ascii'))

def read_tokens():
    path = data_dir()/'meli_tokens.enc'
    if not path.exists(): return {}
    return json.loads(cipher().decrypt(path.read_bytes()))

def write_tokens(tokens):
    directory = data_dir(); directory.mkdir(parents=True, exist_ok=True)
    encrypted = cipher().encrypt(json.dumps(tokens).encode())
    fd, temporary = tempfile.mkstemp(prefix='.meli-',dir=directory)
    try:
        with os.fdopen(fd,'wb') as stream:
            stream.write(encrypted); stream.flush(); os.fsync(stream.fileno())
        os.replace(temporary,directory/'meli_tokens.enc')
    finally:
        if os.path.exists(temporary): os.unlink(temporary)
