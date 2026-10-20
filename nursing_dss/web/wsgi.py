"""WSGI entry point (no web framework needed).

* On Vercel:  api/index.py exposes `app`; vercel.json rewrites /api/* to it and
              Vercel serves public/ itself.
* Locally:    `python serve.py` also serves public/ from this same app.
"""
import json
import mimetypes
import os
from typing import Optional

from ..bootstrap import create_system
from ..routing import RoutingSystem
from .api import USER_ERRORS, Api

PUBLIC_DIR = os.path.join(os.path.dirname(__file__), "..", "..", "public")
MAX_BODY = 1_000_000


def create_app(system: Optional[RoutingSystem] = None, serve_static: bool = False):
    holder = {"api": Api(system) if system else None}

    def get_routes():
        if holder["api"] is None:  # lazy: open/seed the DB on the first request, not at import
            holder["api"] = Api(create_system())
        return holder["api"].routes()

    def respond(start_response, code: int, body: dict):
        data = json.dumps(body).encode()
        start_response(f"{code} {'OK' if code < 400 else 'Error'}", [
            ("Content-Type", "application/json"), ("Content-Length", str(len(data))),
            ("Cache-Control", "no-store")])
        return [data]

    def static(start_response, path: str):
        rel = "index.html" if path in ("", "/") else path.lstrip("/")
        full = os.path.abspath(os.path.join(PUBLIC_DIR, rel))
        if not full.startswith(os.path.abspath(PUBLIC_DIR)) or not os.path.isfile(full):
            return respond(start_response, 404, {"error": "not found"})
        with open(full, "rb") as fh:
            data = fh.read()
        ctype = mimetypes.guess_type(full)[0] or "application/octet-stream"
        start_response("200 OK", [("Content-Type", ctype), ("Content-Length", str(len(data)))])
        return [data]

    def app(environ, start_response):
        method, path = environ["REQUEST_METHOD"], environ.get("PATH_INFO", "")
        if not path.startswith("/api/"):
            if serve_static and method == "GET":
                return static(start_response, path)
            return respond(start_response, 404, {"error": "not found"})
        try:
            handler = get_routes().get((method, path.rstrip("/")))
            if handler is None:
                return respond(start_response, 404, {"error": f"no route {method} {path}"})
            payload = {}
            if method == "GET":
                from urllib.parse import parse_qsl
                payload = dict(parse_qsl(environ.get("QUERY_STRING", "")))
            else:
                length = int(environ.get("CONTENT_LENGTH") or 0)
                if length > MAX_BODY:
                    return respond(start_response, 413, {"error": "request too large"})
                raw = environ["wsgi.input"].read(length) if length else b""
                payload = json.loads(raw) if raw else {}
                if not isinstance(payload, dict):
                    raise ValueError("JSON body must be an object.")
            return respond(start_response, 200, handler(payload))
        except json.JSONDecodeError:
            return respond(start_response, 400, {"error": "Request body is not valid JSON."})
        except USER_ERRORS as exc:
            msg = f"Missing field: {exc}" if isinstance(exc, KeyError) else str(exc)
            return respond(start_response, 400, {"error": msg})
        except Exception:  # never leak internals
            return respond(start_response, 500, {"error": "Internal server error."})

    return app
