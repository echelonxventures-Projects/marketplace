"""Local test server: exposes the marketplace domain over a stdlib HTTP API.

This is the U15 adapter surface (Spec §00.3 approved exclusion, now filled in as
a local testing adapter). It adds no runtime dependency: it is built on
``http.server`` so the platform's stdlib-only property is preserved.

Everything here is a thin adapter. It resolves an ``AuthorizationContext`` from
a bearer token and calls the canonical universe services; no domain logic lives
in this package.
"""

from .runtime import Runtime, build_runtime
from .server import create_server, run_server

__all__ = [
    "Runtime",
    "build_runtime",
    "create_server",
    "run_server",
]
