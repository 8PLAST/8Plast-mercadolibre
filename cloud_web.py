"""Servidor HTTP de producción: PORT se lee en Python, sin expansión de shell."""
import os


def listen_port():
    raw = os.environ.get('PORT', '8000').strip()
    if not raw.isascii() or not raw.isdecimal() or not 1 <= int(raw) <= 65535:
        raise RuntimeError('PORT debe ser un número entre 1 y 65535; eliminá valores literales como $PORT en Railway.')
    return int(raw)


def main():
    from waitress import serve
    from cloud_app import create_app
    serve(create_app(), host='0.0.0.0', port=listen_port())


if __name__ == '__main__':
    main()
