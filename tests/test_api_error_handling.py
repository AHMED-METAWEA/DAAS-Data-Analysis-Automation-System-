"""Guards the "Failed to fetch" class of bug.

A browser only shows a useful message if the error response carries CORS
headers. Starlette's built-in ServerErrorMiddleware sits *outside*
CORSMiddleware, so before ``backend.app.main.unhandled_errors_as_json`` existed
any unhandled exception produced a bare, header-less 500 that the browser
rejected outright — `fetch` then failed with `TypeError: Failed to fetch` and
the real cause was visible only in the server log.
"""

from __future__ import annotations

from fastapi.testclient import TestClient

from backend.app.main import app

ORIGIN = "http://localhost:3000"


def _client() -> TestClient:
    # raise_server_exceptions=False so the middleware's response is returned
    # instead of the exception being re-raised into the test.
    return TestClient(app, raise_server_exceptions=False)


def test_unhandled_exception_returns_json_detail_with_cors_headers() -> None:
    @app.get("/__boom_json")
    def _boom() -> None:
        raise RuntimeError("kaboom in the save path")

    res = _client().get("/__boom_json", headers={"Origin": ORIGIN})

    assert res.status_code == 500
    # The actual cause must reach the client, not an opaque body.
    assert "kaboom in the save path" in res.json()["detail"]
    # ...and it must be readable by the browser, which is what CORS decides.
    assert res.headers["access-control-allow-origin"] == ORIGIN


def test_http_exception_still_carries_cors_headers() -> None:
    from fastapi import HTTPException

    @app.get("/__boom_http")
    def _boom_http() -> None:
        raise HTTPException(status_code=400, detail="bad data")

    res = _client().get("/__boom_http", headers={"Origin": ORIGIN})

    assert res.status_code == 400
    assert res.json()["detail"] == "bad data"
    assert res.headers["access-control-allow-origin"] == ORIGIN


def test_success_response_carries_cors_headers() -> None:
    res = _client().get("/health", headers={"Origin": ORIGIN})
    assert res.status_code == 200
    assert res.headers["access-control-allow-origin"] == ORIGIN


def test_loopback_origin_on_any_port_is_allowed() -> None:
    """The same UI reached as 127.0.0.1 rather than localhost is not a
    different application, but a strict single-origin policy blocked every
    request from it — which surfaces as "Failed to fetch" too."""
    for origin in ("http://127.0.0.1:3000", "http://localhost:3001"):
        res = _client().get("/health", headers={"Origin": origin})
        assert res.headers.get("access-control-allow-origin") == origin, origin


def test_preflight_for_a_real_api_route_is_allowed() -> None:
    res = _client().options(
        "/api/v1/projects",
        headers={
            "Origin": ORIGIN,
            "Access-Control-Request-Method": "POST",
            "Access-Control-Request-Headers": "authorization,content-type",
        },
    )
    assert res.status_code == 200
    assert res.headers["access-control-allow-origin"] == ORIGIN
