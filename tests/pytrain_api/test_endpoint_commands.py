from __future__ import annotations

from dataclasses import dataclass, field
from unittest.mock import AsyncMock, call

import pytest
from fastapi import HTTPException
from pytrain import TMCC1AuxCommandEnum, TMCC1RouteCommandEnum

from src.pytrain_api import endpoints
from src.pytrain_api.pytrain_component import (
    AuxOption,
    BellOption,
    DialogOption,
    HornOption,
    OnOffOption,
    SmokeOption,
    SwitchPosition,
)
from src.pytrain_api.response_models import ok_response


@dataclass
class CommandCase:
    component: str
    path: str
    method: str
    args: tuple = (7,)
    kwargs: dict = field(default_factory=dict)
    request: dict = field(default_factory=dict)

    @property
    def url(self):
        return f"/pytrain/v1/{self.component}/7/{self.path}"


def motive_cases(component):
    cases = []
    for suffix in ("", "_req"):
        for action in (
            "forward",
            "reverse",
            "front_coupler",
            "rear_coupler",
            "stop",
            "toggle_direction",
            "volume_up",
            "volume_down",
        ):
            cases.append(CommandCase(component, action + suffix, action))
        for action in ("boost", "brake"):
            for duration in (None, 1.5):
                cases.append(
                    CommandCase(
                        component,
                        action + suffix,
                        action,
                        (7, duration),
                        request={"params": {} if duration is None else {"duration": duration}},
                    )
                )
        for action in ("startup", "shutdown"):
            for dialog in (False, True):
                cases.append(
                    CommandCase(
                        component,
                        action + suffix,
                        action,
                        kwargs={"dialog": dialog},
                        request={"params": {"dialog": dialog}} if dialog else {},
                    )
                )
        for level in (0, 7):
            cases.append(
                CommandCase(
                    component,
                    "momentum" + suffix,
                    "momentum",
                    (7, level),
                    request={"params": {"level": level}},
                )
            )
        for option in DialogOption:
            cases.append(
                CommandCase(
                    component,
                    "dialog" + suffix,
                    "dialog",
                    (7, option),
                    request={"params": {"dialog" if component == "engine" else "option": option.value}},
                )
            )
    for path in ("smoke", "smoke_req", "smoke_level_req"):
        for level in SmokeOption:
            cases.append(
                CommandCase(
                    component,
                    path,
                    "smoke",
                    kwargs={"level": level},
                    request={"params": {"level": level.value}},
                )
            )
    for option in BellOption:
        body = {"option": option.value}
        duration = 1.5 if option == BellOption.ONCE else None
        ding = 3 if option == BellOption.DING else None
        if duration is not None:
            body["duration"] = duration
        if ding is not None:
            body["ding"] = ding
        cases.append(CommandCase(component, "bell", "ring_bell", (7, option, duration, ding), request={"json": body}))
        cases.append(
            CommandCase(
                component,
                "bell_req",
                "ring_bell",
                (7, option, None),
                request={"params": {"option": option.value}},
            )
        )
    cases.append(CommandCase(component, "bell_req", "ring_bell", (7, None, None)))
    for option in HornOption:
        intensity = 10 if option == HornOption.QUILLING else None
        cases.append(
            CommandCase(
                component,
                "horn",
                "blow_horn",
                (7, option, intensity, None),
                request={"json": {"option": option.value}},
            )
        )
        cases.append(
            CommandCase(
                component,
                "horn_req",
                "blow_horn",
                (7, option, 10, None),
                request={"params": {"option": option.value}},
            )
        )
    for intensity in (0, 15):
        for path, location in (("horn", "json"), ("horn_req", "params")):
            cases.append(
                CommandCase(
                    component,
                    path,
                    "blow_horn",
                    (7, HornOption.QUILLING, intensity, 2.5),
                    request={location: {"option": "quilling", "intensity": intensity, "duration": 2.5}},
                )
            )
    for number in (0, 9):
        for path, location in (("numeric", "json"), ("numeric_req", "params")):
            cases.append(
                CommandCase(
                    component,
                    path,
                    "numeric",
                    (7, number, 1.5),
                    request={location: {"number": number, "duration": 1.5}},
                )
            )
    for option in AuxOption:
        cases.append(
            CommandCase(
                component,
                "aux",
                "aux",
                (7, option, 9, 1.5),
                request={"json": {"aux_req": option.value, "number": 9, "duration": 1.5}},
            )
        )
        cases.append(
            CommandCase(
                component,
                option.value,
                "aux",
                (7, option, 9, 1.5),
                request={"params": {"number": 9, "duration": 1.5}},
            )
        )
    for body, duration in (
        (None, None),
        ({}, None),
        ({"hold": False, "duration": 5}, None),
        ({"hold": True}, 3),
        ({"hold": True, "duration": 1}, 3),
        ({"hold": True, "duration": 5}, 5),
    ):
        cases.append(CommandCase(component, "reset", "reset", kwargs={"duration": duration}, request={"json": body}))
    for params, duration in (({}, None), ({"hold": True}, 3), ({"hold": True, "duration": 5}, 5)):
        cases.append(CommandCase(component, "reset_req", "reset", (7, duration), request={"params": params}))
    for speed in (0, 195, "stop", "roll", "restricted", "slow", "medium", "limited", "normal", "highball"):
        cases.append(
            CommandCase(
                component,
                "speed",
                "set_speed" if component == "engine" else "speed",
                (7, speed, False, False) if component == "engine" else (7, speed),
                {} if component == "engine" else {"immediate": False, "dialog": False},
                {"json": {"speed": speed}},
            )
        )
    cases.append(
        CommandCase(
            component,
            "speed",
            "set_speed" if component == "engine" else "speed",
            (7, 25, True, True) if component == "engine" else (7, 25),
            {} if component == "engine" else {"immediate": True, "dialog": True},
            {"json": {"speed": 25, "immediate": True, "dialog": True}},
        )
    )
    for speed in ("25", "slow"):
        cases.append(
            CommandCase(
                component,
                f"speed_req/{speed}",
                "speed",
                (7, speed),
                {"immediate": True, "dialog": False},
                {"params": {"immediate": True, "dialog": False}},
            )
        )
    return cases


def accessory_cases():
    cases = []
    for action, selector, limit, value_name in (("amc2_motor", "motor", 2, "speed"), ("amc2_lamp", "lamp", 4, "level")):
        for selection in (1, limit):
            for state in OnOffOption:
                cases.append(
                    CommandCase(
                        "accessory",
                        action,
                        action,
                        (7, selection, state, None),
                        {"strict": True},
                        {"json": {"mode": "state", selector: selection, "state": state.value}},
                    )
                )
            for value in (0, 100):
                cases.append(
                    CommandCase(
                        "accessory",
                        action,
                        action,
                        (7, selection, None, value),
                        {"strict": False},
                        {"json": {"mode": value_name, selector: selection, value_name: value, "strict": False}},
                    )
                )
                cases.append(
                    CommandCase(
                        "accessory",
                        action + "_req",
                        action,
                        (7, selection, None, value),
                        request={"params": {selector: selection, value_name: value}},
                    )
                )
            cases.append(
                CommandCase(
                    "accessory",
                    action + "_req",
                    action,
                    (7, selection, OnOffOption.ON, None),
                    request={"params": {selector: selection, "state": "on"}},
                )
            )
    for state in OnOffOption:
        for strict in (False, True):
            cases.append(
                CommandCase(
                    "accessory",
                    "asc2",
                    "asc2",
                    (7, state, 1.5),
                    {"strict": strict},
                    {"json": {"state": state.value, "duration": 1.5, "strict": strict}},
                )
            )
            cases.append(
                CommandCase(
                    "accessory",
                    "bpc",
                    "bpc2",
                    (7, state),
                    {"strict": strict},
                    {"json": {"state": state.value, "strict": strict}},
                )
            )
        cases.append(
            CommandCase(
                "accessory",
                "asc2_req",
                "asc2",
                (7, state, None),
                request={"params": {"state": state.value}},
            )
        )
        cases.append(
            CommandCase(
                "accessory",
                "bpc2_req",
                "bpc2",
                (7, state),
                request={"params": {"state": state.value}},
            )
        )
    for suffix in ("", "_req"):
        for action in ("boost", "brake"):
            cases.append(CommandCase("accessory", action + suffix, action, (7, None)))
            cases.append(
                CommandCase(
                    "accessory",
                    action + suffix,
                    action,
                    (7, 1.5),
                    request={"params": {"duration": 1.5}},
                )
            )
        for action, command in (
            ("front_coupler", TMCC1AuxCommandEnum.FRONT_COUPLER),
            ("rear_coupler", TMCC1AuxCommandEnum.REAR_COUPLER),
        ):
            cases.append(
                CommandCase(
                    "accessory",
                    action + suffix,
                    "open_coupler",
                    (7, command, 1.5),
                    request={"params": {"duration": 1.5}},
                )
            )
    for number in (0, 9):
        for path, location in (("numeric", "json"), ("numeric_req", "params")):
            cases.append(
                CommandCase(
                    "accessory",
                    path,
                    "do_numeric",
                    (TMCC1AuxCommandEnum.NUMERIC, 7, number, 1.5),
                    request={location: {"number": number, "duration": 1.5}},
                )
            )
    for speed in (-5, 0, 5):
        cases.append(
            CommandCase(
                "accessory",
                "speed",
                "relative_speed",
                (7, speed, 1.5),
                request={"json": {"speed": speed, "duration": 1.5}},
            )
        )
        cases.append(
            CommandCase(
                "accessory",
                f"speed_req/{speed}",
                "relative_speed",
                (7, speed, None),
            )
        )
    for option in AuxOption:
        cases.append(
            CommandCase(
                "accessory",
                "aux",
                "aux",
                (7, option, 9, 1.5),
                request={"json": {"aux_req": option.value, "number": 9, "duration": 1.5}},
            )
        )
        cases.append(
            CommandCase(
                "accessory",
                option.value,
                "aux",
                (7, option, None, 1.5),
                request={"params": {"duration": 1.5}},
            )
        )
    return cases


COMMAND_CASES = motive_cases("engine") + motive_cases("train") + accessory_cases()
for position in SwitchPosition:
    COMMAND_CASES.append(CommandCase("switch", f"{position.value}_req", "throw", (7, position)))
    for suffix in ("", "_req"):
        COMMAND_CASES.append(
            CommandCase(
                "switch",
                "throw" + suffix,
                "throw",
                (7, position),
                request={"params": {"position": position.value}},
            )
        )
for suffix in ("", "_req"):
    COMMAND_CASES.append(CommandCase("route", "fire" + suffix, "do_request", (TMCC1RouteCommandEnum.FIRE, 7)))


@pytest.mark.parametrize("case", COMMAND_CASES, ids=lambda case: f"{case.component}/{case.path}")
def test_component_command_dispatch(client, headers, components, case):
    target = components[case.component]
    method = getattr(target, case.method)
    method.return_value = ok_response("command accepted")

    response = client.post(case.url, headers=headers, **case.request)

    assert response.status_code == 200, response.text
    expected = "Route 7 fired" if case.component == "route" else "command accepted"
    assert response.json() == {"status": expected}
    assert target.mock_calls == [getattr(call, case.method)(*case.args, **case.kwargs)]
    if isinstance(method, AsyncMock):
        method.assert_awaited_once_with(*case.args, **case.kwargs)
    for name, other in components.items():
        if name != case.component:
            assert other.mock_calls == []


def test_every_component_command_route_has_a_success_case():
    routes = [
        route
        for route in endpoints.router.routes
        if "POST" in route.methods and "cli_req" not in route.path and not route.path.startswith("/pytrain/v1/system/")
    ]
    for route in routes:
        assert any(route.path_regex.fullmatch(case.url) for case in COMMAND_CASES), route.path


@pytest.mark.parametrize("case", COMMAND_CASES, ids=lambda case: f"{case.component}/{case.path}")
def test_component_command_propagates_service_errors(client, headers, components, case):
    getattr(components[case.component], case.method).side_effect = HTTPException(
        status_code=404,
        detail="Component not found",
        headers={"X-Error": "404"},
    )
    response = client.post(case.url, headers=headers, **case.request)
    assert response.status_code == 404
    assert response.json() == {"detail": "Component not found"}
