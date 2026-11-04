"""Shared test fixtures: in-memory DB, fake adapters, and a recording sender."""

from __future__ import annotations

import os
import pytest

from subitoo import registry
from subitoo.core import db
from tests._helpers import BoomSite, FakeSite, Recorder


@pytest.fixture(autouse=True)
def _isolated_ai_configuration(request, monkeypatch):
    """AI tests must not depend on private .env or the operator's model settings."""
    if request.node.path.name.startswith('test_ai_'):
        from subitoo.ai.config import AIConfig
        for name in list(os.environ):
            if name.startswith('AI_'):
                monkeypatch.delenv(name)
        monkeypatch.setitem(AIConfig.model_config, 'env_file', None)


@pytest.fixture(autouse=True)
def _register_fakes():
    registry.load_all()  # pull in real adapters too
    registry.SITES["fake"] = FakeSite
    registry.SITES["boom"] = BoomSite
    FakeSite.to_return = []
    yield
    registry.SITES.pop("fake", None)
    registry.SITES.pop("boom", None)


@pytest.fixture
def conn():
    c = db.init_db(":memory:")
    yield c
    c.close()


@pytest.fixture
def recorder():
    return Recorder()
