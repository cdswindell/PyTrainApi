import asyncio
import json
import mimetypes
from datetime import datetime, timedelta, timezone
from unittest.mock import MagicMock, call

import jwt
import pytest
from fastapi import HTTPException
from fastapi.testclient import TestClient
from pydantic import ValidationError
from starlette.exceptions import HTTPException as StarletteHTTPException

from src.pytrain_api import endpoints


def signed_token(payload, secret=None):
    return jwt.encode(payload, secret or endpoints.SECRET_KEY, algorithm=endpoints.ALGORITHM)


@pytest.mark.parametrize("key", [None, "", 123, [], {}, " ", "Bearer ", "wrong-key"])
def test_get_api_token_rejects_missing_or_invalid_raw_keys(key):
    with pytest.raises(HTTPException) as exc:
        endpoints.get_api_token(key)
    assert exc.value.status_code == 401
    assert exc.value.detail == "Missing or invalid API key"


@pytest.mark.parametrize("key", ["test-api-key", " test-api-key ", "Bearer test-api-key", "bEaReR  test-api-key "])
def test_get_api_token_normalizes_raw_key(key):
    assert endpoints.get_api_token(key) is True


@pytest.mark.parametrize("keys", [{"guid": "local-key"}, {"local-key"}, ["local-key"], ("local-key",)])
def test_get_api_token_accepts_stored_raw_values(monkeypatch, keys):
    monkeypatch.setattr(endpoints, "API_KEYS", keys)
    assert endpoints.get_api_token("local-key") is True


@pytest.mark.parametrize("keys", [{"guid": "local-key"}, None, 42])
def test_get_api_token_does_not_authorize_mapping_names_or_misconfigured_stores(monkeypatch, keys):
    monkeypatch.setattr(endpoints, "API_KEYS", keys)
    with pytest.raises(HTTPException) as exc:
        endpoints.get_api_token("guid")
    assert exc.value.status_code == 401
    assert exc.value.detail == "Missing or invalid API key"


@pytest.mark.parametrize("storage", ["unknown", "primary", "dict", "list", "noniterable"])
def test_get_api_token_accepts_signed_magic_claim(monkeypatch, storage):
    token = signed_token({"magic": endpoints.API_NAME})
    if storage == "primary":
        monkeypatch.setattr(endpoints, "API_TOKEN", token)
    elif storage == "dict":
        monkeypatch.setattr(endpoints, "API_KEYS", {"guid": token})
    elif storage == "list":
        monkeypatch.setattr(endpoints, "API_KEYS", [token])
    elif storage == "noniterable":
        monkeypatch.setattr(endpoints, "API_KEYS", None)
    assert endpoints.get_api_token(f"Bearer {token}") is True


@pytest.mark.parametrize("existing", [None, "same", "outdated-token"])
def test_get_api_token_registers_and_refreshes_server_guid(existing):
    token = signed_token({"SERVER": endpoints.API_SERVER, "GUID": "client-guid"})
    if existing is not None:
        endpoints.API_KEYS["client-guid"] = token if existing == "same" else existing
    assert endpoints.get_api_token(token) is True
    assert endpoints.API_KEYS == {"client-guid": token}


@pytest.mark.parametrize(
    "claims",
    [
        {},
        {"magic": "wrong"},
        {"SERVER": "other", "GUID": "guid"},
        {"SERVER": "api.example.test"},
        {"SERVER": "api.example.test", "GUID": ""},
    ],
)
def test_get_api_token_rejects_signed_tokens_without_authorizing_claims(claims):
    with pytest.raises(HTTPException) as exc:
        endpoints.get_api_token(signed_token(claims))
    assert exc.value.status_code == 401
    assert exc.value.detail == "Missing or invalid API key"
    assert endpoints.API_KEYS == {}


def test_get_api_token_requires_mapping_for_server_guid(monkeypatch):
    monkeypatch.setattr(endpoints, "API_KEYS", [])
    token = signed_token({"SERVER": endpoints.API_SERVER, "GUID": "guid"})
    with pytest.raises(HTTPException) as exc:
        endpoints.get_api_token(token)
    assert exc.value.status_code == 401
    assert exc.value.detail == "Server token mapping not supported"


@pytest.mark.parametrize(
    ("kind", "status", "detail"),
    [
        ("expired", 498, "Signature has expired"),
        ("signature", 401, "Signature verification failed"),
        ("algorithm", 401, "The specified alg value is not allowed"),
        ("future", 401, "The token is not yet valid (nbf)"),
        ("malformed", 401, "Invalid header padding"),
    ],
)
def test_jwt_auth_errors_are_http_responses(client, kind, status, detail):
    claims = {"magic": endpoints.API_NAME}
    if kind == "expired":
        claims["exp"] = datetime.now(timezone.utc) - timedelta(seconds=1)
    elif kind == "future":
        claims["nbf"] = datetime.now(timezone.utc) + timedelta(hours=1)
    token = signed_token(claims, "incorrect-secret-key-at-least-32-bytes" if kind == "signature" else None)
    if kind == "algorithm":
        token = jwt.encode(claims, endpoints.SECRET_KEY, algorithm="HS384")
    elif kind == "malformed":
        token = "a.b.c"
    response = client.post("/pytrain/v1/system/version", headers={"X-API-Key": token})
    assert response.status_code == status
    assert response.json() == {"detail": detail}
    assert endpoints.API_KEYS == {}


@pytest.mark.parametrize("delta", [None, timedelta(minutes=2), timedelta(seconds=-1), timedelta(0)])
def test_create_api_token_sets_expiry_magic_and_preserves_input(delta):
    payload = {"GUID": "client", "magic": "original", "exp": 123}
    before = datetime.now(timezone.utc)
    token = endpoints.create_api_token(payload, delta)
    after = datetime.now(timezone.utc)
    decoded = jwt.decode(token, endpoints.SECRET_KEY, algorithms=[endpoints.ALGORITHM], options={"verify_exp": False})
    duration = delta or timedelta(days=365)
    assert int((before + duration).timestamp()) <= decoded["exp"] <= int((after + duration).timestamp())
    assert decoded == {"GUID": "client", "magic": endpoints.API_NAME, "exp": decoded["exp"]}
    assert payload == {"GUID": "client", "magic": "original", "exp": 123}


def test_create_api_token_without_payload_and_with_custom_secret():
    secret = "custom-secret-key-at-least-32-bytes"
    token = endpoints.create_api_token(secret=secret)
    payload = jwt.decode(token, secret, algorithms=[endpoints.ALGORITHM])
    assert set(payload) == {"magic", "exp"}
    assert payload["magic"] == endpoints.API_NAME
    with pytest.raises(jwt.InvalidSignatureError):
        jwt.decode(token, endpoints.SECRET_KEY, algorithms=[endpoints.ALGORITHM])


@pytest.mark.parametrize("length", [0, 1, 32, 64])
def test_create_secret_hex_length(length):
    secret = endpoints.create_secret(length)
    assert len(secret) == 2 * length
    assert all(char in "0123456789abcdef" for char in secret)


def test_create_secret_default_and_invalid_length():
    assert len(endpoints.create_secret()) == 64
    with pytest.raises(ValueError):
        endpoints.create_secret(-1)


@pytest.mark.parametrize("local_key", [None, b"", b"discovery-key"])
@pytest.mark.parametrize("body_fails", [False, True])
def test_lifespan_registers_local_key_and_always_shuts_down(api, local_key, body_fails):
    api.service_info = MagicMock(properties={} if local_key is None else {b"uuid": local_key})

    async def exercise():
        async with endpoints.lifespan(endpoints.app):
            api.create_service.assert_called_once_with()
            api.shutdown_service.assert_not_called()
            assert endpoints.API_KEYS == ({"discovery-key": "discovery-key"} if local_key else {})
            if local_key:
                assert endpoints.get_api_token(local_key.decode()) is True
            if body_fails:
                raise RuntimeError("lifespan body failed")

    if body_fails:
        with pytest.raises(RuntimeError, match="lifespan body failed"):
            asyncio.run(exercise())
    else:
        asyncio.run(exercise())
    assert api.mock_calls == [call.create_service(), call.shutdown_service()]


def test_lifespan_startup_failure_is_propagated(api):
    api.create_service.side_effect = RuntimeError("discovery unavailable")

    async def exercise():
        async with endpoints.lifespan(endpoints.app):
            pytest.fail("startup failure must not enter application lifetime")

    with pytest.raises(RuntimeError, match="discovery unavailable"):
        asyncio.run(exercise())
    api.shutdown_service.assert_not_called()
    assert endpoints.API_KEYS == {}


@pytest.mark.parametrize(
    ("path", "setting", "media_type"),
    [
        ("/favicon.ico", "FAVICON_PATH", mimetypes.guess_type("favicon.ico")[0]),
        ("/apple-touch-icon.png", "APPLE_ICON_PATH", "image/png"),
        ("/apple-touch-icon-precomposed.png", "APPLE_ICON_PATH", "image/png"),
    ],
)
def test_public_icons_serve_file_without_authentication(client, monkeypatch, tmp_path, path, setting, media_type):
    icon = tmp_path / path.removeprefix("/")
    icon.write_bytes(b"test icon bytes")
    monkeypatch.setattr(endpoints, setting, str(icon))
    response = client.get(path)
    assert response.status_code == 200
    assert response.content == b"test icon bytes"
    assert response.headers["content-type"] == media_type
    assert response.headers["content-length"] == "15"


@pytest.mark.parametrize(
    ("path", "setting"),
    [
        ("/favicon.ico", "FAVICON_PATH"),
        ("/apple-touch-icon.png", "APPLE_ICON_PATH"),
        ("/apple-touch-icon-precomposed.png", "APPLE_ICON_PATH"),
    ],
)
def test_missing_public_icons_are_forbidden(client, monkeypatch, path, setting):
    monkeypatch.setattr(endpoints, setting, None)
    response = client.get(path)
    assert response.status_code == 403
    assert response.json() == {"detail": "Forbidden"}


@pytest.mark.parametrize("path", ["/pytrain", "/pytrain/v1"])
def test_public_documentation_redirects(client, path):
    response = client.get(path, follow_redirects=False)
    assert response.status_code == 301
    assert response.headers["location"] == "/docs"


def test_public_docs_use_openapi_and_custom_favicon(client):
    response = client.get("/docs")
    assert response.status_code == 200
    assert response.headers["content-type"].startswith("text/html")
    assert f"<title>{endpoints.PROGRAM_NAME} API</title>" in response.text
    assert "url: '/openapi.json'" in response.text
    assert 'href="/static/favicon.ico"' in response.text


@pytest.mark.parametrize("signing_key", ["API_SERVER", "SECRET_PHRASE"])
@pytest.mark.parametrize("server", ["api.example.test", "API.EXAMPLE.TEST"])
def test_public_version_issues_usable_expiring_token(client, monkeypatch, signing_key, server):
    monkeypatch.setattr(endpoints.uuid, "uuid4", lambda: "new-client-guid")
    token = signed_token({"SERVER": server}, getattr(endpoints, signing_key))
    before = datetime.now(timezone.utc)
    response = client.post("/version", json={"uid": token})
    after = datetime.now(timezone.utc)
    assert response.status_code == 200
    body = response.json()
    assert set(body) == {"api-token", "pytrain", "pytrain_api"}
    assert isinstance(body["pytrain"], str) and body["pytrain"]
    assert isinstance(body["pytrain_api"], str) and body["pytrain_api"]
    claims = jwt.decode(body["api-token"], endpoints.SECRET_KEY, algorithms=[endpoints.ALGORITHM])
    assert claims == {"GUID": "new-client-guid", "SERVER": server, "magic": endpoints.API_NAME, "exp": claims["exp"]}
    duration = timedelta(minutes=15)
    assert int((before + duration).timestamp()) <= claims["exp"] <= int((after + duration).timestamp())
    assert endpoints.API_KEYS == {"new-client-guid": body["api-token"]}
    authenticated = client.post("/pytrain/v1/system/version", headers={"X-API-Key": body["api-token"]})
    assert authenticated.status_code == 200
    assert authenticated.json() == {"pytrain": body["pytrain"], "pytrain_api": body["pytrain_api"]}


@pytest.mark.parametrize("claims", [{}, {"SERVER": "other.example.test"}])
def test_public_version_rejects_missing_or_mismatched_server(client, claims):
    response = client.post("/version", json={"uid": signed_token(claims, endpoints.API_SERVER)})
    assert response.status_code == 403
    assert response.json() == {"detail": "Forbidden"}
    assert endpoints.API_KEYS == {}


def test_public_version_rejects_wrong_signature(client):
    token = signed_token({"SERVER": endpoints.API_SERVER}, "untrusted-secret-at-least-32-bytes")
    response = client.post("/version", json={"uid": token})
    assert response.status_code == 403
    assert response.json() == {"detail": "Forbidden"}
    assert endpoints.API_KEYS == {}


@pytest.mark.parametrize("body", [{}, {"uid": None}, {"uid": 1}])
def test_public_version_validates_request_body(client, body):
    response = client.post("/version", json=body)
    assert response.status_code == 422
    assert response.json()["detail"][0]["loc"] == ["body", "uid"]
    assert endpoints.API_KEYS == {}


@pytest.mark.parametrize("kind", ["malformed", "expired", "nonstring-server"])
def test_public_version_unhandled_token_errors_currently_return_500(kind):
    claims = {"SERVER": endpoints.API_SERVER}
    if kind == "expired":
        claims["exp"] = datetime.now(timezone.utc) - timedelta(seconds=1)
    elif kind == "nonstring-server":
        claims["SERVER"] = 42
    token = "not-a-jwt" if kind == "malformed" else signed_token(claims, endpoints.API_SERVER)
    client = TestClient(endpoints.app, raise_server_exceptions=False)
    response = client.post("/version", json={"uid": token})
    assert response.status_code == 500
    assert response.text == "Internal Server Error"
    assert endpoints.API_KEYS == {}


@pytest.mark.parametrize(
    ("status_code", "headers", "expected_status", "detail"),
    [
        (404, None, 403, "Forbidden"),
        (404, {"X-Error": "other"}, 403, "Forbidden"),
        (404, {"X-Error": "404"}, 404, "missing train"),
        (400, None, 400, "missing train"),
        (401, None, 401, "missing train"),
        (498, None, 498, "missing train"),
    ],
)
def test_http_exception_handler_hides_only_unmarked_not_found(status_code, headers, expected_status, detail):
    exc = StarletteHTTPException(status_code, detail="missing train", headers=headers)
    response = asyncio.run(endpoints.http_exception_handler(None, exc))
    assert response.status_code == expected_status
    assert json.loads(response.body) == {"detail": detail}


def test_unregistered_route_is_forbidden(client):
    response = client.get("/not-a-real-route")
    assert response.status_code == 403
    assert response.json() == {"detail": "Forbidden"}


def test_validation_exception_handler_combines_and_normalizes_messages():
    exc = ValidationError.from_exception_data(
        "Token",
        [
            {
                "type": "value_error",
                "loc": ("access_token",),
                "input": "bad",
                "ctx": {"error": ValueError("bad token")},
            },
            {"type": "value_error", "loc": ("token_type",), "input": "bad", "ctx": {"error": ValueError("bad type")}},
        ],
    )
    response = asyncio.run(endpoints.validation_exception_handler(None, exc))
    assert response.status_code == 422
    assert json.loads(response.body) == {"detail": "bad token; bad type"}


def test_validation_exception_handler_falls_back_to_string():
    response = asyncio.run(endpoints.validation_exception_handler(None, ValueError("invalid value")))
    assert response.status_code == 422
    assert json.loads(response.body) == {"detail": "invalid value"}
