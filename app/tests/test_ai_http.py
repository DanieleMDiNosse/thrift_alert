# ruff: noqa: F811
# Imported fixture is registered by pytest and used as a function argument.
"""Exercise the AI HTTP boundary without network or inference."""

import json
import sys
from types import SimpleNamespace
import pytest
from tests.test_gui import gui, request  # noqa: F401


def test_ai_assets_preserve_dashboard_and_csp(gui):
    status, html, headers = request(gui, "/")
    assert status == 200
    assert 'id="ai-assistant"' in html
    assert "/static/ai.js" in html and "/static/ai.css" in html
    assert "Le tue ricerche" in html
    assert request(gui, "/static/ai.js")[0] == 200
    assert request(gui, "/static/ai.css")[0] == 200


def test_ai_get_and_post_inherit_host_csrf_and_origin_guards(gui, monkeypatch):
    calls = []
    fake = SimpleNamespace(
        snapshot=lambda conn, **kw: {"products": [], **kw},
        action=lambda conn, path, data: calls.append((path, data)) or {"ok": True},
    )
    monkeypatch.setitem(sys.modules, "subitoo.ai.service", fake)
    code, body, _ = request(gui, "/api/ai/state?category=camera&watch_id=2")
    assert code == 200 and json.loads(body)["category"] == "camera"
    assert request(gui, "/api/ai/sync", {}, csrf=False)[0] == 403
    assert (
        request(gui, "/api/ai/sync", {}, headers={"Origin": "https://evil.example"})[0]
        == 403
    )
    assert calls == []
    assert request(gui, "/api/ai/sync", {})[0] == 200
    assert len(calls) == 1
    assert request(gui, "/api/ai/state", headers={"Host": "evil.example"})[0] == 403


@pytest.mark.parametrize(
    "query",
    ["watch_id=-1", "watch_id=bad", "category=" + ("a" * 101), "watch_id=1&watch_id=2"],
)
def test_invalid_ai_query_is_client_error(gui, query):
    assert request(gui, "/api/ai/state?" + query)[0] == 400
