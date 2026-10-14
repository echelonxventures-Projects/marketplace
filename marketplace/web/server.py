"""The stdlib HTTP server.

Uses ``http.server.ThreadingHTTPServer`` so no third-party dependency is added.
Every request is routed through ``api.router``; domain errors are mapped to
their canonical HTTP status (each ``MarketplaceError`` carries ``http_status``).
"""

from __future__ import annotations

import json
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Any, Optional

from marketplace.kernel.errors import MarketplaceError
from marketplace.kernel.registry import CONCEPTS

from . import api
from .router import Request, Response, Router, parse_query, to_json
from .runtime import Runtime, build_runtime

DEFAULT_HOST = "127.0.0.1"
DEFAULT_PORT = 8000


class MarketplaceHTTPRequestHandler(BaseHTTPRequestHandler):
    server_version = "UCOSMarketplace/1.0"

    # -- plumbing ---------------------------------------------------------

    @property
    def runtime(self) -> Runtime:
        return self.server.runtime  # type: ignore[attr-defined]

    @property
    def router(self) -> Router:
        return self.server.router  # type: ignore[attr-defined]

    def log_message(self, format: str, *args: Any) -> None:
        # Keep the default access log; it is useful for local testing.
        super().log_message(format, *args)

    def _send(self, response: Response) -> None:
        body = to_json(response)
        self.send_response(response.status)
        # A handler may set its own Content-Type (e.g. text/html); only default
        # to JSON when it did not, so HTML pages are served correctly.
        if body and not response.headers.get("Content-Type"):
            self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        for key, value in response.headers.items():
            self.send_header(key, value)
        self.end_headers()
        if body:
            self.wfile.write(body)

    def _send_error(self, status: int, payload: dict) -> None:
        body = json.dumps(payload, indent=2, default=str).encode()
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def _bearer(self) -> Optional[str]:
        header = self.headers.get("Authorization", "")
        if header.startswith("Bearer "):
            return header[len("Bearer "):].strip()
        return self.headers.get("X-API-Key")

    def _read_body(self) -> Any:
        length = int(self.headers.get("Content-Length") or 0)
        if length == 0:
            return None
        raw = self.rfile.read(length)
        if not raw:
            return None
        try:
            return json.loads(raw)
        except json.JSONDecodeError as exc:
            from marketplace.kernel.errors import ValidationError

            raise ValidationError(f"Malformed JSON body: {exc}")

    def _dispatch(self, method: str) -> None:
        parsed = self.path.split("?", 1)
        path, query_string = parsed[0], parsed[1] if len(parsed) > 1 else ""
        resolved = self.router.resolve(method, path)
        if resolved is None:
            self._send_error(
                404,
                {
                    "code": "NOT_FOUND",
                    "message": f"No route for {method} {path}",
                    "details": {"method": method, "path": path},
                    "retryable": False,
                },
            )
            return
        handler, params = resolved
        try:
            body = self._read_body() if method in {"POST", "PUT", "PATCH"} else None
        except MarketplaceError as exc:
            self._send_error(exc.http_status, exc.to_contract())
            return

        token = self._bearer()
        request = Request(
            method=method,
            path=path,
            params=params,
            query=parse_query(query_string),
            body=body,
            headers={k: v for k, v in self.headers.items()},
            token=token,
        )
        request.runtime = self.runtime  # type: ignore[attr-defined]

        if token:
            try:
                # A friendly demo key maps to a real token; anything else is
                # treated as a raw bearer token and validated by the resolver.
                request.authz = self.runtime.resolve(
                    self.runtime.token_for(token) or token
                )
            except MarketplaceError as exc:
                self._send_error(exc.http_status, exc.to_contract())
                return

        try:
            response = handler(request)
        except MarketplaceError as exc:
            self._send_error(exc.http_status, exc.to_contract())
            return
        except Exception as exc:  # noqa: BLE001 - never leak internals (GOV-10)
            self._send_error(
                500,
                {
                    "code": "INTERNAL_ERROR",
                    "message": "An internal error occurred; details are withheld",
                    "details": {"type": type(exc).__name__},
                    "retryable": False,
                },
            )
            raise
        self._send(response)

    def do_GET(self) -> None:
        self._dispatch("GET")

    def do_POST(self) -> None:
        self._dispatch("POST")

    def do_PUT(self) -> None:
        self._dispatch("PUT")

    def do_DELETE(self) -> None:
        self._dispatch("DELETE")


class MarketplaceHTTPServer(ThreadingHTTPServer):
    daemon_threads = True
    allow_reuse_address = True


def create_server(
    runtime: Optional[Runtime] = None,
    *,
    host: str = DEFAULT_HOST,
    port: int = DEFAULT_PORT,
) -> MarketplaceHTTPServer:
    """Bind the API to a host and port on an already-wired runtime."""

    runtime = runtime if runtime is not None else build_runtime()
    server = MarketplaceHTTPServer((host, port), MarketplaceHTTPRequestHandler)
    server.runtime = runtime  # type: ignore[attr-defined]
    server.router = api.router  # type: ignore[attr-defined]
    return server


def startup_banner(runtime: Runtime, host: str, port: int) -> str:
    base = f"http://{host}:{port}"
    lines = [
        "",
        "=" * 72,
        "UCOS Marketplace — local test server",
        "=" * 72,
        f"  Base URL          {base}",
        f"  OpenAPI           {base}/api/openapi.json",
        f"  Health            {base}/api/health",
        f"  Demo state        {base}/api/demo/state",
        "",
        "  Demo credentials (send as `Authorization: Bearer <api_key>`):",
    ]
    if runtime.seed_report:
        for persona in runtime.seed_report.personas:
            lines.append(
                f"    {persona['key']:<6} key={persona['api_key']:<14} "
                f"login={persona['subject']} / {persona['password']}"
            )
    lines.extend(
        [
            "",
            "  Try it:",
            f"    curl {base}/api/health",
            f"    curl -H 'Authorization: Bearer mk_demo_buyer' {base}/api/catalog/products",
            f"    curl -X POST -H 'Authorization: Bearer mk_demo_buyer' "
            f"-H 'Content-Type: application/json' "
            f"-d '{{\"offer_id\": \"<offer_id>\", \"quantity\": 2}}' {base}/api/checkout",
            "",
            "  In-memory state: restarting the server resets all data and re-seeds.",
            "=" * 72,
            "",
        ]
    )
    return "\n".join(lines)


def run_server(
    *,
    host: str = DEFAULT_HOST,
    port: int = DEFAULT_PORT,
    seed: bool = True,
) -> None:
    """Build the runtime, seed it and serve until interrupted."""

    runtime = build_runtime(seed=seed)
    server = create_server(runtime, host=host, port=port)
    print(startup_banner(runtime, host, port), flush=True)
    if seed and runtime.seed_report:
        first = runtime.seed_report.products[0] if runtime.seed_report.products else None
        if first:
            print(
                f"  Seeded {len(runtime.seed_report.products)} products; "
                f"first offer is {first['offer_id']} at {first['currency']} {first['price']}.",
                flush=True,
            )
    print(f"  Registered concepts: {len(CONCEPTS.all())}", flush=True)
    print(f"Serving on http://{host}:{port} — press Ctrl+C to stop.", flush=True)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\nShutting down.")
    finally:
        server.server_close()
