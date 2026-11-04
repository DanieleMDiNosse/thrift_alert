# ruff: noqa: F811
"""Hermes OAuth inference without real auth, agent tools or live network."""

import json
import subprocess
from types import SimpleNamespace

import pytest

from subitoo.ai.config import AIConfig
from subitoo.ai.llm import InferenceError, InferenceUnavailable, LLMClient
from tests.test_ai_service import identity, ai_conn  # noqa: F401


def config(tmp_path, **kwargs):
    home = tmp_path / "profile"
    home.mkdir(exist_ok=True)
    (home / "auth.json").write_text("{}")
    return AIConfig(
        _env_file=None,
        provider="hermes",
        hermes_home=str(home),
        hermes_python="/usr/bin/python3",
        retries=0,
        lock_path=str(tmp_path / "lock"),
        **kwargs,
    )


def test_hermes_routes_strict_json_without_app_key_or_http(monkeypatch, tmp_path):
    from subitoo.ai import hermes_bridge

    seen = []

    def run(command, **kwargs):
        seen.append((command, kwargs))
        return SimpleNamespace(
            returncode=0,
            stdout=json.dumps({"content": json.dumps(identity())}).encode(),
        )

    monkeypatch.setattr(hermes_bridge.subprocess, "run", run)
    monkeypatch.setattr(
        "subitoo.ai.llm._open_inference",
        lambda *a, **kw: pytest.fail("No API-key HTTP route"),
    )
    monkeypatch.setenv("OPENAI_API_KEY", "synthetic-not-forwarded")
    cfg = config(tmp_path, api_key="synthetic-not-forwarded")
    assert (
        LLMClient(cfg).complete("identity", {"title": "Acme Box"})["model"]
        == identity()["model"]
    )
    command, kwargs = seen[0]
    assert "-I" in command
    assert kwargs["env"]["HERMES_HOME"] == cfg.hermes_home
    assert kwargs["stderr"] == subprocess.DEVNULL
    assert not kwargs.get("shell", False)
    assert "OPENAI_API_KEY" not in kwargs["env"]
    assert "AI_API_KEY" not in kwargs["env"]
    request = json.loads(kwargs["input"])
    assert request["model"] == "gpt-6.1-sol"
    assert request["reasoning_effort"] == "low"
    assert request["response_format"]["json_schema"]["strict"] is True
    assert "tools" not in request
    assert "synthetic-not-forwarded" not in kwargs["input"].decode()
    assert cfg.public()["credential_configured"] is True
    assert cfg.public()["configuration_error"] is None
    assert cfg.hermes_home not in json.dumps(cfg.public())


@pytest.mark.parametrize(
    "code", ["missing_credentials", "auth_rejected", "model_offline", "timeout"]
)
def test_hermes_auth_and_network_failures_retain_jobs(monkeypatch, tmp_path, code):
    from subitoo.ai import hermes_bridge

    monkeypatch.setattr(
        hermes_bridge.subprocess,
        "run",
        lambda *a, **kw: SimpleNamespace(
            returncode=1, stdout=json.dumps({"error": code}).encode()
        ),
    )
    with pytest.raises(InferenceUnavailable) as caught:
        LLMClient(config(tmp_path)).complete("identity", {"title": "Acme"})
    assert "synthetic" not in str(caught.value)


def test_hermes_timeout_is_bounded_and_safe(monkeypatch, tmp_path):
    from subitoo.ai import hermes_bridge

    def run(*args, **kwargs):
        raise subprocess.TimeoutExpired(
            "private command", timeout=1, output=b"private token"
        )

    monkeypatch.setattr(hermes_bridge.subprocess, "run", run)
    with pytest.raises(InferenceUnavailable) as caught:
        LLMClient(config(tmp_path)).complete("identity", {"title": "Acme"})
    assert caught.value.code == "timeout"
    assert "private" not in str(caught.value)


@pytest.mark.parametrize(
    "body,expected",
    [
        (b"not-json", "invalid_response"),
        (b'{"content":"not-json"}', "invalid_json"),
        (b'{"content":"{}"}', "invalid_schema"),
        (b'{"error":"untrusted failure with token"}', "inference_error"),
    ],
)
def test_hermes_invalid_output_never_becomes_valid_analysis(
    monkeypatch, tmp_path, body, expected
):
    from subitoo.ai import hermes_bridge

    monkeypatch.setattr(
        hermes_bridge.subprocess,
        "run",
        lambda *a, **kw: SimpleNamespace(returncode=0, stdout=body),
    )
    with pytest.raises(InferenceError) as caught:
        LLMClient(config(tmp_path)).complete("identity", {"title": "Acme"})
    assert caught.value.code == expected


def test_hermes_missing_runtime_blocks_before_claim(ai_conn, tmp_path, monkeypatch):
    from subitoo.ai import service, store
    from subitoo.ai.worker import Worker

    cfg = config(tmp_path).model_copy(update={"hermes_python": "/nonexistent/runtime"})
    monkeypatch.setattr(store, "get_config", lambda: cfg)
    assert cfg.public()["configuration_error"]
    with pytest.raises(ValueError, match="Hermes"):
        service.action(ai_conn, "/api/ai/settings", {"enabled": True})
    ai_conn.execute("UPDATE ai_settings SET enabled=1")
    store.enqueue(ai_conn, "identify", 1)
    assert Worker(ai_conn, config=cfg).run_once() is False
    assert ai_conn.execute("SELECT attempts FROM ai_jobs").fetchone()["attempts"] == 0


def test_hermes_rejects_tools_at_bridge_boundary(tmp_path):
    from subitoo.ai.hermes_bridge import complete

    with pytest.raises(InferenceError):
        complete({"tools": [{"type": "function"}]}, config(tmp_path))


@pytest.mark.parametrize(
    "provider_code,expected",
    [
        ("rate_limit_exceeded", "model_offline"),
        ("authentication_error", "hermes_auth"),
    ],
)
def test_stream_failure_preserves_worker_job_attempts(
    ai_conn, tmp_path, monkeypatch, provider_code, expected
):
    from subitoo.ai import hermes_bridge, store
    from subitoo.ai.hermes_runner import generate, RunnerError
    from subitoo.ai.worker import Worker
    from tests.test_ai_hermes_runner import Stream

    class Client:
        def __init__(self):
            self.responses = self

        def with_options(self, **kwargs):
            return self

        def create(self, **kwargs):
            return Stream(
                [
                    SimpleNamespace(
                        type="response.failed",
                        response=SimpleNamespace(
                            error=SimpleNamespace(
                                code=provider_code, message="private provider detail"
                            )
                        ),
                    )
                ]
            )

    def run(*args, **kwargs):
        try:
            generate(
                json.loads(kwargs["input"]), lambda *a, **kw: (Client(), "gpt-6.1-sol")
            )
        except RunnerError as error:
            return SimpleNamespace(
                returncode=1, stdout=json.dumps({"error": error.code}).encode()
            )
        pytest.fail("Expected failed stream")

    monkeypatch.setattr(hermes_bridge.subprocess, "run", run)
    cfg = config(tmp_path)
    ai_conn.execute("UPDATE ai_settings SET enabled=1")
    store.enqueue(ai_conn, "identify", 1, config=cfg)
    worker = Worker(ai_conn, config=cfg)
    for _ in range(cfg.max_attempts + 1):
        ai_conn.execute("UPDATE ai_jobs SET available_at=0")
        worker.run_once()
        job = ai_conn.execute(
            "SELECT status,attempts,error,error_code,available_at FROM ai_jobs"
        ).fetchone()
        assert job["status"] == "queued"
        assert job["attempts"] == 0
        assert job["error_code"] == expected
        assert job["available_at"] > store.now()
        assert "private provider detail" not in job["error"]
    assert ai_conn.execute("SELECT COUNT(*) FROM ai_products").fetchone()[0] == 0
