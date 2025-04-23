#
#  PyTrainApi: a restful API for controlling Lionel Legacy engines, trains, switches, and accessories
#
#  Copyright (c) 2024-2025 Dave Swindell <pytraininfo.gmail.com>
#
#  SPDX-License-Identifier: LPGL
#
#

from __future__ import annotations

from enum import Enum
from typing import Any, TypeVar

from fastapi import Path, HTTPException
from pytrain import (
    CommandScope,
    CommandReq,
    TMCC1EngineCommandEnum,
    TMCC2EngineCommandEnum,
    SequenceCommandEnum,
    TMCC2EffectsControl,
    TMCC1RRSpeedsEnum,
    TMCC2RRSpeedsEnum,
    TMCC2RailSoundsDialogControl,
    EngineState,
)
from pytrain.db.component_state import ComponentState
from pytrain.db.prod_info import ProdInfo
from pytrain.protocol.command_def import CommandDefEnum
from range_key_dict import RangeKeyDict
from starlette import status

from .pytrain_api import PyTrainApi


TMCC_RR_SPEED_MAP = {
    201: TMCC1RRSpeedsEnum.ROLL,
    202: TMCC1RRSpeedsEnum.RESTRICTED,
    203: TMCC1RRSpeedsEnum.SLOW,
    204: TMCC1RRSpeedsEnum.MEDIUM,
    205: TMCC1RRSpeedsEnum.LIMITED,
    206: TMCC1RRSpeedsEnum.NORMAL,
    207: TMCC1RRSpeedsEnum.HIGHBALL,
}
LEGACY_RR_SPEED_MAP = {
    201: TMCC2RRSpeedsEnum.ROLL,
    202: TMCC2RRSpeedsEnum.RESTRICTED,
    203: TMCC2RRSpeedsEnum.SLOW,
    204: TMCC2RRSpeedsEnum.MEDIUM,
    205: TMCC2RRSpeedsEnum.LIMITED,
    206: TMCC2RRSpeedsEnum.NORMAL,
    207: TMCC2RRSpeedsEnum.HIGHBALL,
}

TMCC1_MOMENTUM_MAP = RangeKeyDict(
    {
        (0, 3): TMCC1EngineCommandEnum.MOMENTUM_LOW,
        (3, 6): TMCC1EngineCommandEnum.MOMENTUM_MEDIUM,
        (6, 8): TMCC1EngineCommandEnum.MOMENTUM_HIGH,
    }
)


class DialogOption(str, Enum):
    ENGINEER_ACK = "engineer ack"
    ENGINEER_ALL_CLEAR = "engineer all clear"
    ENGINEER_ARRIVED = "engineer arrived"
    ENGINEER_ARRIVING = "engineer arriving"
    ENGINEER_DEPARTED = "engineer departed"
    ENGINEER_DEPARTURE_DENIED = "engineer deny departure"
    ENGINEER_DEPARTURE_GRANTED = "engineer grant departure"
    ENGINEER_FUEL_LEVEL = "engineer current fuel"
    ENGINEER_FUEL_REFILLED = "engineer fuel refilled"
    ENGINEER_ID = "engineer id"
    TOWER_DEPARTURE_DENIED = "tower deny departure"
    TOWER_DEPARTURE_GRANTED = "tower grant departure"
    TOWER_RANDOM_CHATTER = "tower chatter"


E = TypeVar("E", bound=CommandDefEnum)
Tmcc1DialogToCommand: dict[DialogOption, E] = {
    DialogOption.TOWER_RANDOM_CHATTER: TMCC2EngineCommandEnum.TOWER_CHATTER,
}

Tmcc2DialogToCommand: dict[DialogOption, E] = {
    DialogOption.ENGINEER_ACK: TMCC2RailSoundsDialogControl.ENGINEER_ACK,
    DialogOption.ENGINEER_ID: TMCC2RailSoundsDialogControl.ENGINEER_ID,
    DialogOption.ENGINEER_ALL_CLEAR: TMCC2RailSoundsDialogControl.ENGINEER_ALL_CLEAR,
    DialogOption.ENGINEER_ARRIVED: TMCC2RailSoundsDialogControl.ENGINEER_ARRIVED,
    DialogOption.ENGINEER_ARRIVING: TMCC2RailSoundsDialogControl.ENGINEER_ARRIVING,
    DialogOption.ENGINEER_DEPARTURE_DENIED: TMCC2RailSoundsDialogControl.ENGINEER_DEPARTURE_DENIED,
    DialogOption.ENGINEER_DEPARTURE_GRANTED: TMCC2RailSoundsDialogControl.ENGINEER_DEPARTURE_GRANTED,
    DialogOption.ENGINEER_DEPARTED: TMCC2RailSoundsDialogControl.ENGINEER_DEPARTED,
    DialogOption.ENGINEER_FUEL_LEVEL: TMCC2RailSoundsDialogControl.ENGINEER_FUEL_LEVEL,
    DialogOption.ENGINEER_FUEL_REFILLED: TMCC2RailSoundsDialogControl.ENGINEER_FUEL_REFILLED,
    DialogOption.TOWER_DEPARTURE_DENIED: TMCC2RailSoundsDialogControl.TOWER_DEPARTURE_DENIED,
    DialogOption.TOWER_DEPARTURE_GRANTED: TMCC2RailSoundsDialogControl.TOWER_DEPARTURE_GRANTED,
    DialogOption.TOWER_RANDOM_CHATTER: TMCC2EngineCommandEnum.TOWER_CHATTER,
}


class AuxOption(str, Enum):
    AUX1 = "aux1"
    AUX2 = "aux2"
    AUX3 = "aux3"


class BellOption(str, Enum):
    TOGGLE = "toggle"
    OFF = "off"
    ON = "on"
    ONCE = "once"


class Component(str, Enum):
    ACCESSORY = "accessory"
    BLOCK = "block"
    ENGINE = "engine"
    ROUTE = "route"
    SWITCH = "switch"
    TRAIN = "train"


class HornOption(str, Enum):
    SOUND = "sound"
    GRADE = "grade"
    QUILLING = "quilling"


class OnOffOption(str, Enum):
    OFF = "off"
    ON = "on"


class SmokeOption(str, Enum):
    OFF = "off"
    ON = "on"
    LOW = "low"
    MEDIUM = "medium"
    HIGH = "high"


class PyTrainComponent:
    """
    Represents a component of the PyTrain system as exchanged via the API.

    This class provides mechanisms to interact with PyTrain API components through
    specific commands, allowing operations like query, send, and request handling.
    It also supports handling of TMCC ID paths and command queuing.

    """

    @classmethod
    def id_path(cls, label: str = None, min_val: int = 1, max_val: int = 99) -> Path:
        label = label if label else cls.__name__.replace("PyTrain", "")
        return Path(
            title="TMCC ID",
            description=f"{label}'s TMCC ID",
            ge=min_val,
            le=max_val,
        )

    def __init__(self, scope: CommandScope):
        super().__init__()
        self._scope = scope

    @property
    def scope(self) -> CommandScope:
        return self._scope

    def get(self, tmcc_id: int) -> dict[str, Any]:
        state: ComponentState = PyTrainApi.get().pytrain.store.query(self.scope, tmcc_id)
        if state is None:
            headers = {"X-Error": "404"}
            raise HTTPException(status_code=404, headers=headers, detail=f"{self.scope.title} {tmcc_id} not found")
        else:
            return state.as_dict()

    def send(self, request: E, tmcc_id: int, data: int = None) -> dict[str, Any]:
        try:
            req = CommandReq(request, tmcc_id, data, self.scope).send()
            return {"status": f"{self.scope.title} {req} sent"}
        except Exception as e:
            raise HTTPException(status_code=400, detail=str(e))

    def do_request(
        self,
        cmd_def: E | CommandReq,
        tmcc_id: int = None,
        data: int = None,
        submit: bool = True,
        repeat: int = 1,
        duration: float = 0,
        delay: float = None,
    ) -> CommandReq:
        try:
            if isinstance(cmd_def, CommandReq):
                cmd_req = cmd_def
            else:
                cmd_req = CommandReq.build(cmd_def, tmcc_id, data, self.scope)
            if submit is True:
                repeat = repeat if repeat and repeat >= 1 else 1
                duration = duration if duration is not None else 0
                delay = delay if delay is not None else 0
                cmd_req.send(repeat=repeat, delay=delay, duration=duration)
            return cmd_req
        except Exception as e:
            raise HTTPException(status_code=400, detail=str(e))

    @staticmethod
    def queue_command(cmd: str):
        PyTrainApi.get().pytrain.queue_command(cmd)


class PyTrainEngine(PyTrainComponent):
    def __init__(self, scope: CommandScope):
        super().__init__(scope=scope)

    @property
    def prefix(self) -> str:
        return "engine" if self.scope == CommandScope.ENGINE else "train"

    def is_tmcc(self, tmcc_id: int) -> bool:
        state = PyTrainApi.get().pytrain.store.query(self.scope, tmcc_id)
        if isinstance(state, ComponentState):
            return state.is_tmcc if state else True
        return True

    def tmcc(self, tmcc_id: int) -> str:
        return " -tmcc" if self.is_tmcc(tmcc_id) else ""

    def speed(self, tmcc_id: int, speed: int | str, immediate: bool = False, dialog: bool = False):
        # convert string numbers to ints
        try:
            if isinstance(speed, str) and speed.isdigit() is True:
                speed = int(speed)
        except ValueError:
            pass
        tmcc = self.tmcc(tmcc_id)
        if immediate is True:
            cmd_def = TMCC1EngineCommandEnum.ABSOLUTE_SPEED if tmcc is True else TMCC2EngineCommandEnum.ABSOLUTE_SPEED
        elif dialog is True:
            cmd_def = SequenceCommandEnum.RAMPED_SPEED_DIALOG_SEQ
        else:
            cmd_def = SequenceCommandEnum.RAMPED_SPEED_SEQ
        cmd = None
        if tmcc:
            if isinstance(speed, int):
                if speed in TMCC_RR_SPEED_MAP:
                    speed = TMCC_RR_SPEED_MAP[speed].value[0]
                    cmd = CommandReq.build(cmd_def, tmcc_id, data=speed, scope=self.scope)
                elif 0 <= speed <= 31:
                    cmd = CommandReq.build(cmd_def, tmcc_id, data=speed, scope=self.scope)
            elif isinstance(speed, str):
                cmd_def = TMCC1EngineCommandEnum.by_name(f"SPEED_{speed.upper()}", False)
                if cmd_def:
                    cmd = CommandReq.build(cmd_def, tmcc_id, scope=self.scope)
            if cmd is None:
                sc = self.scope.title
                raise HTTPException(
                    status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
                    detail=f"TMCC {sc} speeds must be between 0 and 31 inclusive: speed step {speed} is invalid.",
                )
        else:
            if isinstance(speed, int):
                if speed in LEGACY_RR_SPEED_MAP:
                    speed = LEGACY_RR_SPEED_MAP[speed].value[0]
                    cmd = CommandReq.build(cmd_def, tmcc_id, data=speed, scope=self.scope)
                elif 0 <= speed <= 199:
                    cmd = CommandReq.build(cmd_def, tmcc_id, data=speed, scope=self.scope)
            elif isinstance(speed, str):
                cmd_def = TMCC2EngineCommandEnum.by_name(f"SPEED_{speed.upper()}", False)
                if cmd_def:
                    cmd = CommandReq.build(cmd_def, tmcc_id, scope=self.scope)
            if cmd is None:
                sc = self.scope.title
                raise HTTPException(
                    status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
                    detail=f"TMCC {sc} speeds must be between 0 and 31 inclusive: speed step {speed} is invalid.",
                )
        self.do_request(cmd)
        return {"status": f"{self.scope.title} {tmcc_id} speed now: {speed}"}

    def dialog(self, tmcc_id: int, dialog: DialogOption):
        if self.is_tmcc(tmcc_id):
            cmd = Tmcc2DialogToCommand.get(dialog, None)
        else:
            cmd = Tmcc2DialogToCommand.get(dialog, None)
        if cmd:
            self.do_request(cmd, tmcc_id)
            return {"status": f"Issued dialog request '{dialog.value}' to {self.scope.title} {tmcc_id}"}
        else:
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
                detail=f"Dialog option '{dialog.value}' not supported on {self.scope.title} {tmcc_id}",
            )

    def startup(self, tmcc_id: int, dialog: bool = False):
        if self.tmcc(tmcc_id) is True:
            cmd = TMCC1EngineCommandEnum.START_UP_IMMEDIATE
        else:
            cmd = (
                TMCC2EngineCommandEnum.START_UP_DELAYED if dialog is True else TMCC2EngineCommandEnum.START_UP_IMMEDIATE
            )
        self.do_request(cmd, tmcc_id)
        return {"status": f"{self.scope.title} {tmcc_id} starting up..."}

    def shutdown(self, tmcc_id: int, dialog: bool = False):
        if self.tmcc(tmcc_id) is True:
            cmd = TMCC1EngineCommandEnum.SHUTDOWN_IMMEDIATE
        else:
            cmd = (
                TMCC2EngineCommandEnum.SHUTDOWN_DELAYED if dialog is True else TMCC2EngineCommandEnum.SHUTDOWN_IMMEDIATE
            )
        self.do_request(cmd, tmcc_id)
        return {"status": f"{self.scope.title} {tmcc_id} shutting down..."}

    def stop(self, tmcc_id: int):
        if self.is_tmcc(tmcc_id):
            self.do_request(TMCC1EngineCommandEnum.STOP_IMMEDIATE, tmcc_id)
        else:
            self.do_request(TMCC2EngineCommandEnum.STOP_IMMEDIATE, tmcc_id)
        return {"status": f"{self.scope.title} {tmcc_id} stopping..."}

    def forward(self, tmcc_id: int):
        if self.is_tmcc(tmcc_id):
            self.do_request(TMCC1EngineCommandEnum.FORWARD_DIRECTION, tmcc_id)
        else:
            self.do_request(TMCC2EngineCommandEnum.FORWARD_DIRECTION, tmcc_id)
        return {"status": f"{self.scope.title} {tmcc_id} forward..."}

    def front_coupler(self, tmcc_id: int):
        if self.is_tmcc(tmcc_id):
            self.do_request(TMCC1EngineCommandEnum.FRONT_COUPLER, tmcc_id)
        else:
            self.do_request(TMCC2EngineCommandEnum.FRONT_COUPLER, tmcc_id)
        return {"status": f"{self.scope.title} {tmcc_id} front coupler..."}

    def momentum(self, tmcc_id: int, level: int):
        if self.is_tmcc(tmcc_id):
            cmd = TMCC1_MOMENTUM_MAP.get(level, TMCC1EngineCommandEnum.MOMENTUM_LOW)
            self.do_request(cmd, tmcc_id)
        else:
            self.do_request(TMCC2EngineCommandEnum.MOMENTUM, tmcc_id, data=level)
        return {"status": f"{self.scope.title} {tmcc_id} momentum to {level}..."}

    def rear_coupler(self, tmcc_id: int):
        if self.is_tmcc(tmcc_id):
            self.do_request(TMCC1EngineCommandEnum.REAR_COUPLER, tmcc_id)
        else:
            self.do_request(TMCC2EngineCommandEnum.REAR_COUPLER, tmcc_id)
        return {"status": f"{self.scope.title} {tmcc_id} rear coupler..."}

    def reset(
        self,
        tmcc_id: int,
        duration: int = None,
    ):
        if self.is_tmcc(tmcc_id):
            self.do_request(TMCC1EngineCommandEnum.RESET, tmcc_id, duration=duration)
        else:
            self.do_request(TMCC2EngineCommandEnum.RESET, tmcc_id, duration=duration)
        return {"status": f"{self.scope.title} {tmcc_id} {'reset and refueled' if duration else 'reset'}..."}

    def reverse(self, tmcc_id: int):
        if self.is_tmcc(tmcc_id):
            self.do_request(TMCC1EngineCommandEnum.REVERSE_DIRECTION, tmcc_id)
        else:
            self.do_request(TMCC2EngineCommandEnum.REVERSE_DIRECTION, tmcc_id)
        return {"status": f"{self.scope.title} {tmcc_id} reverse..."}

    def ring_bell(self, tmcc_id: int, option: BellOption, duration: float = None):
        if self.is_tmcc(tmcc_id):
            self.do_request(TMCC1EngineCommandEnum.RING_BELL, tmcc_id)
        else:
            if option is None or option == BellOption.TOGGLE:
                self.do_request(TMCC2EngineCommandEnum.RING_BELL, tmcc_id)
            elif option == BellOption.ON:
                self.do_request(TMCC2EngineCommandEnum.BELL_ON, tmcc_id)
            elif option == BellOption.OFF:
                self.do_request(TMCC2EngineCommandEnum.BELL_OFF, tmcc_id)
            elif option == BellOption.ONCE:
                self.do_request(TMCC2EngineCommandEnum.BELL_ONE_SHOT_DING, tmcc_id, 3, duration=duration)
        return {"status": f"{self.scope.title} {tmcc_id} ringing bell..."}

    def smoke(self, tmcc_id: int, level: SmokeOption):
        if self.is_tmcc(tmcc_id):
            if level is None or level == SmokeOption.OFF:
                self.do_request(TMCC1EngineCommandEnum.SMOKE_OFF, tmcc_id)
            else:
                self.do_request(TMCC1EngineCommandEnum.SMOKE_ON, tmcc_id)
        else:
            if level is None or level == SmokeOption.OFF:
                self.do_request(TMCC2EffectsControl.SMOKE_OFF, tmcc_id)
            elif level == SmokeOption.ON or level == SmokeOption.LOW:
                self.do_request(TMCC2EffectsControl.SMOKE_LOW, tmcc_id)
            elif level == SmokeOption.MEDIUM:
                self.do_request(TMCC2EffectsControl.SMOKE_MEDIUM, tmcc_id)
            elif level == SmokeOption.HIGH:
                self.do_request(TMCC2EffectsControl.SMOKE_HIGH, tmcc_id)
        return {"status": f"{self.scope.title} {tmcc_id} Smoke: {level}..."}

    def toggle_direction(self, tmcc_id: int):
        if self.is_tmcc(tmcc_id):
            self.do_request(TMCC1EngineCommandEnum.TOGGLE_DIRECTION, tmcc_id)
        else:
            self.do_request(TMCC2EngineCommandEnum.TOGGLE_DIRECTION, tmcc_id)
        return {"status": f"{self.scope.title} {tmcc_id} toggle direction..."}

    def volume_up(self, tmcc_id: int):
        if self.is_tmcc(tmcc_id):
            self.do_request(TMCC1EngineCommandEnum.VOLUME_UP, tmcc_id)
        else:
            self.do_request(TMCC2EngineCommandEnum.VOLUME_UP, tmcc_id)
        return {"status": f"{self.scope.title} {tmcc_id} volume up..."}

    def volume_down(self, tmcc_id: int):
        if self.is_tmcc(tmcc_id):
            self.do_request(TMCC1EngineCommandEnum.VOLUME_DOWN, tmcc_id)
        else:
            self.do_request(TMCC2EngineCommandEnum.VOLUME_DOWN, tmcc_id)
        return {"status": f"{self.scope.title} {tmcc_id} volume up..."}

    def blow_horn(self, tmcc_id: int, option: HornOption, intensity: int = 10, duration: float = None):
        if self.is_tmcc(tmcc_id):
            self.do_request(TMCC1EngineCommandEnum.BLOW_HORN_ONE, tmcc_id, repeat=10)
        else:
            if option is None or option == HornOption.SOUND:
                self.do_request(TMCC2EngineCommandEnum.BLOW_HORN_ONE, tmcc_id, duration=duration)
            elif option == HornOption.GRADE:
                self.do_request(SequenceCommandEnum.GRADE_CROSSING_SEQ, tmcc_id)
            elif option == HornOption.QUILLING:
                self.do_request(TMCC2EngineCommandEnum.QUILLING_HORN, tmcc_id, intensity, duration=duration)
        return {"status": f"{self.scope.title} {tmcc_id} blowing horn..."}

    def aux_req(self, tmcc_id, aux: AuxOption, number, duration):
        if self.is_tmcc(tmcc_id):
            cmd = TMCC1EngineCommandEnum.by_name(f"{aux.name}_OPTION_ONE")
            cmd2 = TMCC1EngineCommandEnum.NUMERIC
        else:
            cmd = TMCC2EngineCommandEnum.by_name(f"{aux.name}_OPTION_ONE")
            cmd2 = TMCC2EngineCommandEnum.NUMERIC
        if cmd:
            if number is not None:
                self.do_request(cmd, tmcc_id)
                self.do_request(cmd2, tmcc_id, data=number, delay=0.10, duration=duration)
            else:
                self.do_request(cmd, tmcc_id, duration=duration)
            d = f" for {duration} second(s)" if duration else ""
            return {"status": f"Sending {aux.name} to {self.scope.title} {tmcc_id}{d}"}
        else:
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
                detail=f"Aux option '{aux.value}' not supported on {self.scope.title} {tmcc_id}",
            )

    def numeric_req(self, tmcc_id, number, duration) -> dict:
        if self.is_tmcc(tmcc_id):
            cmd = TMCC1EngineCommandEnum.NUMERIC
        else:
            cmd = TMCC2EngineCommandEnum.NUMERIC
        self.do_request(cmd, tmcc_id, data=number, duration=duration)
        d = f" for {duration} second(s)" if duration else ""
        return {"status": f"Sending Numeric {number} to {self.scope.title} {tmcc_id}{d}"}

    def boost(self, tmcc_id, duration) -> dict:
        if self.is_tmcc(tmcc_id):
            cmd = TMCC1EngineCommandEnum.BOOST_SPEED
        else:
            cmd = TMCC2EngineCommandEnum.BOOST_SPEED
        self.do_request(cmd, tmcc_id, duration=duration)
        d = f" for {duration} second(s)" if duration else ""
        return {"status": f"Sending Boost request to {self.scope.title} {tmcc_id}{d}"}

    def brake(self, tmcc_id, duration) -> dict:
        if self.is_tmcc(tmcc_id):
            cmd = TMCC1EngineCommandEnum.BRAKE_SPEED
        else:
            cmd = TMCC2EngineCommandEnum.BRAKE_SPEED
        self.do_request(cmd, tmcc_id, duration=duration)
        d = f" for {duration} second(s)" if duration else ""
        return {"status": f"Sending Brake request to {self.scope.title} {tmcc_id}{d}"}

    def get_engine_info(self, tmcc_id) -> dict:
        state = PyTrainApi.get().pytrain.store.query(self.scope, tmcc_id)
        engine_info = dict()
        if isinstance(state, EngineState) and state.bt_id:
            info = ProdInfo.get_info(state.bt_id)
            if info:
                engine_info.update(info)
        return engine_info
