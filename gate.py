"""A gate in front of Ollama, so it can be published through a Cloudflare tunnel without publishing Ollama.
Only what local.py uses gets through - GET /v1/models and POST /v1/chat/completions - only with the key, and only
for weatherboy's model. Pulling, deleting or loading other models, and Ollama's own API, never reach it.

    python gate.py      127.0.0.1:11435 -> Ollama on 127.0.0.1:11434. Needs WEATHERBOY_LOCAL_KEY in .env;
                        without one it turns everything away.

The client side is local.py with WEATHERBOY_LOCAL_URLS=https://<the tunnel's hostname>/v1 and the same key."""
import envfile  # noqa: F401 - .env loaded before the settings below are read
import hmac
import json
import os
import urllib.error
import urllib.request
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

KEY = os.environ.get("WEATHERBOY_LOCAL_KEY", "")
MODELS = {m.strip() for m in os.environ.get("WEATHERBOY_LOCAL_MODEL", "qwen3:8b").split(",")}
OLLAMA = os.environ.get("WEATHERBOY_GATE_OLLAMA", "http://127.0.0.1:11434")
PORT = int(os.environ.get("WEATHERBOY_GATE_PORT", "11435"))
ALLOWED = {("GET", "/v1/models"), ("POST", "/v1/chat/completions")}
MAX_BODY = 2_000_000  # a long conversation with tool results; far more is someone else's idea


class Gate(BaseHTTPRequestHandler):
    def reply(self, code, body, ctype="application/json"):
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def refuse(self, code, why):
        self.reply(code, json.dumps({"error": why}).encode())

    def handle_one(self, method):
        path = self.path.split("?")[0]
        if (method, path) not in ALLOWED:
            return self.refuse(404, "not here")
        given = self.headers.get("Authorization", "").encode()
        if not KEY or not hmac.compare_digest(given, f"Bearer {KEY}".encode()):
            return self.refuse(401, "wrong or missing key")
        n = int(self.headers.get("Content-Length") or 0)
        if n > MAX_BODY:
            return self.refuse(413, "too large")
        body = self.rfile.read(n) if n else None
        if method == "POST":
            try:
                d = json.loads(body or b"{}")
            except ValueError:
                return self.refuse(400, "not JSON")
            if d.get("model") not in MODELS:
                return self.refuse(403, "only " + ", ".join(sorted(MODELS)))
            d["stream"] = False  # one answer per request: the tunnel and local.py both want that
            body = json.dumps(d).encode()
        req = urllib.request.Request(OLLAMA + path, data=body, method=method,
                                     headers={"Content-Type": "application/json"})
        try:
            with urllib.request.urlopen(req, timeout=600) as r:
                self.reply(r.status, r.read(), r.headers.get("Content-Type", "application/json"))
        except urllib.error.HTTPError as e:
            self.reply(e.code, e.read(), e.headers.get("Content-Type", "application/json"))
        except OSError:
            self.refuse(502, "Ollama is not running")

    def do_GET(self):
        self.handle_one("GET")

    def do_POST(self):
        self.handle_one("POST")

    def do_PUT(self):
        self.refuse(405, "no")

    do_DELETE = do_PATCH = do_HEAD = do_PUT

    def log_message(self, *args):  # quiet: the requests are someone's conversation
        pass


if __name__ == "__main__":
    if not KEY:
        print("gate: no WEATHERBOY_LOCAL_KEY in .env - every request will be turned away")
    print(f"gate: 127.0.0.1:{PORT} -> {OLLAMA}, models {', '.join(sorted(MODELS))}")
    ThreadingHTTPServer(("127.0.0.1", PORT), Gate).serve_forever()
