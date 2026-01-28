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
from typing import Any, Callable, TypeVar

from fastapi import HTTPException, Path
from pytrain import (
    AccessoryState,
    CommandReq,
    CommandScope,
    ComponentStateStore,
    EngineState,
    SequenceCommandEnum,
    TMCC1AuxCommandEnum,
    TMCC1EngineCommandEnum,
    TMCC1RRSpeedsEnum,
    TMCC1SwitchCommandEnum,
    TMCC2EffectsControl,
    TMCC2EngineCommandEnum,
    TMCC2RailSoundsDialogControl,
    TMCC2RRSpeedsEnum,
)
from pytrain.db.component_state import ComponentState
from pytrain.db.prod_info import ProdInfo
from pytrain.pdi.amc2_req import Amc2Req
from pytrain.pdi.asc2_req import Asc2Req
from pytrain.pdi.bpc2_req import Bpc2Req
from pytrain.pdi.constants import Amc2Action, Asc2Action, Bpc2Action, PdiCommand
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
# noinspection PyTypeHints
Tmcc1DialogToCommand: dict[DialogOption, E] = {
    DialogOption.TOWER_RANDOM_CHATTER: TMCC2EngineCommandEnum.TOWER_CHATTER,
}

# noinspection PyTypeHints
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
    DING = "ding"
    OFF = "off"
    ON = "on"
    ONCE = "once"
    TOGGLE = "toggle"


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


class SwitchPosition(str, Enum):
    THRU = "thru"
    OUT = "out"


class PyTrainComponent:
    """
    Represents a component of the PyTrain system as exchanged via the API.

    This class provides mechanisms to interact with PyTrain API components through
    specific commands, allowing operations like query, send, and request handling.
    It also supports handling of TMCC ID paths and command queuing.

    """

    # noinspection PyTypeHints
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
        self._state_store = None

    @property
    def state_store(self) -> ComponentStateStore:
        if self._state_store is None:
            self._state_store = PyTrainApi.get().pytrain.store
        return self._state_store

    @property
    def scope(self) -> CommandScope:
        return self._scope

    def get(self, tmcc_id: int) -> dict[str, Any]:
        state: ComponentState = self.state_store.query(self.scope, tmcc_id)
        if state is None:
            headers = {"X-Error": "404"}
            raise HTTPException(status_code=404, headers=headers, detail=f"{self.scope.title} {tmcc_id} not found")
        else:
            return state.as_dict()

    def do_numeric(self, cmd: CommandDefEnum, tmcc_id, number, duration) -> dict[str, str]:
        if cmd not in [TMCC1EngineCommandEnum.NUMERIC, TMCC2EngineCommandEnum.NUMERIC, TMCC1AuxCommandEnum.NUMERIC]:
            raise HTTPException(status_code=400, detail=f"Invalid command '{cmd}' for numeric request")
        self.do_request(cmd, tmcc_id, data=number, duration=duration)
        d = f" for {duration} second(s)" if duration else ""
        return {"status": f"Sending Numeric {number} to {self.scope.title} {tmcc_id}{d}"}

    def do_relative_speed(
        self,
        cmd: CommandDefEnum,
        tmcc_id: int,
        speed: int,
        duration: float = None,
    ) -> dict[str, str]:
        self.do_request(cmd, tmcc_id, data=speed, duration=duration)
        d = f" for {duration} second(s)" if duration else ""
        return {"status": f"Sending Relative Speed {speed} request to {self.scope.title} {tmcc_id}{d}"}

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
        scope: CommandScope = None,
        submit: bool = True,
        repeat: int = 1,
        duration: float = 0,
        delay: float = None,
    ) -> CommandReq:
        try:
            if isinstance(cmd_def, CommandReq):
                cmd_req = cmd_def
            else:
                scope = scope or self.scope
                cmd_req = CommandReq.build(cmd_def, tmcc_id, data, scope)
            if submit:
                repeat = repeat if repeat and repeat >= 1 else 1
                duration = duration if duration is not None else 0
                delay = delay if delay is not None else 0
                cmd_req.send(repeat=repeat, delay=delay, duration=duration)
            return cmd_req
        except Exception as e:
            raise HTTPException(status_code=400, detail=str(e))


class PyTrainAccessory(PyTrainComponent):
    def __init__(self, scope: CommandScope):
        super().__init__(scope=scope)

    def enforce_strict(self, tmcc_id: int, label: str, is_valid: Callable[[AccessoryState], bool]) -> None:
        acc_state = self.state_store.query(self.scope, tmcc_id)
        if acc_state is None:
            headers = {"X-Error": "404"}
            raise HTTPException(status_code=404, headers=headers, detail=f"{label} {tmcc_id} not found")
        if not is_valid(acc_state):
            ia = "an" if label.startswith("A") else "a"
            raise HTTPException(status_code=400, detail=f"{self.scope.title} {tmcc_id} is not {ia} {label}")

    def amc2_motor(
        self,
        tmcc_id: int,
        motor: int,
        state: OnOffOption | None,
        speed: int | None,
        strict: bool = False,
    ) -> dict[str, str]:
        if strict:
            self.enforce_strict(tmcc_id, "AMC2", lambda x: x.is_amc2)
        if state:
            self.do_request(TMCC1AuxCommandEnum.NUMERIC, tmcc_id, data=motor)
            self.do_request(
                TMCC1AuxCommandEnum.AUX1_OPT_ONE if state == OnOffOption.ON else TMCC1AuxCommandEnum.AUX2_OPT_ONE,
                tmcc_id,
            )
            return {"status": f"Setting AMC2 {tmcc_id} Motor {motor} to {state.name}"}
        if speed is not None:
            Amc2Req(tmcc_id, PdiCommand.AMC2_SET, Amc2Action.MOTOR, motor=motor - 1, speed=speed).send()
            return {"status": f"Setting AMC2 {tmcc_id} Motor {motor} Speed to {speed}"}
        raise HTTPException(status_code=400, detail="Must specify either motor state or speed.")

    def amc2_lamp(
        self,
        tmcc_id: int,
        lamp: int,
        state: OnOffOption | None,
        level: int | None,
        strict: bool = False,
    ) -> dict[str, str]:
        if strict:
            self.enforce_strict(tmcc_id, "AMC2", lambda x: x.is_amc2)
        if state:
            self.do_request(TMCC1AuxCommandEnum.NUMERIC, tmcc_id, data=lamp + 2)
            self.do_request(
                TMCC1AuxCommandEnum.AUX1_OPT_ONE if state == OnOffOption.ON else TMCC1AuxCommandEnum.AUX2_OPT_ONE,
                tmcc_id,
            )
            return {"status": f"Setting AMC2 {tmcc_id} Lamp {lamp} to {state.name}"}
        if level is not None:
            Amc2Req(tmcc_id, PdiCommand.AMC2_SET, Amc2Action.LAMP, lamp=lamp - 1, level=level).send()
            return {"status": f"Setting AMC2 {tmcc_id} Lamp {lamp} Level to {level}"}
        raise HTTPException(status_code=400, detail="Must specify either lamp state or level.")

    def asc2(self, tmcc_id: int, state: OnOffOption, duration: float = None, strict: bool = False) -> dict[str, str]:
        if strict:
            self.enforce_strict(tmcc_id, "ASC2", lambda x: x.is_asc2)
        try:
            duration = duration if duration is not None and duration > 0.0 else 0
            int_state = 0 if state == OnOffOption.OFF else 1
            d = f" for {duration} second(s)" if duration else ""
            # adjust time and duration parameters
            if int_state == 1:
                if duration > 2.5:
                    time = 0.600
                    duration -= time
                elif 0.0 < duration <= 2.55:
                    time = duration
                    duration = 0
                else:
                    time = 0
            else:
                time = duration = 0.0
            req = Asc2Req(tmcc_id, PdiCommand.ASC2_SET, Asc2Action.CONTROL1, values=int_state, time=time)
            req.send(duration=duration)
            return {"status": f"Sending Asc2 {tmcc_id} {state.name}{d}"}
        except Exception as e:
            raise HTTPException(status_code=400, detail=str(e))

    def bpc2(self, tmcc_id: int, state: OnOffOption, strict: bool = False) -> dict[str, str]:
        if strict:
            self.enforce_strict(tmcc_id, "BPC2", lambda x: x.is_bpc2)
        try:
            int_state = 0 if state == OnOffOption.OFF else 1
            Bpc2Req(tmcc_id, PdiCommand.BPC2_SET, Bpc2Action.CONTROL3, state=int_state).send()
            return {"status": f"Sending Bpc2 {tmcc_id} {state.name}"}
        except Exception as e:
            raise HTTPException(status_code=400, detail=str(e))

    def open_coupler(self, tmcc_id: int, coupler: TMCC1AuxCommandEnum, duration: float | None = None) -> dict[str, str]:
        self.do_request(coupler, tmcc_id, duration=duration)
        d = f" for {duration} second(s)" if duration else ""
        ct = coupler.title.replace("_", " ")
        return {"status": f"Sending open {ct} request to {self.scope.title} {tmcc_id}{d}"}

    def boost(self, tmcc_id: int, duration: float = None) -> dict[str, str]:
        self.do_request(TMCC1AuxCommandEnum.BOOST, tmcc_id, duration=duration)
        d = f" for {duration} second(s)" if duration else ""
        return {"status": f"Sending Boost request to {self.scope.title} {tmcc_id}{d}"}

    def brake(self, tmcc_id: int, duration: float = None) -> dict[str, str]:
        self.do_request(TMCC1AuxCommandEnum.BRAKE, tmcc_id, duration=duration)
        d = f" for {duration} second(s)" if duration else ""
        return {"status": f"Sending Brake request to {self.scope.title} {tmcc_id}{d}"}

    def relative_speed(self, tmcc_id: int, speed: int, duration: float = None) -> dict[str, str]:
        return self.do_relative_speed(TMCC1AuxCommandEnum.RELATIVE_SPEED, tmcc_id, speed, duration)

    def aux(self, tmcc_id: int, aux_req: AuxOption, number: int = None, duration: float = None) -> dict[str, str]:
        cmd = TMCC1AuxCommandEnum.by_name(f"{aux_req.name}_OPT_ONE")
        if cmd:
            if number is not None:
                self.do_request(cmd, tmcc_id)
                self.do_request(TMCC1AuxCommandEnum.NUMERIC, tmcc_id, data=number, delay=0.10, duration=duration)
            else:
                self.do_request(cmd, tmcc_id, duration=duration)
            d = f" for {duration} second(s)" if duration else ""
            return {"status": f"Sending {aux_req.name} to {self.scope.title} {tmcc_id}{d}"}
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail=f"Aux option '{aux_req.value}' not supported on {self.scope.title} {tmcc_id}",
        )


class PyTrainSwitch(PyTrainComponent):
    def __init__(self, scope: CommandScope):
        super().__init__(scope=scope)

    def throw(self, tmcc_id: int, position: SwitchPosition) -> dict[str, str]:
        pos = TMCC1SwitchCommandEnum.OUT if position == SwitchPosition.OUT else TMCC1SwitchCommandEnum.THRU
        self.do_request(pos, tmcc_id)
        return {"status": f"Throwing {self.scope.title} {tmcc_id} {position.value}"}


class PyTrainEngine(PyTrainComponent):
    def __init__(self, scope: CommandScope):
        super().__init__(scope=scope)

    @property
    def prefix(self) -> str:
        return "engine" if self.scope == CommandScope.ENGINE else "train"

    def is_tmcc(self, tmcc_id: int) -> bool:
        state = self.state_store.query(self.scope, tmcc_id)
        if isinstance(state, ComponentState):
            return state.is_tmcc if state else True
        return True

    def tmcc(self, tmcc_id: int) -> str:
        return " -tmcc" if self.is_tmcc(tmcc_id) else ""

    async def _set_speed(
        self,
        tmcc_id: int,
        speed: int | str,
        immediate: bool | None,
        dialog: bool | None,
    ) -> dict[str, str]:
        return self.speed(tmcc_id, speed, immediate=immediate, dialog=dialog)

    def speed(self, tmcc_id: int, speed: int | str, immediate: bool = False, dialog: bool = False) -> dict[str, str]:
        # convert string numbers to ints
        try:
            if isinstance(speed, str) and speed.isdigit() is True:
                speed = int(speed)
        except ValueError:
            pass
        tmcc = self.tmcc(tmcc_id)
        if immediate:
            cmd_def = TMCC1EngineCommandEnum.ABSOLUTE_SPEED if tmcc is True else TMCC2EngineCommandEnum.ABSOLUTE_SPEED
        elif dialog:
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

    def relative_speed(self, tmcc_id: int, speed: int, duration: float = None) -> dict[str, str]:
        cmd = TMCC1EngineCommandEnum.RELATIVE_SPEED if self.is_tmcc(tmcc_id) else TMCC2EngineCommandEnum.RELATIVE_SPEED
        return self.do_relative_speed(cmd, tmcc_id, speed, duration)

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
        if self.tmcc(tmcc_id):
            cmd = TMCC1EngineCommandEnum.START_UP_IMMEDIATE
        else:
            cmd = (
                TMCC2EngineCommandEnum.START_UP_DELAYED if dialog is True else TMCC2EngineCommandEnum.START_UP_IMMEDIATE
            )
        self.do_request(cmd, tmcc_id)
        return {"status": f"{self.scope.title} {tmcc_id} starting up..."}

    def shutdown(self, tmcc_id: int, dialog: bool = False):
        if self.tmcc(tmcc_id):
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

    def ring_bell(self, tmcc_id: int, option: BellOption | None, duration: float = None, ding: int = 0):
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
                self.do_request(TMCC2EngineCommandEnum.BELL_ONE_SHOT_DING, tmcc_id, 0, duration=duration)
            elif option == BellOption.DING:
                ding = ding if ding is not None and 0 <= ding <= 3 else 0
                self.do_request(TMCC2EngineCommandEnum.BELL_ONE_SHOT_DING, tmcc_id, ding)
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

    def stop_all(self) -> dict:
        self.do_request(TMCC1EngineCommandEnum.STOP_IMMEDIATE, 99)
        self.do_request(TMCC2EngineCommandEnum.STOP_IMMEDIATE, 99, scope=CommandScope.TRAIN)
        return {"status": "Sent 'stop' command to all engines and trains..."}

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
        return {"status": f"{self.scope.title} {tmcc_id} volume down..."}

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

    def aux(self, tmcc_id, aux: AuxOption, number, duration):
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

    def numeric(self, tmcc_id, number, duration) -> dict:
        cmd = TMCC1EngineCommandEnum.NUMERIC if self.is_tmcc(tmcc_id) else TMCC2EngineCommandEnum.NUMERIC
        return self.do_numeric(cmd, tmcc_id, number, duration)

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
        state = self.state_store.query(self.scope, tmcc_id)
        engine_info = dict()
        if isinstance(state, EngineState) and state.bt_id:
            info = ProdInfo.get_info(state.bt_id)
            if info:
                engine_info.update(info)
        return engine_info
