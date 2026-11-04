# ruff: noqa: F811
"""Cloud GPT transport without real credentials or network calls."""

import json
import pytest
from subitoo.ai.config import AIConfig
from subitoo.ai.llm import LLMClient, InferenceError
from tests.test_ai_service import identity, ai_conn  # noqa: F401
from tests.test_ai_inference_runtime import Reply


def config(tmp_path, **kwargs):
    return AIConfig(
        _env_file=None,
        provider="openai",
        api_key="synthetic-credential",
        lock_path=str(tmp_path / "lock"),
        retries=0,
        **kwargs,
    )


def test_gpt_low_uses_cloud_parameters_not_llamacpp(monkeypatch, tmp_path):
    seen = []

    def opened(req, timeout):
        seen.append((req, json.loads(req.data)))
        return Reply(json.dumps(identity()))

    monkeypatch.setattr("subitoo.ai.llm._open_inference", opened)
    cfg = config(tmp_path)
    LLMClient(cfg).complete("identity", {"title": "Acme Box"})
    req, payload = seen[0]
    assert req.full_url == "https://api.openai.com/v1/chat/completions"
    assert payload["model"] == "gpt-6.1-sol"
    assert payload["reasoning_effort"] == "low"
    assert payload["store"] is False
    assert payload["max_completion_tokens"] == cfg.max_tokens
    assert all(
        key not in payload
        for key in ["chat_template_kwargs", "max_tokens", "temperature"]
    )
    assert cfg.public()["provider"] == "openai"
    assert "synthetic-credential" not in json.dumps(cfg.public())


def test_missing_key_blocks_before_any_request(monkeypatch, tmp_path):
    def forbidden(*args, **kwargs):
        raise AssertionError("No network without configured key")

    monkeypatch.setattr("subitoo.ai.llm._open_inference", forbidden)
    cfg = AIConfig(
        _env_file=None, provider="openai", lock_path=str(tmp_path / "lock"), retries=0
    )
    with pytest.raises(InferenceError) as caught:
        LLMClient(cfg).complete("identity", {"title": "Acme"})
    assert caught.value.code == "missing_api_key"


def test_openai_strict_research_schema_and_nullable_missing_evidence(
    monkeypatch, tmp_path
):
    seen = []
    result = {
        "criteria": {
            "sound_quality": {"score": 75, "source_ids": ["s"]},
            "reliability": None,
        },
        "strengths": [],
        "weaknesses": [],
        "known_issues": [],
        "specifications": [{"key": "power", "value": "20W"}],
        "summary": "Source synthesis",
        "confidence": 0.7,
        "source_assessments": {"s": None},
        "professional_summary": "Review",
        "user_summary": "",
    }

    def opened(req, timeout):
        seen.append(json.loads(req.data))
        return Reply(json.dumps(result))

    monkeypatch.setattr("subitoo.ai.llm._open_inference", opened)
    answer = LLMClient(config(tmp_path)).complete(
        "research",
        {
            "category_criteria": ["sound_quality", "reliability"],
            "sources": [{"id": "s"}],
        },
    )
    assert answer["specifications"] == {"power": "20W"}
    assert "reliability" not in answer["criteria"]
    assert answer["source_assessments"] == {}
    schema = seen[0]["response_format"]["json_schema"]["schema"]

    def check(node):
        if isinstance(node, dict):
            if node.get("type") == "object":
                assert node["additionalProperties"] is False
                assert set(node.get("required", [])) == set(node.get("properties", {}))
            assert "default" not in node
            for value in node.values():
                check(value)
        elif isinstance(node, list):
            for value in node:
                check(value)

    check(schema)


def test_cloud_endpoint_must_not_send_key_over_plain_http(monkeypatch, tmp_path):
    monkeypatch.setattr(
        "subitoo.ai.llm._open_inference",
        lambda *args, **kwargs: pytest.fail("Credential request must be blocked"),
    )
    with pytest.raises(InferenceError):
        LLMClient(config(tmp_path, endpoint="http://example.org/v1")).complete(
            "identity", {"title": "Acme"}
        )


def test_enabling_cloud_without_key_is_rejected(ai_conn, monkeypatch):
    from subitoo.ai import service, store

    cfg = AIConfig(_env_file=None, provider="openai")
    monkeypatch.setattr(store, "get_config", lambda: cfg)
    with pytest.raises(ValueError, match="AI_API_KEY"):
        service.action(ai_conn, "/api/ai/settings", {"enabled": True})
    assert store.settings(ai_conn)["enabled"] is False


def test_missing_key_does_not_consume_queued_jobs(ai_conn):
    from subitoo.ai import store
    from subitoo.ai.worker import Worker

    ai_conn.execute("UPDATE ai_settings SET enabled=1")
    store.enqueue(ai_conn, "identify", 1)
    assert (
        Worker(ai_conn, config=AIConfig(_env_file=None, provider="openai")).run_once()
        is False
    )
    row = ai_conn.execute("SELECT status,attempts FROM ai_jobs").fetchone()
    assert row["status"] == "queued" and row["attempts"] == 0
