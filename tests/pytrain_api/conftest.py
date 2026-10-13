from __future__ import annotations

import os
from unittest.mock import MagicMock, patch

import pytest
from fastapi.testclient import TestClient
from pytrain import CommandReq, CommandScope


TEST_ENV = {
    "SECRET_KEY": "unit-test-secret-key-at-least-32-bytes",
    "SECRET_PHRASE": "unit-test-secret-phrase-32-bytes-long",
    "ALGORITHM": "HS256",
    "API_TOKEN": "test-api-key",
    "API_SERVER": "api.example.test",
    "UNSECURE_TOKENS": "",
    "ALEXA_TOKEN_EXP_MIN": "15",
}

# Keep import-time defaults independent of the developer's .env and credentials.
with patch.dict(os.environ, TEST_ENV), patch("dotenv.load_dotenv"):
    from src.pytrain_api import endpoints


@pytest.fixture(autouse=True)
def isolated_api_settings(monkeypatch):
    for name, value in TEST_ENV.items():
        monkeypatch.setattr(endpoints, name, int(value) if name == "ALEXA_TOKEN_EXP_MIN" else value)
    monkeypatch.setattr(endpoints, "API_KEYS", {})
    monkeypatch.setattr(endpoints.app, "dependency_overrides", {})
    monkeypatch.setattr(endpoints.app, "openapi_schema", None)
    monkeypatch.setattr(CommandReq, "send", MagicMock(side_effect=AssertionError("Unexpected hardware command")))


@pytest.fixture
def headers():
    return {"X-API-Key": TEST_ENV["API_TOKEN"]}


@pytest.fixture
def api(monkeypatch):
    instance = MagicMock(spec=endpoints.PyTrainApi)
    instance.pytrain = MagicMock()
    monkeypatch.setattr(endpoints.PyTrainApi, "get", MagicMock(return_value=instance))
    return instance


@pytest.fixture
def client(api):
    # No context manager: endpoint tests must not advertise a real Zeroconf service.
    client = TestClient(endpoints.app)
    yield client
    client.close()


@pytest.fixture
def components(monkeypatch):
    mocks = {}
    for name, scope in (
        ("accessory", CommandScope.ACC),
        ("block", CommandScope.BLOCK),
        ("engine", CommandScope.ENGINE),
        ("route", CommandScope.ROUTE),
        ("switch", CommandScope.SWITCH),
        ("train", CommandScope.TRAIN),
    ):
        component = MagicMock(spec=type(getattr(endpoints, f"_{name}")))
        component.scope = scope
        monkeypatch.setattr(endpoints, f"_{name}", component)
        mocks[name] = component
    return mocks
