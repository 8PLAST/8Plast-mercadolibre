from __future__ import annotations

import logging
import os

from flask import Flask, jsonify, request

try:
    from .storage import store_delivery
except ImportError:
    from storage import store_delivery


def create_app(test_config=None):
    app = Flask(__name__)
    if test_config: app.config.update(test_config)

    @app.get("/health")
    def health():
        return jsonify(status="ok"), 200

    @app.post("/webhook/mercadolibre")
    def mercadolibre_webhook():
        # Persistir es deliberadamente el único trabajo sincrónico: es pequeño, transaccional
        # y garantiza que el evento no se pierda antes de responder a MercadoLibre.
        payload = request.get_json(silent=True)
        if not isinstance(payload, dict):
            payload = {"_invalid_payload": request.get_data(as_text=True)[:10000]}
        try:
            result = store_delivery(payload)
            return jsonify(status="received", duplicate=result["duplicate"], complete=result["complete"]), 200
        except Exception:
            app.logger.exception("No se pudo persistir una notificación de MercadoLibre")
            # Un fallo de persistencia debe permitir que MercadoLibre reintente.
            return jsonify(status="temporary_error"), 503

    return app


app = create_app()


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    app.run(host="0.0.0.0", port=int(os.environ.get("PORT", "8000")), debug=False)
