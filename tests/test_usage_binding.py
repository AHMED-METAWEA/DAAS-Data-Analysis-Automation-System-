"""Usage attribution across FastAPI's threadpool boundary.

This is the load-bearing assumption of the whole metering design, and it is not
self-evident: FastAPI runs ``def`` endpoints in a worker thread, and a ContextVar
set in the wrong place lands on a throwaway copy of the context that the endpoint
never sees. If that were the case here, every agent call would record nothing and
the usage dashboard would show a confident, permanent zero.

So these tests drive the real dependency through a real ASGI app rather than
asserting on the ContextVar directly — the propagation is the thing under test,
not the bookkeeping.
"""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import pytest
from fastapi import Depends, FastAPI
from fastapi.testclient import TestClient

from backend.app.api.deps import bind_usage_context
from backend.app.core.security import create_access_token
from backend.app.services import usage_recorder
from backend.app.services.usage_recorder import attach_project, current_context
from tools.llm_client import complete
from tools.llm_usage import UsageEvent


@pytest.fixture
def recorded(monkeypatch) -> list[tuple[usage_recorder.UsageContext, UsageEvent]]:
    """Intercept persistence so these tests need no database."""
    captured: list[tuple[usage_recorder.UsageContext, UsageEvent]] = []
    monkeypatch.setattr(
        usage_recorder, "persist", lambda ctx, event: captured.append((ctx, event))
    )
    return captured


@pytest.fixture
def groq_only(monkeypatch):
    monkeypatch.setenv("GROQ_API_KEY", "test-key")
    monkeypatch.setenv("LLM_PROVIDER_ORDER", "groq")


def _groq_client(text="hi", prompt=90, completion=10, total=100):
    client = MagicMock()
    client.chat.completions.create.return_value = SimpleNamespace(
        choices=[SimpleNamespace(message=SimpleNamespace(content=text))],
        usage=SimpleNamespace(
            prompt_tokens=prompt, completion_tokens=completion, total_tokens=total
        ),
    )
    return client


def _app() -> FastAPI:
    """A miniature of the real app: same global dependency, sync endpoints."""
    app = FastAPI(dependencies=[Depends(bind_usage_context)])

    # `def`, not `async def` — this is the case that matters. Every LLM-calling
    # endpoint in the product is sync and therefore runs in a worker thread.
    @app.post("/analyze")
    def analyze() -> dict:
        return {"answer": complete("insights", [{"role": "user", "content": "x"}], model="m")}

    @app.post("/projects/{project_id}/analyze")
    def analyze_project(project_id: str) -> dict:
        # Stands in for get_owned_project, which is likewise sync.
        attach_project(project_id)
        return {"answer": complete("insights", [{"role": "user", "content": "x"}], model="m")}

    @app.get("/whoami")
    def whoami() -> dict:
        ctx = current_context()
        return {"user_id": ctx.user_id if ctx else None}

    return app


def _auth(user_id: str = "user-abc") -> dict[str, str]:
    return {"Authorization": f"Bearer {create_access_token(user_id)}"}


class TestRequestBinding:
    def test_sync_endpoint_sees_the_binding_from_the_async_dependency(self, groq_only, recorded) -> None:
        with patch("groq.Groq", return_value=_groq_client()):
            with TestClient(_app()) as client:
                resp = client.post("/analyze", headers=_auth())

        assert resp.status_code == 200
        assert resp.json() == {"answer": "hi"}
        assert len(recorded) == 1, "the sink did not survive the threadpool hop"
        ctx, event = recorded[0]
        assert ctx.user_id == "user-abc"
        assert ctx.source == "user"
        assert (event.purpose, event.total_tokens) == ("insights", 100)

    def test_project_is_attached_by_a_sync_dependency_mutating_the_context(self, groq_only, recorded) -> None:
        # A sync dependency cannot *set* a ContextVar that the endpoint will
        # see, but it can mutate the object one already points at. If that ever
        # stops holding, usage silently loses its project attribution.
        with patch("groq.Groq", return_value=_groq_client()):
            with TestClient(_app()) as client:
                client.post("/projects/proj-42/analyze", headers=_auth())

        assert recorded[0][0].project_id == "proj-42"

    def test_unauthenticated_request_is_a_no_op_not_an_error(self, groq_only, recorded) -> None:
        # The dependency is installed app-wide, including on login and health.
        with patch("groq.Groq", return_value=_groq_client()):
            with TestClient(_app()) as client:
                resp = client.post("/analyze")

        assert resp.status_code == 200
        assert recorded == []

    def test_garbage_token_is_a_no_op_not_a_401(self, groq_only, recorded) -> None:
        with patch("groq.Groq", return_value=_groq_client()):
            with TestClient(_app()) as client:
                resp = client.post("/analyze", headers={"Authorization": "Bearer not-a-jwt"})

        assert resp.status_code == 200
        assert recorded == []

    def test_bindings_do_not_leak_between_requests(self, groq_only, recorded) -> None:
        # Each request is its own Task with its own copy of the context, so a
        # bare `set` cannot leak. Asserted because the alternative — one user's
        # tokens billed to whoever called last — fails silently.
        with patch("groq.Groq", return_value=_groq_client()):
            with TestClient(_app()) as client:
                client.post("/analyze", headers=_auth("alice"))
                client.post("/analyze", headers=_auth("bob"))
                anon = client.get("/whoami")

        assert [ctx.user_id for ctx, _ in recorded] == ["alice", "bob"]
        assert anon.json() == {"user_id": None}

    def test_project_from_one_request_does_not_bleed_into_the_next(self, groq_only, recorded) -> None:
        with patch("groq.Groq", return_value=_groq_client()):
            with TestClient(_app()) as client:
                client.post("/projects/proj-1/analyze", headers=_auth())
                client.post("/analyze", headers=_auth())

        assert [ctx.project_id for ctx, _ in recorded] == ["proj-1", None]


class TestOutOfRequestBinding:
    def test_scheduler_style_block_binds_owner_and_source(self, groq_only, recorded) -> None:
        # The monitoring worker runs with no request and therefore no ambient
        # sink; without an explicit binding its tokens land on nobody's bill.
        with patch("groq.Groq", return_value=_groq_client()):
            with usage_recorder.usage_context("owner-9", project_id="proj-7", source="schedule"):
                complete("insights", [{"role": "user", "content": "x"}], model="m")

        assert len(recorded) == 1
        ctx, _ = recorded[0]
        assert (ctx.user_id, ctx.project_id, ctx.source) == ("owner-9", "proj-7", "schedule")

    def test_sink_is_disarmed_after_the_block(self, groq_only, recorded) -> None:
        with patch("groq.Groq", return_value=_groq_client()):
            with usage_recorder.usage_context("owner-9"):
                complete("insights", [{"role": "user", "content": "x"}], model="m")
            complete("insights", [{"role": "user", "content": "x"}], model="m")

        assert len(recorded) == 1, "calls after the block must not be attributed to its owner"
