"""Development server: serves ./web and proxies /api/* to the API (default http://localhost:8000).

    python web/devserver.py            -> http://localhost:8080

In production this is Nginx's job (static files plus a reverse proxy for /api).
"""

import http.server
import os
import urllib.error
import urllib.request

API_URL = os.environ.get("API_URL", "http://localhost:8000").rstrip("/")
if not API_URL.startswith(("http://", "https://")):
    raise SystemExit("API_URL must start with http:// or https://")
PORT = int(os.environ.get("WEB_PORT", "8080"))
ROOT = os.path.dirname(os.path.abspath(__file__))


class Handler(http.server.SimpleHTTPRequestHandler):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, directory=ROOT, **kwargs)

    def _proxy(self) -> None:
        length = int(self.headers.get("Content-Length", 0))
        body = self.rfile.read(length) if length else None
        req = urllib.request.Request(API_URL + self.path[len("/api") :], data=body, method=self.command)  # noqa: S310
        for h in ("Content-Type", "X-Request-ID"):
            if self.headers.get(h):
                req.add_header(h, self.headers[h])
        try:
            with urllib.request.urlopen(req, timeout=10) as res:  # noqa: S310
                payload, code, ctype = res.read(), res.status, res.headers.get("Content-Type", "application/json")
        except urllib.error.HTTPError as err:
            payload, code, ctype = err.read(), err.code, err.headers.get("Content-Type", "application/json")
        except OSError:
            payload, code, ctype = b'{"detail":"API unreachable"}', 502, "application/json"
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(payload)))
        self.end_headers()
        self.wfile.write(payload)

    def do_GET(self):  # noqa: N802
        self._proxy() if self.path.startswith("/api/") else super().do_GET()

    def do_POST(self):  # noqa: N802
        self._proxy() if self.path.startswith("/api/") else self.send_error(405)


if __name__ == "__main__":
    print(f"Pollie web on http://localhost:{PORT} (proxying /api to {API_URL})")
    http.server.ThreadingHTTPServer(("0.0.0.0", PORT), Handler).serve_forever()  # noqa: S104
