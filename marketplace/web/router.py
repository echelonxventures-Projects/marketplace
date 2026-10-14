"""A tiny path/method router plus a domain-object serializer.

No third-party dependency. Paths match literal segments and ``{param}``
placeholders; handlers receive a ``Request`` and return a ``Response``.
"""

from __future__ import annotations

import json
from dataclasses import asdict, dataclass, field, is_dataclass
from datetime import date, datetime
from decimal import Decimal
from enum import Enum
from typing import Any, Callable, Optional
from urllib.parse import parse_qs

from marketplace.kernel import Money


@dataclass
class Request:
    """One inbound HTTP request, already parsed."""

    method: str
    path: str
    params: dict[str, str] = field(default_factory=dict)
    query: dict[str, Any] = field(default_factory=dict)
    body: Any = None
    headers: dict[str, str] = field(default_factory=dict)
    token: Optional[str] = None
    authz: Any = None  # AuthorizationContext once resolved


@dataclass
class Response:
    status: int = 200
    payload: Any = None
    headers: dict[str, str] = field(default_factory=dict)

    @staticmethod
    def ok(payload: Any = None, **extra: Any) -> "Response":
        return Response(200, _merge(payload, extra))

    @staticmethod
    def created(payload: Any = None, **extra: Any) -> "Response":
        return Response(201, _merge(payload, extra))

    @staticmethod
    def html(document: str) -> "Response":
        return Response(200, document, headers={"Content-Type": "text/html; charset=utf-8"})


def _merge(payload: Any, extra: dict[str, Any]) -> Any:
    if not extra:
        return payload
    if isinstance(payload, dict):
        return {**payload, **extra}
    return {"result": payload, **extra}


Handler = Callable[[Request], Response]


class Router:
    """Registers (method, pattern) -> handler and resolves incoming requests."""

    def __init__(self) -> None:
        self._routes: list[tuple[str, list[str], Handler]] = []

    def add(self, method: str, pattern: str, handler: Handler) -> None:
        self._routes.append((method.upper(), pattern.strip("/").split("/"), handler))

    def resolve(self, method: str, path: str) -> Optional[tuple[Handler, dict[str, str]]]:
        segments = path.strip("/").split("/")
        for route_method, pattern, handler in self._routes:
            if route_method != method.upper():
                continue
            if len(pattern) != len(segments):
                continue
            params = _match(pattern, segments)
            if params is not None:
                return handler, params
        return None

    def routes(self) -> list[tuple[str, str]]:
        return [(m, "/" + "/".join(p)) for m, p, _ in self._routes]

    # -- convenience verbs ------------------------------------------------

    def get(self, pattern: str) -> Callable[[Handler], Handler]:
        return self._decorator("GET", pattern)

    def post(self, pattern: str) -> Callable[[Handler], Handler]:
        return self._decorator("POST", pattern)

    def put(self, pattern: str) -> Callable[[Handler], Handler]:
        return self._decorator("PUT", pattern)

    def delete(self, pattern: str) -> Callable[[Handler], Handler]:
        return self._decorator("DELETE", pattern)

    def _decorator(self, method: str, pattern: str) -> Callable[[Handler], Handler]:
        def register(handler: Handler) -> Handler:
            self.add(method, pattern, handler)
            return handler

        return register


def _match(pattern: list[str], segments: list[str]) -> Optional[dict[str, str]]:
    params: dict[str, str] = {}
    for p, s in zip(pattern, segments):
        if p.startswith("{") and p.endswith("}") and len(p) > 2:
            params[p[1:-1]] = s
        elif p != s:
            return None
    return params


def parse_query(query_string: str) -> dict[str, Any]:
    """Parse a query string, single-valued (last wins) with list hints for `ids`."""
    if not query_string:
        return {}
    parsed = parse_qs(query_string, keep_blank_values=True)
    out: dict[str, Any] = {}
    for key, values in parsed.items():
        if key.endswith("[]"):
            out[key[:-2]] = values
        else:
            out[key] = values[-1]
    return out


def serialize(value: Any) -> Any:
    """Convert domain objects to JSON-safe structures.

    Handles dataclasses, ``Money``, ``Decimal``, enums, dates, sets/tuples and
    nested containers. Unknown objects fall back to ``str`` so the API never
    crashes on an unfamiliar type.
    """

    if value is None or isinstance(value, (bool, int, float, str)):
        return value
    if isinstance(value, Money):
        return value.to_dict()
    if isinstance(value, Decimal):
        return str(value)
    if isinstance(value, Enum):
        return value.value
    if isinstance(value, (datetime, date)):
        return value.isoformat()
    if isinstance(value, (frozenset, set)):
        return sorted(serialize(v) for v in value)  # type: ignore[type-var]
    if isinstance(value, (list, tuple)):
        return [serialize(v) for v in value]
    if isinstance(value, dict):
        return {str(k): serialize(v) for k, v in value.items()}
    if is_dataclass(value) and not isinstance(value, type):
        return {k: serialize(v) for k, v in asdict(value).items()}  # type: ignore[call-overload]
    if hasattr(value, "to_dict") and callable(value.to_dict):
        try:
            return serialize(value.to_dict())
        except TypeError:
            pass
    return str(value)


def to_json(response: Response) -> bytes:
    payload = response.payload
    if payload is None:
        return b""
    if isinstance(payload, (bytes, str)):
        data = payload
    else:
        data = json.dumps(serialize(payload), indent=2, default=str).encode()
    return data if isinstance(data, bytes) else data.encode()
