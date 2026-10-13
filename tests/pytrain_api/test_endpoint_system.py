from unittest.mock import MagicMock, call

import pytest
from fastapi import APIRouter, HTTPException
from fastapi.testclient import TestClient

from src import pytrain_api
from src.pytrain_api import endpoints


COMMAND_ROUTES = [
    ("GET", "halt", endpoints.TMCC1HaltCommandEnum.HALT),
    *[
        ("POST", action + suffix, getattr(endpoints.TMCC1SyncCommandEnum, action.upper()))
        for action in ("reboot", "restart", "resync", "shutdown", "update")
        for suffix in ("", "_req")
    ],
]
SYSTEM_ROUTES = [(method, path) for method, path, _ in COMMAND_ROUTES] + [
    ("POST", action + suffix) for action in ("debug", "echo", "stop_all", "version") for suffix in ("", "_req")
]


@pytest.fixture
def system_command(monkeypatch):
    command = MagicMock(name="system_command", spec=endpoints.CommandReq)
    factory = MagicMock(return_value=command)
    monkeypatch.setattr(endpoints, "CommandReq", factory)
    return factory, command


@pytest.mark.parametrize(("method", "path"), SYSTEM_ROUTES)
@pytest.mark.parametrize("auth", [None, "incorrect-api-key"])
def test_all_system_aliases_require_authentication(client, api, system_command, method, path, auth):
    response = client.request(method, f"/pytrain/v1/system/{path}", headers={} if auth is None else {"X-API-Key": auth})
    assert response.status_code == 401
    assert response.json() == {"detail": "Not authenticated" if auth is None else "Missing or invalid API key"}
    system_command[0].assert_not_called()
    assert api.pytrain.mock_calls == []


@pytest.mark.parametrize(("method", "path", "command_enum"), COMMAND_ROUTES)
def test_system_command_dispatch(client, headers, system_command, method, path, command_enum):
    factory, command = system_command
    response = client.request(method, f"/pytrain/v1/system/{path}", headers=headers)
    assert response.status_code == 200
    assert response.json() == {"status": f"{command_enum.name} command sent"}
    factory.assert_called_once_with(command_enum)
    command.send.assert_called_once_with()


@pytest.mark.parametrize(("method", "path", "command_enum"), COMMAND_ROUTES)
@pytest.mark.parametrize("failure", ["construct", "send"])
def test_system_command_errors_return_400(client, headers, system_command, method, path, command_enum, failure):
    factory, command = system_command
    if failure == "construct":
        factory.side_effect = RuntimeError("command unavailable")
    else:
        command.send.side_effect = RuntimeError("command unavailable")
    response = client.request(method, f"/pytrain/v1/system/{path}", headers=headers)
    assert response.status_code == 400
    assert response.json() == {"detail": "command unavailable"}
    factory.assert_called_once_with(command_enum)
    if failure == "construct":
        command.send.assert_not_called()
    else:
        command.send.assert_called_once_with()


@pytest.mark.parametrize(("action", "label"), [("debug", "Debugging"), ("echo", "Echo")])
@pytest.mark.parametrize("suffix", ["", "_req"])
@pytest.mark.parametrize(("params", "enabled"), [({}, True), ({"on": "true"}, True), ({"on": "false"}, False)])
def test_debug_and_echo_aliases_queue_exact_command(client, headers, api, action, label, suffix, params, enabled):
    response = client.post(f"/pytrain/v1/system/{action}{suffix}", headers=headers, params=params)
    assert response.status_code == 200
    assert response.json() == {"status": f"{label} {'enabled' if enabled else 'disabled'}"}
    assert api.pytrain.mock_calls == [call.queue_command(f"{action} {'on' if enabled else 'off'}")]


@pytest.mark.parametrize("path", ["debug", "debug_req", "echo", "echo_req"])
def test_debug_and_echo_reject_invalid_boolean(client, headers, api, path):
    response = client.post(f"/pytrain/v1/system/{path}", headers=headers, params={"on": "perhaps"})
    assert response.status_code == 422
    error = response.json()["detail"][0]
    assert error["loc"] == ["query", "on"]
    assert error["type"] == "bool_parsing"
    api.pytrain.queue_command.assert_not_called()


@pytest.mark.parametrize("path", ["stop_all", "stop_all_req"])
def test_stop_all_dispatches_engine_and_train_broadcasts(client, headers, monkeypatch, path):
    engine_command = MagicMock(spec=endpoints.CommandReq)
    train_command = MagicMock(spec=endpoints.CommandReq)
    factory = MagicMock(side_effect=[engine_command, train_command])
    dispatch = MagicMock()
    dispatch.attach_mock(factory, "construct")
    dispatch.attach_mock(engine_command.send, "engine_send")
    dispatch.attach_mock(train_command.send, "train_send")
    monkeypatch.setattr(endpoints, "CommandReq", factory)
    response = client.post(f"/pytrain/v1/system/{path}", headers=headers)
    assert response.status_code == 200
    assert response.json() == {"status": "Sent 'stop' command to all engines and trains..."}
    assert dispatch.mock_calls == [
        call.construct(endpoints.TMCC1EngineCommandEnum.STOP_IMMEDIATE, 99),
        call.engine_send(),
        call.construct(endpoints.TMCC2EngineCommandEnum.STOP_IMMEDIATE, 99, scope=endpoints.CommandScope.TRAIN),
        call.train_send(),
    ]


@pytest.mark.parametrize("path", ["debug", "debug_req", "echo", "echo_req", "stop_all", "stop_all_req"])
def test_unhandled_system_dispatch_errors_currently_return_500(headers, api, system_command, path):
    api.pytrain.queue_command.side_effect = RuntimeError("queue unavailable")
    factory, command = system_command
    command.send.side_effect = RuntimeError("send unavailable")
    client = TestClient(endpoints.app, raise_server_exceptions=False)
    response = client.post(f"/pytrain/v1/system/{path}", headers=headers)
    assert response.status_code == 500
    assert response.text == "Internal Server Error"
    if path.startswith("stop_all"):
        factory.assert_called_once_with(endpoints.TMCC1EngineCommandEnum.STOP_IMMEDIATE, 99)
        command.send.assert_called_once_with()
        api.pytrain.queue_command.assert_not_called()
    else:
        factory.assert_not_called()
        api.pytrain.queue_command.assert_called_once_with(f"{path.removesuffix('_req')} on")


@pytest.mark.parametrize("path", ["version", "version_req"])
def test_system_version_aliases_return_both_versions(client, headers, monkeypatch, path):
    train_version = MagicMock(return_value="2.3.4")
    api_version = MagicMock(return_value="5.6.7")
    monkeypatch.setattr(endpoints, "pytrain_get_version", train_version)
    monkeypatch.setattr(pytrain_api, "get_version", api_version)
    response = client.post(f"/pytrain/v1/system/{path}", headers=headers)
    assert response.status_code == 200
    assert response.json() == {"pytrain": "2.3.4", "pytrain_api": "5.6.7"}
    train_version.assert_called_once_with()
    api_version.assert_called_once_with()


@pytest.mark.parametrize("path", ["version", "version_req"])
@pytest.mark.parametrize("source", ["pytrain", "api"])
def test_system_version_errors_return_400(client, headers, monkeypatch, path, source):
    version = MagicMock(side_effect=RuntimeError("version unavailable"))
    if source == "pytrain":
        monkeypatch.setattr(endpoints, "pytrain_get_version", version)
    else:
        monkeypatch.setattr(pytrain_api, "get_version", version)
    response = client.post(f"/pytrain/v1/system/{path}", headers=headers)
    assert response.status_code == 400
    assert response.json() == {"detail": "version unavailable"}
    version.assert_called_once_with()


@pytest.mark.parametrize("component", ["engine", "train", "switch", "accessory", "route", "block"])
@pytest.mark.parametrize("tmcc_id", [1, 99])
@pytest.mark.parametrize("is_tmcc", [None, "", "false"])
def test_cli_formats_and_sends_parsed_command(client, headers, api, component, tmcc_id, is_tmcc):
    command = MagicMock(spec=endpoints.CommandReq)
    api.pytrain.parse_cli.return_value = command
    params = {"command": "-speed 12"}
    if is_tmcc is not None:
        params["is_tmcc"] = is_tmcc
    tmcc = " -tmcc" if is_tmcc is not None and component in {"engine", "train"} else ""
    expected = f"{component} {tmcc_id}{tmcc} -speed 12"
    response = client.post(f"/pytrain/v1/{component}/{tmcc_id}/cli_req", headers=headers, params=params)
    assert response.status_code == 200
    assert response.json() == {"status": f"'{expected}' command sent"}
    api.pytrain.parse_cli.assert_called_once_with(expected)
    command.send.assert_called_once_with()


@pytest.mark.parametrize("parse_result", [None, "unrecognized option", False])
def test_cli_rejects_non_command_parse_results(client, headers, api, parse_result):
    api.pytrain.parse_cli.return_value = parse_result
    response = client.post("/pytrain/v1/engine/1/cli_req", headers=headers, params={"command": "-bad"})
    assert response.status_code == 400
    assert response.json() == {"detail": f"Command is invalid: {parse_result}"}
    api.pytrain.parse_cli.assert_called_once_with("engine 1 -bad")


@pytest.mark.parametrize("failure", ["parse", "send", "http"])
def test_cli_propagates_http_errors_and_translates_runtime_errors(client, headers, api, failure):
    command = MagicMock(spec=endpoints.CommandReq)
    api.pytrain.parse_cli.return_value = command
    if failure == "parse":
        api.pytrain.parse_cli.side_effect = RuntimeError("parse failed")
    elif failure == "send":
        command.send.side_effect = RuntimeError("send failed")
    else:
        api.pytrain.parse_cli.side_effect = HTTPException(status_code=409, detail="command conflict")
    response = client.post("/pytrain/v1/train/2/cli_req", headers=headers, params={"command": "-stop"})
    assert response.status_code == (409 if failure == "http" else 400)
    assert response.json() == {"detail": "command conflict" if failure == "http" else f"{failure} failed"}
    api.pytrain.parse_cli.assert_called_once_with("train 2 -stop")
    if failure == "send":
        command.send.assert_called_once_with()
    else:
        command.send.assert_not_called()


@pytest.mark.parametrize(
    ("path", "params", "location", "error_type"),
    [
        ("engine/0", {"command": "-stop"}, ["path", "tmcc_id"], "greater_than_equal"),
        ("engine/100", {"command": "-stop"}, ["path", "tmcc_id"], "less_than_equal"),
        ("unknown/1", {"command": "-stop"}, ["path", "component"], "enum"),
        ("engine/1", {}, ["query", "command"], "missing"),
    ],
)
def test_cli_validation_prevents_dispatch(client, headers, api, path, params, location, error_type):
    response = client.post(f"/pytrain/v1/{path}/cli_req", headers=headers, params=params)
    assert response.status_code == 422
    assert len(response.json()["detail"]) == 1
    error = response.json()["detail"][0]
    assert error["loc"] == location
    assert error["type"] == error_type
    api.pytrain.parse_cli.assert_not_called()


@pytest.mark.parametrize("tmcc_id", ["-1", "abc", "1.5"])
def test_cli_noninteger_route_is_hidden(client, headers, api, tmcc_id):
    response = client.post(f"/pytrain/v1/engine/{tmcc_id}/cli_req", headers=headers, params={"command": "-stop"})
    assert response.status_code == 403
    assert response.json() == {"detail": "Forbidden"}
    api.pytrain.parse_cli.assert_not_called()


def test_cli_requires_authentication(client, api):
    response = client.post("/pytrain/v1/engine/1/cli_req", params={"command": "-stop"})
    assert response.status_code == 401
    assert response.json() == {"detail": "Not authenticated"}
    api.pytrain.parse_cli.assert_not_called()


@pytest.mark.parametrize(
    ("name", "operation", "summary", "category"),
    [
        ("System.StopAll", "system_stop_all", "System Stop All", "System"),
        ("", "", "", "Misc"),
        ("System..Version", "system__version", "System Version", "System"),
    ],
)
def test_route_names_generate_metadata(name, operation, summary, category):
    assert endpoints._operation_id_from_name(name) == operation
    assert endpoints._summary_from_name(name) == summary
    assert endpoints.infer_category(name) == category


def test_response_metadata_merge_precedence_and_nonmutation():
    base = {200: {"description": "base"}, 404: {"description": "base missing"}}
    user = {400: {"description": "custom bad request"}}
    result = endpoints._merge_responses(
        user, success_model=endpoints.SuccessResponse, base=base, error_codes=(403, 404, 599)
    )
    assert result == {
        200: {"description": "base"},
        400: {"description": "custom bad request"},
        401: endpoints.BASE_ERROR_RESPONSES[401],
        403: endpoints.OPTIONAL_ERROR_RESPONSES[403],
        404: endpoints.OPTIONAL_ERROR_RESPONSES[404],
        498: endpoints.BASE_ERROR_RESPONSES[498],
    }
    assert base == {200: {"description": "base"}, 404: {"description": "base missing"}}
    assert user == {400: {"description": "custom bad request"}}
    assert endpoints._merge_responses() == {}
    assert endpoints._error_responses_for(None) == endpoints.BASE_ERROR_RESPONSES


@pytest.mark.parametrize(("helper", "label"), [(endpoints.mobile_post, "Mobile"), (endpoints.legacy_post, "Legacy")])
@pytest.mark.parametrize("explicit", [False, True])
def test_post_route_helpers_register_response_and_documentation_metadata(helper, label, explicit):
    router = APIRouter()
    options = (
        {
            "operation_id": "custom_id",
            "summary": "Custom summary",
            "category": "Custom",
            "response_model": endpoints.VersionResponse,
        }
        if explicit
        else {}
    )

    def handler():
        return {"status": "ok"}

    decorated = helper(router, "/example", name="System.TestRoute", errors=(404,), **options)(handler)
    route = router.routes[0]
    assert decorated is handler
    assert route.path == "/example"
    assert route.methods == {"POST"}
    assert route.tags == [f"{label}.{'Custom' if explicit else 'System'}"]
    assert route.operation_id == ("custom_id" if explicit else "system_test_route")
    assert route.summary == ("Custom summary" if explicit else "System Test Route")
    assert route.response_model is (endpoints.VersionResponse if explicit else endpoints.SuccessResponse)
    assert set(route.responses) == {200, 400, 401, 404, 498}
    assert route.responses[200]["model"] is route.response_model


@pytest.mark.parametrize("include_404", [False, True])
@pytest.mark.parametrize("include_400", [False, True])
def test_get_route_helper_optional_errors_and_overrides(include_404, include_400):
    router = APIRouter()

    def handler():
        return {"status": "ok"}

    endpoints.api_get(
        router,
        "/example",
        name="System.TestRoute",
        include_404=include_404,
        include_400=include_400,
        responses={401: {"description": "custom auth"}},
    )(handler)
    route = router.routes[0]
    assert route.methods == {"GET"}
    assert route.tags == ["Legacy.System", "Mobile.System"]
    assert route.operation_id == "system_test_route"
    assert route.summary == "System Test Route"
    assert route.response_model is endpoints.SuccessResponse
    assert set(route.responses) == {200, 401, 498} | ({404} if include_404 else set()) | (
        {400} if include_400 else set()
    )
    assert route.responses[401] == {"description": "custom auth"}


@pytest.mark.parametrize("helper", [endpoints.mobile_post, endpoints.legacy_post, endpoints.api_get])
def test_route_helpers_can_disable_default_response_model(helper):
    router = APIRouter()

    def handler():
        return {"anything": True}

    helper(router, "/example", name="System.TestRoute", default_success_model=None)(handler)
    route = router.routes[0]
    assert route.response_model is None
    assert 200 not in route.responses


def test_get_route_helper_explicit_metadata_and_model():
    router = APIRouter()

    def handler():
        return {"pytrain": "1", "pytrain_api": "2"}

    endpoints.api_get(
        router,
        "/example",
        name="System.TestRoute",
        operation_id="custom_id",
        summary="Custom summary",
        category="Custom",
        response_model=endpoints.VersionResponse,
        labels=["Private"],
    )(handler)
    route = router.routes[0]
    assert route.tags == ["Private.Custom"]
    assert route.operation_id == "custom_id"
    assert route.summary == "Custom summary"
    assert route.response_model is endpoints.VersionResponse
    assert route.responses[200]["model"] is endpoints.VersionResponse
