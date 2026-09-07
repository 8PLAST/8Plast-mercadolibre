"""Compatibilidad del webhook de Render: no requiere expansión de shell."""
from cloud_web import listen_port

bind = '0.0.0.0:{}'.format(listen_port())
workers = 1
threads = 4
