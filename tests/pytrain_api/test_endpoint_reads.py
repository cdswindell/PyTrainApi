from __future__ import annotations

from copy import deepcopy
from unittest.mock import MagicMock

import pytest
from fastapi import HTTPException
from pytrain import CommandScope


BASE_STATE = {"tmcc_id": 7, "road_name": "Union Pacific", "road_number": "4004"}
ENGINE_STATE = {
    **BASE_STATE,
    "scope": "engine",
    "bt_id": "BT-007",
    "control": "Legacy",
    "direction": "forward",
    "engine_class": "Steam",
    "engine_type": "Big Boy",
    "fuel_level": 100,
    "labor": 12,
    "max_speed": 195,
    "momentum": 3,
    "record_no": 1,
    "rpm": None,
    "smoke": "low",
    "sound_type": "RailSounds",
    "speed": 25,
    "speed_limit": 100,
    "target_speed": 30,
    "train_brake": 0,
    "water_level": 80,
    "year": 2025,
}
READ_CASES = [
    (
        "accessory",
        "accessories",
        CommandScope.ACC,
        {
            **BASE_STATE,
            "scope": "accessory",
            "type": "accessory",
            "lcs": None,
            "aux": "on",
            "aux1": None,
            "aux2": None,
        },
    ),
    (
        "block",
        "blocks",
        CommandScope.BLOCK,
        {
            "scope": "block",
            "block_id": 7,
            "name": "Yard",
            "direction": "forward",
            "sensor_track": 2,
            "switch": 3,
            "previous_block_id": None,
            "next_block_id": 8,
            "is_occupied": True,
            "occupied_by": {"scope": "engine", "tmcc_id": 7},
        },
    ),
    ("engine", "engines", CommandScope.ENGINE, ENGINE_STATE),
    (
        "route",
        "routes",
        CommandScope.ROUTE,
        {
            **BASE_STATE,
            "scope": "route",
            "active": True,
            "switches": [{"switch": 2, "position": "thru"}],
            "routes": [{"route": 3}],
        },
    ),
    ("switch", "switches", CommandScope.SWITCH, {**BASE_STATE, "scope": "switch", "state": "out"}),
    (
        "train",
        "trains",
        CommandScope.TRAIN,
        {
            **ENGINE_STATE,
            "scope": "train",
            "flags": 1,
            "components": {7: "engine"},
        },
    ),
]
PRODUCT = {
    "id": 1,
    "skuNumber": 12345,
    "blE_DecId": 7,
    "blE_HexId": "0007",
    "productFamily": 1,
    "engineClass": 2,
    "engineType": "Steam",
    "description": "Big Boy",
    "roadName": "Union Pacific",
    "roadNumber": "4004",
    "gauge": "O",
    "pmid": 3,
    "smoke": True,
    "hasOnBoardSound": True,
    "appSoundFilesAvailable": False,
    "blE_StreamingSoundsSupported": False,
    "appControlledLight": True,
    "frontCoupler": False,
    "rearCoupler": True,
    "sound": True,
    "masterVolume": True,
    "customSound": False,
    "undefinedBit": False,
    "imageUrl": "https://example.test/engine.png",
}


def state_mock(data, description="Union Pacific", is_legacy=True, is_tmcc=False):
    state = MagicMock()
    state.as_dict.side_effect = lambda: deepcopy(data)
    state.__str__.return_value = description
    state.is_legacy = is_legacy
    state.is_tmcc = is_tmcc
    return state


def json_state(data):
    expected = deepcopy(data)
    if expected.get("components") is not None:
        expected["components"] = {str(key): value for key, value in expected["components"].items()}
    return expected


@pytest.mark.parametrize("component,plural,scope,data", READ_CASES)
def test_list_serializes_state_and_queries_correct_scope(client, headers, api, component, plural, scope, data):
    api.pytrain.store.query.return_value = [state_mock(data)]
    response = client.get(f"/pytrain/v1/{plural}", headers=headers)
    assert response.status_code == 200, response.text
    assert response.json() == [json_state(data)]
    api.pytrain.store.query.assert_called_once_with(scope)


@pytest.mark.parametrize("component,plural,scope,data", READ_CASES)
def test_get_serializes_state(client, headers, components, component, plural, scope, data):
    components[component].get.return_value = deepcopy(data)
    response = client.get(f"/pytrain/v1/{component}/7", headers=headers)
    assert response.status_code == 200, response.text
    assert response.json() == json_state(data)
    components[component].get.assert_called_once_with(7)


@pytest.mark.parametrize("component,plural,scope,data", READ_CASES)
@pytest.mark.parametrize("contains", ["UNION", "pacific", "", "not present"])
def test_list_filters_text_case_insensitively(client, headers, api, component, plural, scope, data, contains):
    api.pytrain.store.query.return_value = [state_mock(data)]
    response = client.get(f"/pytrain/v1/{plural}", headers=headers, params={"contains": contains})
    if contains == "not present":
        assert response.status_code == 404
        assert response.json() == {"detail": f"No matching {scope.label} found"}
    else:
        assert response.status_code == 200
        assert response.json() == [json_state(data)]


@pytest.mark.parametrize("component,plural,scope,data", READ_CASES)
@pytest.mark.parametrize("states,matching", [(None, False), ([], True)])
def test_list_missing_states_returns_not_found(client, headers, api, component, plural, scope, data, states, matching):
    api.pytrain.store.query.return_value = states
    response = client.get(f"/pytrain/v1/{plural}", headers=headers)
    assert response.status_code == 404
    assert response.json() == {"detail": f"No {'matching ' if matching else ''}{scope.label} found"}


@pytest.mark.parametrize("component,plural,scope,data", READ_CASES)
def test_get_missing_component_preserves_not_found(client, headers, components, component, plural, scope, data):
    components[component].get.side_effect = HTTPException(
        status_code=404,
        headers={"X-Error": "404"},
        detail=f"{scope.title} 7 not found",
    )
    response = client.get(f"/pytrain/v1/{component}/7", headers=headers)
    assert response.status_code == 404
    assert response.json() == {"detail": f"{scope.title} 7 not found"}


@pytest.mark.parametrize("plural", ["engines", "trains"])
@pytest.mark.parametrize(
    "filters,ids",
    [
        ({}, [7, 8]),
        ({"is_legacy": True}, [7]),
        ({"is_legacy": False}, [8]),
        ({"is_tmcc": True}, [8]),
        ({"is_tmcc": False}, [7]),
        ({"is_legacy": True, "is_tmcc": False, "contains": "UNION"}, [7]),
        ({"is_legacy": True, "is_tmcc": True}, []),
        ({"is_tmcc": True, "contains": "UNION"}, []),
    ],
)
def test_motive_list_combines_filters(client, headers, api, plural, filters, ids):
    data = next(data for _, name, _, data in READ_CASES if name == plural)
    api.pytrain.store.query.return_value = [
        state_mock(data),
        state_mock({**data, "tmcc_id": 8}, "Santa Fe", is_legacy=False, is_tmcc=True),
    ]
    response = client.get(f"/pytrain/v1/{plural}", headers=headers, params=filters)
    assert response.status_code == (200 if ids else 404)
    if ids:
        assert [item["tmcc_id"] for item in response.json()] == ids
    else:
        assert response.json() == {"detail": f"No matching {plural[:-1].title()} found"}


@pytest.mark.parametrize("scope", ["acc", "sensor_track", "sensor track", "power_district", "power district"])
def test_accessory_normalizes_legacy_state(client, headers, components, scope):
    components["accessory"].get.return_value = {**BASE_STATE, "scope": scope, "state": "on"}
    response = client.get("/pytrain/v1/accessory/7", headers=headers)
    assert response.status_code == 200
    assert response.json() == READ_CASES[0][3]


def test_engine_product_information(client, headers, components):
    components["engine"].get_engine_info.return_value = PRODUCT.copy()
    response = client.get("/pytrain/v1/engine/7/info", headers=headers)
    assert response.status_code == 200
    assert response.json() == PRODUCT
    components["engine"].get_engine_info.assert_called_once_with(7)


def test_unavailable_product_information_returns_validation_error(client, headers, components):
    components["engine"].get_engine_info.return_value = {}
    response = client.get("/pytrain/v1/engine/7/info", headers=headers)
    assert response.status_code == 422
    assert response.json() == {"detail": "Product information not available"}


@pytest.mark.parametrize("component,plural,scope,data", READ_CASES)
def test_invalid_state_returns_validation_error(client, headers, components, component, plural, scope, data):
    components[component].get.return_value = {}
    response = client.get(f"/pytrain/v1/{component}/7", headers=headers)
    assert response.status_code == 422
    assert "Field required" in response.json()["detail"]
