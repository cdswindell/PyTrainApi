from __future__ import annotations

import pytest

from .test_endpoint_commands import COMMAND_CASES
from .test_endpoint_reads import READ_CASES, json_state


COMMAND_ROUTES = list({case.url: case for case in reversed(COMMAND_CASES)}.values())
BODY_ROUTES = [
    (component, action) for component in ("engine", "train", "accessory") for action in ("aux", "numeric", "speed")
] + [(component, action) for component in ("engine", "train") for action in ("bell", "horn")]
BODY_ROUTES += [("accessory", action) for action in ("amc2_motor", "amc2_lamp", "asc2", "bpc")]


def assert_rejected(response, components, location):
    assert response.status_code == 422, response.text
    errors = response.json()["detail"]
    assert isinstance(errors, list) and errors
    assert any(error["loc"][0] == location for error in errors)
    assert all(component.mock_calls == [] for component in components.values())


@pytest.mark.parametrize("case", COMMAND_ROUTES, ids=lambda case: f"{case.component}/{case.path}")
@pytest.mark.parametrize("key", [None, "incorrect-api-key"])
def test_component_commands_require_authentication(client, components, case, key):
    response = client.post(case.url, headers={} if key is None else {"X-API-Key": key}, **case.request)
    assert response.status_code == 401
    assert response.json() == {"detail": "Not authenticated" if key is None else "Missing or invalid API key"}
    assert all(component.mock_calls == [] for component in components.values())


@pytest.mark.parametrize(
    "path", [path for component, plural, _, _ in READ_CASES for path in (plural, f"{component}/7")] + ["engine/7/info"]
)
@pytest.mark.parametrize("key", [None, "incorrect-api-key"])
def test_component_reads_require_authentication(client, api, components, path, key):
    response = client.get(f"/pytrain/v1/{path}", headers={} if key is None else {"X-API-Key": key})
    assert response.status_code == 401
    assert response.json() == {"detail": "Not authenticated" if key is None else "Missing or invalid API key"}
    assert all(component.mock_calls == [] for component in components.values())
    api.pytrain.store.query.assert_not_called()


@pytest.mark.parametrize("case", COMMAND_ROUTES, ids=lambda case: f"{case.component}/{case.path}")
@pytest.mark.parametrize("boundary", ["below", "above"])
def test_commands_reject_out_of_range_ids(client, headers, components, case, boundary):
    tmcc_id = 0 if boundary == "below" else (10000 if case.component in ("engine", "train") else 100)
    response = client.post(case.url.replace("/7/", f"/{tmcc_id}/"), headers=headers, **case.request)
    assert_rejected(response, components, "path")


@pytest.mark.parametrize("component,plural,scope,data", READ_CASES)
@pytest.mark.parametrize("boundary", ["below", "above"])
def test_reads_reject_out_of_range_ids(client, headers, components, component, plural, scope, data, boundary):
    tmcc_id = 0 if boundary == "below" else (10000 if component in ("engine", "train") else 100)
    response = client.get(f"/pytrain/v1/{component}/{tmcc_id}", headers=headers)
    assert_rejected(response, components, "path")


@pytest.mark.parametrize("component,plural,scope,data", READ_CASES)
@pytest.mark.parametrize("boundary", ["lower", "upper"])
def test_reads_accept_id_boundaries(client, headers, components, component, plural, scope, data, boundary):
    tmcc_id = 1 if boundary == "lower" else (9999 if component in ("engine", "train") else 99)
    record = {**data, "block_id" if component == "block" else "tmcc_id": tmcc_id}
    components[component].get.return_value = record.copy()
    response = client.get(f"/pytrain/v1/{component}/{tmcc_id}", headers=headers)
    assert response.status_code == 200
    assert response.json() == json_state(record)
    components[component].get.assert_called_once_with(tmcc_id)


@pytest.mark.parametrize("component,action", BODY_ROUTES)
@pytest.mark.parametrize("body", [None, {}, [], "invalid"])
def test_required_command_bodies(client, headers, components, component, action, body):
    response = client.post(f"/pytrain/v1/{component}/7/{action}", headers=headers, json=body)
    assert_rejected(response, components, "body")


@pytest.mark.parametrize("component,action", BODY_ROUTES)
def test_malformed_json(client, headers, components, component, action):
    response = client.post(
        f"/pytrain/v1/{component}/7/{action}",
        headers={**headers, "Content-Type": "application/json"},
        content='{"unfinished":',
    )
    assert_rejected(response, components, "body")


INVALID_BODIES = [
    ("aux", {"aux_req": "aux4"}),
    ("aux", {"aux_req": "aux1", "number": -1}),
    ("aux", {"aux_req": "aux1", "number": 10}),
    ("aux", {"aux_req": "aux1", "duration": 0}),
    ("aux", {"aux_req": "aux1", "unexpected": True}),
    ("numeric", {"number": -1}),
    ("numeric", {"number": 10}),
    ("numeric", {"number": "invalid"}),
    ("numeric", {"number": 1, "duration": -1}),
    ("numeric", {"number": 1, "unexpected": True}),
]
MOTIVE_INVALID_BODIES = [
    ("bell", {"option": "invalid"}),
    ("bell", {"option": "once", "duration": 0}),
    ("bell", {"option": "ding", "ding": -1}),
    ("bell", {"option": "ding", "ding": 4}),
    ("bell", {"option": "toggle", "duration": 1}),
    ("horn", {"option": "invalid"}),
    ("horn", {"option": "quilling", "intensity": -1}),
    ("horn", {"option": "quilling", "intensity": 16}),
    ("horn", {"option": "sound", "duration": 0}),
    ("horn", {"option": "grade", "duration": 1}),
    ("reset", {"duration": 0}),
    ("reset", {"duration": -1}),
    ("reset", {"hold": "invalid"}),
] + [("speed", {"speed": value}) for value in (-1, 196, 1.5, True, "10", "fast", None)]
ACCESSORY_INVALID_BODIES = [
    ("amc2_motor", {"mode": "speed", "motor": 0, "speed": 1}),
    ("amc2_motor", {"mode": "speed", "motor": 3, "speed": 1}),
    ("amc2_motor", {"mode": "speed", "motor": 1, "speed": -1}),
    ("amc2_motor", {"mode": "speed", "motor": 1, "speed": 101}),
    ("amc2_motor", {"mode": "speed", "motor": 1, "state": "on"}),
    ("amc2_motor", {"mode": "state", "motor": 1, "state": "on", "speed": 1}),
    ("amc2_motor", {"mode": "invalid", "motor": 1, "speed": 1}),
    ("amc2_lamp", {"mode": "level", "lamp": 0, "level": 1}),
    ("amc2_lamp", {"mode": "level", "lamp": 5, "level": 1}),
    ("amc2_lamp", {"mode": "level", "lamp": 1, "level": -1}),
    ("amc2_lamp", {"mode": "level", "lamp": 1, "level": 101}),
    ("amc2_lamp", {"mode": "level", "lamp": 1, "state": "on"}),
    ("amc2_lamp", {"mode": "state", "lamp": 1, "state": "on", "level": 1}),
    ("amc2_lamp", {"mode": "invalid", "lamp": 1, "level": 1}),
    ("asc2", {"state": "invalid"}),
    ("asc2", {"state": "on", "duration": 0}),
    ("asc2", {"state": "on", "extra": 1}),
    ("bpc", {"state": "invalid"}),
    ("bpc", {"state": "on", "extra": 1}),
    ("speed", {"speed": -6}),
    ("speed", {"speed": 6}),
    ("speed", {"speed": 0, "duration": 0}),
    ("speed", {"speed": 0, "extra": 1}),
]
BODY_FAILURES = [
    (component, action, body) for component in ("engine", "train", "accessory") for action, body in INVALID_BODIES
]
BODY_FAILURES += [
    (component, action, body) for component in ("engine", "train") for action, body in MOTIVE_INVALID_BODIES
]
BODY_FAILURES += [("accessory", action, body) for action, body in ACCESSORY_INVALID_BODIES]


@pytest.mark.parametrize("component,action,body", BODY_FAILURES)
def test_invalid_command_fields(client, headers, components, component, action, body):
    response = client.post(f"/pytrain/v1/{component}/7/{action}", headers=headers, json=body)
    assert_rejected(response, components, "body")


QUERY_FAILURES = []
for component in ("engine", "train", "accessory"):
    for suffix in ("", "_req"):
        for action in ("boost", "brake"):
            for duration in (0, -1, "invalid"):
                QUERY_FAILURES.append((component, action + suffix, {"duration": duration}))
    for number in (-1, 10, "invalid"):
        QUERY_FAILURES.append((component, "numeric_req", {"number": number}))
    QUERY_FAILURES.append((component, "numeric_req", {"number": 1, "duration": 0}))
for component in ("engine", "train"):
    for suffix in ("", "_req"):
        for action, field_name, values in (
            ("momentum", "level", (-1, 8, "invalid")),
            ("smoke", "level", ("invalid",)),
            ("dialog", "dialog" if component == "engine" else "option", ("invalid",)),
            ("startup", "dialog", ("invalid",)),
            ("shutdown", "dialog", ("invalid",)),
        ):
            if action in ("momentum", "smoke", "dialog"):
                QUERY_FAILURES.append((component, action + suffix, {}))
            for value in values:
                QUERY_FAILURES.append((component, action + suffix, {field_name: value}))
    for option in (
        {},
        {"option": "invalid"},
        {"option": "quilling", "intensity": -1},
        {"option": "quilling", "intensity": 16},
        {"option": "sound", "duration": 0},
    ):
        QUERY_FAILURES.append((component, "horn_req", option))
    QUERY_FAILURES.append((component, "bell_req", {"option": "invalid"}))
    QUERY_FAILURES.append((component, "reset_req", {"hold": True, "duration": 2}))
for action, field_name, values, valid in (
    ("amc2_motor_req", "motor", (0, 3), {"speed": 10}),
    ("amc2_motor_req", "speed", (-1, 101), {"motor": 1}),
    ("amc2_lamp_req", "lamp", (0, 5), {"level": 10}),
    ("amc2_lamp_req", "level", (-1, 101), {"lamp": 1}),
    ("asc2_req", "state", ("invalid",), {}),
    ("bpc2_req", "state", ("invalid",), {}),
):
    QUERY_FAILURES.extend(("accessory", action, {**valid, field_name: value}) for value in values)
for action in ("amc2_motor_req", "amc2_lamp_req", "asc2_req", "bpc2_req"):
    QUERY_FAILURES.append(("accessory", action, {}))
for action in ("throw", "throw_req"):
    QUERY_FAILURES.extend(("switch", action, params) for params in ({}, {"position": "invalid"}))


@pytest.mark.parametrize("component,action,params", QUERY_FAILURES)
def test_invalid_command_queries(client, headers, components, component, action, params):
    response = client.post(f"/pytrain/v1/{component}/7/{action}", headers=headers, params=params)
    assert_rejected(response, components, "query")


@pytest.mark.parametrize("speed", [-6, 6, "invalid"])
def test_relative_speed_path_bounds(client, headers, components, speed):
    response = client.post(f"/pytrain/v1/accessory/7/speed_req/{speed}", headers=headers)
    assert_rejected(response, components, "path")


@pytest.mark.parametrize("component", ["engine", "train", "accessory"])
def test_invalid_aux_path(client, headers, components, component):
    response = client.post(f"/pytrain/v1/{component}/7/aux4", headers=headers)
    assert_rejected(response, components, "path")


@pytest.mark.parametrize("plural", ["engines", "trains"])
@pytest.mark.parametrize("field_name", ["is_legacy", "is_tmcc"])
def test_invalid_list_filters_do_not_query_store(client, headers, api, plural, field_name):
    response = client.get(f"/pytrain/v1/{plural}", headers=headers, params={field_name: "invalid"})
    assert response.status_code == 422
    assert response.json()["detail"][0]["loc"] == ["query", field_name]
    api.pytrain.store.query.assert_not_called()
