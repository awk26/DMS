"""Application entry point.

    python app.py            # run the web application
    flask --app app run      # alternative

Set DMS_SSL_CERT_FILE and DMS_SSL_KEY_FILE to serve HTTPS directly (TLS 1.2 or newer only).
In production, run behind a TLS-terminating reverse proxy or a WSGI server such as waitress.
"""
import os
import ssl

from werkzeug.serving import WSGIRequestHandler

from dms import create_app, ensure_maintenance, ensure_scheduler, ensure_validity_worker

app = create_app()


class RequestHandler(WSGIRequestHandler):
    """Sends a generic "Server" header (config SERVER_HEADER) instead of the Werkzeug and Python
    versions, which a security scan reports as information disclosure."""

    def version_string(self):
        return app.config.get("SERVER_HEADER") or "DMS"


def tls_context():
    cert, key = app.config.get("SSL_CERT_FILE"), app.config.get("SSL_KEY_FILE")
    if not (cert and key):
        return None
    ctx = ssl.create_default_context(ssl.Purpose.CLIENT_AUTH)
    ctx.minimum_version = ssl.TLSVersion.TLSv1_2
    ctx.load_cert_chain(str(cert), str(key))
    return ctx


if __name__ == "__main__":
    ensure_scheduler(app)  # start Gmail synchronization immediately, not on first request
    ensure_maintenance(app)
    ensure_validity_worker(app)  # validity analysis jobs + daily expiry run (VALIDITY_WORKER_ENABLED)
    app.run(host=os.environ.get("DMS_HOST", "127.0.0.1"), port=int(os.environ.get("DMS_PORT", "5000")),
            debug=False, threaded=True, ssl_context=tls_context(), request_handler=RequestHandler)
