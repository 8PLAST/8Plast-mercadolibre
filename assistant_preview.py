"""Local review preview; bind to loopback only, no operational database access."""
import secrets
from pathlib import Path
from flask import Flask, abort, request, session
from assistant_center import register_center


def create_app(review_path=None):
    app = Flask(__name__, template_folder='readonly_portal/templates', static_folder='readonly_portal/static')
    app.secret_key = secrets.token_hex(32)
    app.config.update(MAX_CONTENT_LENGTH=2_100_000, SESSION_COOKIE_HTTPONLY=True, SESSION_COOKIE_SAMESITE='Strict')

    @app.before_request
    def protect():
        session.setdefault('csrf', secrets.token_urlsafe(32))
        if request.method == 'POST' and not secrets.compare_digest(request.form.get('csrf',''), session['csrf']):
            abort(403)

    @app.context_processor
    def common(): return {'csrf': session['csrf']}

    @app.get('/')
    def home():
        from flask import redirect
        return redirect('/asistentes')

    register_center(app, review_path or Path(__file__).parent / 'outputs' / 'assistant_reviews_preview.db')
    return app


if __name__ == '__main__':
    create_app().run(host='127.0.0.1', port=5081, debug=False)
