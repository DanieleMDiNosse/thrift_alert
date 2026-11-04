"""Runtime request contract and safe error classification."""

import json
import pytest
from subitoo.ai.config import AIConfig
from subitoo.ai.llm import LLMClient, InferenceError
from tests.test_ai_service import identity


class Reply:
    def __init__(self, content, finish="stop"):
        self.content = content
        self.finish = finish

    def __enter__(self):
        return self

    def __exit__(self, *args):
        pass

    def read(self, n):
        return json.dumps(
            {
                "choices": [
                    {"finish_reason": self.finish, "message": {"content": self.content}}
                ]
            }
        ).encode()


def test_local_request_disables_default_xhigh_thinking_and_uses_schema(
    monkeypatch, tmp_path
):
    seen = []

    def opened(request, timeout):
        seen.append(json.loads(request.data))
        return Reply(json.dumps(identity()))

    monkeypatch.setattr("subitoo.ai.llm._open_inference", opened)
    config = AIConfig(_env_file=None, lock_path=str(tmp_path / "lock"), retries=0)
    assert (
        LLMClient(config).complete("identity", {"title": "Acme Box"})["model"] == "Box"
    )
    payload = seen[0]
    assert payload["chat_template_kwargs"]["enable_thinking"] is False
    assert payload["response_format"]["type"] == "json_schema"
    assert (
        payload["response_format"]["json_schema"]["schema"]["additionalProperties"]
        is False
    )


@pytest.mark.parametrize(
    "reply,code",
    [
        (Reply("", "length"), "output_truncated"),
        (Reply(""), "empty_response"),
        (Reply("not json"), "invalid_json"),
        (Reply('{"category":"camera"}'), "invalid_schema"),
    ],
)
def test_failures_keep_specific_secret_safe_codes(monkeypatch, tmp_path, reply, code):
    monkeypatch.setattr("subitoo.ai.llm._open_inference", lambda *args, **kw: reply)
    cfg = AIConfig(_env_file=None, retries=0, lock_path=str(tmp_path / "lock"))
    with pytest.raises(InferenceError) as caught:
        LLMClient(cfg).complete("identity", {"title": "Example"})
    assert caught.value.code == code
    assert "not json" not in str(caught.value)


def test_research_schema_is_bounded_to_requested_criteria_and_sources(
    monkeypatch, tmp_path
):
    seen = []
    research = {
        "criteria": {},
        "strengths": [],
        "weaknesses": [],
        "known_issues": [],
        "specifications": {},
        "summary": "Evidence limited",
        "confidence": 0.5,
    }

    def opened(request, timeout):
        seen.append(json.loads(request.data))
        return Reply(json.dumps(research))

    monkeypatch.setattr("subitoo.ai.llm._open_inference", opened)
    cfg = AIConfig(_env_file=None, retries=0, lock_path=str(tmp_path / "lock"))
    LLMClient(cfg).complete(
        "research",
        {
            "category_criteria": ["sound_quality", "reliability"],
            "sources": [{"id": "source-real"}],
        },
    )
    payload = seen[0]
    schema = payload["response_format"]["json_schema"]["schema"]
    assert set(schema["properties"]["criteria"]["properties"]) == {
        "sound_quality",
        "reliability",
    }
    assert schema["properties"]["criteria"]["additionalProperties"] is False
    assert schema["$defs"]["Criterion"]["properties"]["source_ids"]["items"][
        "enum"
    ] == ["source-real"]
    assert payload["max_tokens"] <= 1600
    assert '"$defs"' not in payload["messages"][0]["content"]


def test_chat_echo_is_not_a_successful_answer(monkeypatch, tmp_path):
    question = "Quali compromessi per bikepacking?"
    monkeypatch.setattr(
        "subitoo.ai.llm._open_inference",
        lambda *args, **kw: Reply(json.dumps({"message": question, "source_ids": []})),
    )
    cfg = AIConfig(_env_file=None, retries=0, lock_path=str(tmp_path / "lock"))
    with pytest.raises(InferenceError) as caught:
        LLMClient(cfg).complete(
            "chat",
            {"products": [], "messages": [{"role": "user", "content": question}]},
        )
    assert caught.value.code == "unhelpful_response"
