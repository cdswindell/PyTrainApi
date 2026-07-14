#
#  PyTrainApi: a restful API for controlling Lionel Legacy engines, trains, switches, and accessories
#
#  Copyright (c) 2024-2025 Dave Swindell <pytraininfo.gmail.com>
#
#  SPDX-License-Identifier: LPGL
#
#

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest
from fastapi import HTTPException
from pytrain import CommandReq, CommandScope, TMCC1AuxCommandEnum, TMCC1EngineCommandEnum, TMCC2EngineCommandEnum

from src.pytrain_api.pytrain_component import OnOffOption, PyTrainAccessory, PyTrainComponent, PyTrainEngine


class TestPyTrainEngine:
    def setup_method(self):
        self.engine = PyTrainEngine(scope=CommandScope.ENGINE)

    def test_prefix_with_engine_scope(self):
        assert self.engine.prefix == "engine"

    def test_prefix_with_train_scope(self):
        self.engine = PyTrainEngine(scope=CommandScope.TRAIN)
        assert self.engine.prefix == "train"

    def test_is_tmcc_returns_true_for_non_component_state(self, monkeypatch):
        self.engine._state_store = MagicMock()
        self.engine._state_store.query.return_value = object()

        assert self.engine.is_tmcc(17) is True

    def test_numeric_uses_tmcc1_when_is_tmcc(self, monkeypatch):
        called: dict[str, object] = {}

        def fake_do_numeric(cmd, tmcc_id, number, duration):
            called.update(cmd=cmd, tmcc_id=tmcc_id, number=number, duration=duration)
            return {"status": "ok"}

        monkeypatch.setattr(self.engine, "is_tmcc", lambda _tmcc_id: True)
        monkeypatch.setattr(self.engine, "do_numeric", fake_do_numeric)

        result = self.engine.numeric(12, 4, 1.25)

        assert result == {"status": "ok"}
        assert called == {
            "cmd": TMCC1EngineCommandEnum.NUMERIC,
            "tmcc_id": 12,
            "number": 4,
            "duration": 1.25,
        }

    def test_numeric_uses_tmcc2_when_not_tmcc(self, monkeypatch):
        called: dict[str, object] = {}

        def fake_do_numeric(cmd, tmcc_id, number, duration):
            called.update(cmd=cmd, tmcc_id=tmcc_id, number=number, duration=duration)
            return {"status": "ok"}

        monkeypatch.setattr(self.engine, "is_tmcc", lambda _tmcc_id: False)
        monkeypatch.setattr(self.engine, "do_numeric", fake_do_numeric)

        self.engine.numeric(12, 7, None)

        assert called["cmd"] == TMCC2EngineCommandEnum.NUMERIC
        assert called["tmcc_id"] == 12
        assert called["number"] == 7

    def test_get_engine_info_returns_prod_info_for_bt_id(self, monkeypatch):
        self.engine._state_store = MagicMock()
        self.engine._state_store.query.return_value = SimpleNamespace(bt_id="BT-001")

        monkeypatch.setattr(
            "src.pytrain_api.pytrain_component.EngineState",
            type("FakeEngineState", (), {}),
        )
        fake_state_type = __import__("src.pytrain_api.pytrain_component", fromlist=["EngineState"]).EngineState
        self.engine._state_store.query.return_value = fake_state_type()
        self.engine._state_store.query.return_value.bt_id = "BT-001"

        prod_info = {"road_name": "Lionel", "product_id": "6-12345"}
        monkeypatch.setattr(
            "src.pytrain_api.pytrain_component.ProdInfo.get_info", lambda bt_id: prod_info if bt_id else None
        )

        assert self.engine.get_engine_info(7) == prod_info


class TestPyTrainComponent:
    def setup_method(self):
        self.component = PyTrainComponent(scope=CommandScope.ENGINE)

    def test_get_raises_not_found_when_state_missing(self):
        self.component._state_store = MagicMock()
        self.component._state_store.query.return_value = None

        with pytest.raises(HTTPException) as exc:
            self.component.get(22)

        assert exc.value.status_code == 404
        assert exc.value.headers["X-Error"] == "404"

    def test_do_request_builds_and_sends_with_normalized_repeat_delay_duration(self, monkeypatch):
        fake_req = MagicMock()
        build_calls: list[tuple] = []

        def fake_build(cmd_def, tmcc_id, data, scope):
            build_calls.append((cmd_def, tmcc_id, data, scope))
            return fake_req

        monkeypatch.setattr(CommandReq, "build", fake_build)

        req = self.component.do_request(
            TMCC1EngineCommandEnum.FORWARD_DIRECTION, tmcc_id=5, repeat=0, delay=None, duration=None
        )

        assert req is fake_req
        assert build_calls == [(TMCC1EngineCommandEnum.FORWARD_DIRECTION, 5, None, CommandScope.ENGINE)]
        fake_req.send.assert_called_once_with(repeat=1, delay=0, duration=0)

    def test_do_request_wraps_exceptions_as_http_400(self, monkeypatch):
        monkeypatch.setattr(
            CommandReq, "build", lambda *_args, **_kwargs: (_ for _ in ()).throw(ValueError("bad command"))
        )

        with pytest.raises(HTTPException) as exc:
            self.component.do_request(TMCC1EngineCommandEnum.FORWARD_DIRECTION, tmcc_id=1)

        assert exc.value.status_code == 400
        assert "bad command" in exc.value.detail


class TestPyTrainAccessory:
    def setup_method(self):
        self.accessory = PyTrainAccessory(scope=CommandScope.ACC)

    def test_enforce_strict_raises_not_found(self):
        self.accessory._state_store = MagicMock()
        self.accessory._state_store.query.return_value = None

        with pytest.raises(HTTPException) as exc:
            self.accessory.enforce_strict(9, "AMC2", lambda _state: True)

        assert exc.value.status_code == 404

    def test_amc2_motor_with_state_sends_numeric_then_onoff(self, monkeypatch):
        calls: list[tuple] = []

        def fake_do_request(cmd, tmcc_id, **kwargs):
            calls.append((cmd, tmcc_id, kwargs))

        monkeypatch.setattr(self.accessory, "do_request", fake_do_request)

        self.accessory.amc2_motor(4, motor=2, state=OnOffOption.ON, speed=None)

        assert calls[0] == (TMCC1AuxCommandEnum.NUMERIC, 4, {"data": 2})
        assert calls[1] == (TMCC1AuxCommandEnum.AUX1_OPT_ONE, 4, {})

    def test_amc2_motor_requires_state_or_speed(self):
        with pytest.raises(HTTPException) as exc:
            self.accessory.amc2_motor(4, motor=1, state=None, speed=None)

        assert exc.value.status_code == 422
        assert "Must specify either motor state or speed" in exc.value.detail
