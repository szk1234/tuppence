"""Every /api route needs a session unless it's on the explicit public list (structural check).

New routers must go into `routes.PROTECTED`; this test turns that convention into a check.
"""

import re

from fastapi.testclient import TestClient
from starlette.routing import Mount

from tuppence.app.deps import require_session

PUBLIC_API = {
    ("/api/auth/session", "GET"),
    ("/api/auth/setup", "POST"),
    ("/api/auth/login", "POST"),
    ("/api/openapi.json", "GET"),
    ("/api/openapi.json", "HEAD"),
}
CATCH_ALL = "/api/{path:path}"  # answers 404 for unknown API paths, never data


def _flat_routes(app):
    """Yield every route, expanding included routers (FastAPI includes them lazily)."""
    for route in app.routes:
        expand = getattr(route, "effective_route_contexts", None)
        if expand is not None:
            yield from expand()
        else:
            yield route


def _api_routes(app):
    for route in _flat_routes(app):
        path = getattr(route, "path", "") or ""
        assert not (isinstance(route, Mount) and path.startswith("/api")), path
        if path == "/api" or path.startswith("/api/"):
            for method in sorted(getattr(route, "methods", None) or {"GET"}):
                yield route, path, method


def _calls(dependant):
    for dep in dependant.dependencies:
        yield dep.call
        yield from _calls(dep)


def test_every_api_route_requires_a_session(make_app):
    app = make_app("server")
    routes = [(r, p, m) for r, p, m in _api_routes(app) if p != CATCH_ALL]
    assert len(routes) > 15  # the walk really saw the routers
    unguarded = [
        f"{m} {p}"
        for r, p, m in routes
        if (p, m) not in PUBLIC_API
        and (getattr(r, "dependant", None) is None or require_session not in _calls(r.dependant))
    ]
    assert unguarded == [], f"routes missing require_session: {unguarded}"


def test_every_protected_api_route_answers_401_without_a_session(make_app):
    c = TestClient(make_app("server"))
    seen = 0
    for _r, path, method in _api_routes(c.app):
        if path == CATCH_ALL or (path, method) in PUBLIC_API:
            continue
        seen += 1
        url = re.sub(r"\{[^}]+\}", "x", path)
        assert c.request(method, url, json={}).status_code == 401, f"{method} {path}"
    assert seen > 15


def test_public_list_matches_real_routes(make_app):
    present = {(p, m) for _r, p, m in _api_routes(make_app("server"))}
    assert present >= PUBLIC_API
