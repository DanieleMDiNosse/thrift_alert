# ruff: noqa: F811
"""On-demand mode never dispatches legacy scoring/research automatically."""

from unittest.mock import Mock
import pytest
from subitoo.ai.config import AIConfig
from subitoo.ai import store
from subitoo.ai.worker import Worker
from subitoo.ai.llm import InferenceUnavailable
from tests.test_ai_service import ai_conn  # noqa: F401
from tests.test_gui import gui, request  # noqa: F401


def test_worth_inference_uses_dedicated_prompt_schema_and_validator(
    tmp_path, monkeypatch
):
    import json
    import sys
    from types import SimpleNamespace
    from pydantic import BaseModel
    from subitoo.ai.llm import LLMClient
    from tests.test_ai_inference_runtime import Reply

    class Contract(BaseModel):
        message: str
        source_ids: list[str]
        new_price: None

    validate = Mock(
        side_effect=lambda value, ids: Contract.model_validate(value).model_dump()
    )
    monkeypatch.setitem(
        sys.modules,
        "subitoo.ai.worth",
        SimpleNamespace(Worth=Contract, validate_worth=validate),
    )
    seen = []

    def opened(req, timeout):
        seen.append(json.loads(req.data))
        return Reply(
            json.dumps(
                {
                    "message": "Confronto prudente: prezzo del nuovo non verificato.",
                    "source_ids": [],
                    "new_price": None,
                }
            )
        )

    monkeypatch.setattr("subitoo.ai.llm._open_inference", opened)
    cfg = AIConfig(
        _env_file=None, retries=0, lock_path=str(tmp_path / "inference.lock")
    )
    result = LLMClient(cfg).complete("worth", {"listing": {"price": 50}, "sources": []})
    assert result["new_price"] is None
    validate.assert_called_once()
    assert (
        "confrontando il prezzo col prezzo del nuovo"
        in seen[0]["messages"][0]["content"]
    )
    assert seen[0]["response_format"]["json_schema"]["name"] == "worth"
    assert "Non assegnare punteggi" in seen[0]["messages"][0]["content"]


def test_on_demand_does_not_process_automatic_jobs(ai_conn):
    ai_conn.execute("UPDATE ai_settings SET enabled=1")
    store.enqueue(ai_conn, "identify", 1)
    cfg = AIConfig(_env_file=None, workflow="on_demand")
    worker = Worker(ai_conn, config=cfg)
    worker._process = Mock(side_effect=AssertionError("No automatic inference"))
    assert worker.run_once() is False
    worker._process.assert_not_called()
    assert ai_conn.execute("SELECT attempts FROM ai_jobs").fetchone()["attempts"] == 0


def test_explicit_worth_request_is_not_blocked_by_legacy_pause(ai_conn):
    ai_conn.execute("UPDATE ai_settings SET enabled=0")
    store.enqueue(ai_conn, "worth", "test-key")
    cfg = AIConfig(_env_file=None, workflow="on_demand")
    worker = Worker(ai_conn, config=cfg)
    worker._process = Mock(side_effect=InferenceUnavailable(code="model_offline"))
    assert worker.run_once() is True
    row = ai_conn.execute("SELECT status,attempts FROM ai_jobs").fetchone()
    assert row["status"] == "queued" and row["attempts"] == 0


def test_on_demand_main_never_discovers_listings(ai_conn, monkeypatch):
    from subitoo.ai import worker

    cfg = AIConfig(_env_file=None, workflow="on_demand")
    monkeypatch.setattr(worker, "get_config", lambda: cfg)
    ai_conn.execute("UPDATE ai_settings SET enabled=1")
    discovery = Mock(side_effect=AssertionError("No background discovery"))
    monkeypatch.setattr(store, "discover", discovery)
    path = ai_conn.execute("PRAGMA database_list").fetchone()[2]
    worker.main(["--db", path, "--once"])
    discovery.assert_not_called()


def test_on_demand_index_hides_rankings_and_loads_listing_button(gui, monkeypatch):
    from subitoo.ai import config

    monkeypatch.setattr(
        config, "get_config", lambda: AIConfig(_env_file=None, workflow="on_demand")
    )
    status, body, _ = request(gui, "/")
    assert status == 200
    assert 'data-workflow="on_demand" hidden' in body
    assert "/static/worth.js" in body


@pytest.mark.parametrize(
    "data",
    [
        {"site": "subito", "id": "abc", "prompt": "execute instructions"},
        {"site": "unknown", "id": "abc"},
        {"site": "subito", "id": "abc", "refresh": "yes"},
    ],
)
def test_worth_api_rejects_privileged_or_malformed_inputs(gui, data):
    assert request(gui, "/api/ai/worth", data)[0] == 400


def test_worth_endpoint_uses_existing_csrf_boundary(gui):
    assert (
        request(gui, "/api/ai/worth", {"site": "subito", "id": "abc"}, csrf=False)[0]
        == 403
    )
