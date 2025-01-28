#
#  PyTrainApi: a restful api for controlling Lionel Legacy engines, trains, switches, and accessories
#
#  Copyright (c) 2025 Dave Swindell <pytraininfo.gmail.com>
#
#  SPDX-License-Identifier: LPGL
#
from __future__ import annotations

from enum import Enum
from typing import TypeVar, Annotated, Any

from fastapi import FastAPI, HTTPException, APIRouter, Path, Query
from fastapi_utils.cbv import cbv
from pydantic import BaseModel, field_validator, model_validator, Field
from pytrain import (
    CommandScope,
    TMCC1SwitchCommandEnum,
    CommandReq,
    TMCC1HaltCommandEnum,
    PROGRAM_NAME,
    TMCC1EngineCommandEnum,
    TMCC2EngineCommandEnum,
    TMCC1RouteCommandEnum,
    SequenceCommandEnum,
)
from pytrain.cli.pytrain import PyTrain
from pytrain.db.component_state import ComponentState
from pytrain.protocol.command_def import CommandDefEnum
from starlette.responses import RedirectResponse

E = TypeVar("E", bound=CommandDefEnum)
API_NAME = "PyTrainApi"

pytrain = PyTrain("-client -api -echo".split())
app = FastAPI()
router = APIRouter()


class BellOption(str, Enum):
    OFF = "off"
    ON = "on"
    ONCE = "once"
    TOGGLE = "toggle"


class HornOption(str, Enum):
    SOUND = "sound"
    GRADE = "grade"
    QUILLING = "quilling"


class Component(str, Enum):
    ACCESSORY = "accessory"
    ENGINE = "engine"
    ROUTE = "route"
    SWITCH = "switch"
    TRAIN = "train"


class ComponentInfo(BaseModel):
    tmcc_id: Annotated[int, Field(title="TMCC ID", description="Assigned TMCC ID", ge=1, le=99)]
    road_name: Annotated[str, Field(description="Road Name assigned by user", max_length=32)]
    road_number: Annotated[str, Field(description="Road Number assigned by user", max_length=4)]
    scope: Component


class ComponentInfoIr(ComponentInfo):
    road_name: Annotated[str, Field(description="Road Name assigned by user or read from Sensor Track", max_length=32)]
    road_number: Annotated[str, Field(description="Road Name assigned by user or read from Sensor Track", max_length=4)]


C = TypeVar("C", bound=ComponentInfo)


class RouteSwitch(BaseModel):
    switch: int
    position: str


class RouteInfo(ComponentInfo):
    switches: dict[int, str] | None


class SwitchInfo(ComponentInfo):
    scope: Component = Component.SWITCH
    state: str | None


class AccessoryInfo(ComponentInfo):
    # noinspection PyMethodParameters
    @model_validator(mode="before")
    def validate_model(cls, data: Any) -> Any:
        if isinstance(data, dict):
            for field in {"aux", "aux1", "aux2"}:
                if field not in data:
                    data[field] = None
            if "block" in data:
                data["aux"] = data["block"]
                del data["block"]
            if "type" not in data:
                data["type"] = "accessory"
        return data

    # noinspection PyMethodParameters
    @field_validator("scope", mode="before")
    def validate_component(cls, v: str) -> str:
        return "accessory" if v in {"acc", "sensor_track", "sensor track", "power_district", "power district"} else v

    scope: Component = Component.ACCESSORY
    type: str | None
    aux: str | None
    aux1: str | None
    aux2: str | None


class EngineInfo(ComponentInfoIr):
    scope: Component = Component.ENGINE
    control: str | None
    direction: str | None
    engine_class: str | None
    engine_type: str | None
    labor: int | None
    max_speed: int | None
    momentum: int | None
    rpm: int | None
    smoke: str | None
    sound_type: str | None
    speed: int | None
    speed_limit: int | None
    train_brake: int | None
    year: int | None


class TrainInfo(ComponentInfoIr):
    scope: Component = Component.TRAIN


@app.get("/", summary=f"Redirect to {API_NAME} Documentation")
def redirect_to_new_url():
    return RedirectResponse(url="/docs")


@app.post("/{component}/{tmcc_id:int}/cli_req")
async def send_command(
    component: Component,
    tmcc_id: Annotated[
        int,
        Path(
            title="TMCC ID",
            description="TMCC ID of the component to control",
            ge=1,
            le=99,
        ),
    ],
    command: Annotated[str, Query(description=f"{PROGRAM_NAME} CLI command")],
    is_tmcc: Annotated[str | None, Query(description="Send TMCC-style commands")] = None,
):
    try:
        if component in [Component.ENGINE, Component.TRAIN]:
            tmcc = " -tmcc" if is_tmcc is not None else ""
        else:
            tmcc = ""
        cmd = f"{component.value} {tmcc_id}{tmcc} {command}"
        parse_response = pytrain.parse_cli(cmd)
        if isinstance(parse_response, CommandReq):
            parse_response.send()
            return {"status": f"'{cmd}' command sent"}
        else:
            raise HTTPException(status_code=422, detail=f"Command is invalid: {parse_response}")
    except HTTPException as he:
        raise he
    except Exception as e:
        raise HTTPException(status_code=400, detail=str(e))


@app.get(
    "/system/halt",
    summary="Emergency Stop",
    description="Stops all engines and trains, in their tracks; turns off all power districts",
)
async def halt():
    try:
        CommandReq(TMCC1HaltCommandEnum.HALT).send()
        return {"status": "HALT command sent"}
    except Exception as e:
        raise HTTPException(status_code=400, detail=str(e))


@app.post("/system/echo_req")
async def echo(on: bool = True):
    pytrain.queue_command(f"echo {'on' if on else 'off'}")
    return {"status": f"Echo {'enabled' if on else 'disabled'}"}


@app.post("/system/stop_req")
async def stop():
    pytrain.queue_command("tr 99 -s")
    pytrain.queue_command("en 99 -s")
    pytrain.queue_command("en 99 -tmcc -s")
    return {"status": "Stop all engines and trains command sent"}


def get_components(
    scope: CommandScope,
    contains: str = None,
    is_legacy: bool = None,
    is_tmcc: bool = None,
) -> list[dict[str, any]]:
    states = pytrain.store.query(scope)
    if states is None:
        raise HTTPException(status_code=404, detail=f"No {scope.label} found")
    else:
        ret = list()
        for state in states:
            if is_legacy is not None and state.is_legacy != is_legacy:
                continue
            if is_tmcc is not None and state.is_tmcc != is_tmcc:
                continue
            # noinspection PyUnresolvedReferences
            if contains and state.name and contains.lower() not in state.name.lower():
                continue
            ret.append(state.as_dict())
        if not ret:
            raise HTTPException(status_code=404, detail=f"No matching {scope.label} found")
        return ret


class PyTrainComponent:
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
        state: ComponentState = pytrain.store.query(self.scope, tmcc_id)
        if state is None:
            raise HTTPException(status_code=404, detail=f"{self.scope.title} {tmcc_id} not found")
        else:
            return state.as_dict()

    def send(self, request: E, tmcc_id: int, data: int = None) -> dict[str, any]:
        try:
            req = CommandReq(request, tmcc_id, data, self.scope).send()
            return {"status": f"{self.scope.title} {req} sent"}
        except Exception as e:
            raise HTTPException(status_code=400, detail=str(e))

    def make_request(self, cmd_def: E, tmcc_id: int, data: int = None, submit: bool = True) -> CommandReq:
        cmd_req = CommandReq.build(cmd_def, tmcc_id, data, self.scope)
        if submit is True:
            cmd_req.send()
        return cmd_req

    @staticmethod
    def queue_command(cmd: str):
        pytrain.queue_command(cmd)


@app.get("/accessories", response_model=list[AccessoryInfo])
async def get_accessories(contains: str = None):
    return [AccessoryInfo(**d) for d in get_components(CommandScope.ACC, contains=contains)]


@cbv(router)
class Accessory(PyTrainComponent):
    def __init__(self):
        super().__init__(CommandScope.ACC)

    @router.get("/accessory/{tmcc_id}", response_model=AccessoryInfo)
    async def get_accessory(self, tmcc_id: Annotated[int, Accessory.id_path()]):
        return AccessoryInfo(**super().get(tmcc_id))


class PyTrainEngine(PyTrainComponent):
    def __init__(self, scope: CommandScope):
        super().__init__(scope=scope)

    @property
    def prefix(self) -> str:
        return "engine" if self.scope == CommandScope.ENGINE else "train"

    def is_tmcc(self, tmcc_id: int) -> bool:
        state = pytrain.store.query(self.scope, tmcc_id)
        return state.is_tmcc if state and state else True

    def tmcc(self, tmcc_id: int) -> str:
        return " -tmcc" if self.is_tmcc(tmcc_id) else ""

    def speed(self, tmcc_id: int, speed: int, immediate: bool = False, dialog: bool = False):
        tmcc = self.tmcc(tmcc_id)
        if tmcc:
            immediate = True
        self.queue_command(
            f"{self.prefix} {tmcc_id}{tmcc} sp {speed}{' -i' if immediate else ''}{' -d' if dialog else ''}"
        )
        return {"status": f"{self.scope.title} {tmcc_id} speed now {speed}"}

    def startup(self, tmcc_id: int, dialog: bool = False):
        cmd = "-sui"
        tmcc = self.tmcc(tmcc_id)
        if not tmcc and dialog is True:
            cmd = "-sud"
        self.queue_command(f"{self.prefix} {tmcc_id}{tmcc} {cmd}")
        return {"status": f"{self.scope.title} {tmcc_id} starting up..."}

    def shutdown(self, tmcc_id: int, dialog: bool = False):
        cmd = "-sdi"
        tmcc = self.tmcc(tmcc_id)
        if not tmcc and dialog is True:
            cmd = "-sdd"
        self.queue_command(f"{self.prefix} {tmcc_id}{tmcc} {cmd}")
        return {"status": f"{self.scope.title} {tmcc_id} shutting down..."}

    def stop(self, tmcc_id: int):
        tmcc = self.tmcc(tmcc_id)
        self.queue_command(f"{self.prefix} {tmcc_id}{tmcc} -stop")
        return {"status": f"{self.scope.title} {tmcc_id} stopping..."}

    def forward(self, tmcc_id: int):
        if self.is_tmcc(tmcc_id):
            self.make_request(TMCC1EngineCommandEnum.FORWARD_DIRECTION, tmcc_id)
        else:
            self.make_request(TMCC2EngineCommandEnum.FORWARD_DIRECTION, tmcc_id)
        return {"status": f"{self.scope.title} {tmcc_id} forward..."}

    def reverse(self, tmcc_id: int):
        if self.is_tmcc(tmcc_id):
            self.make_request(TMCC1EngineCommandEnum.REVERSE_DIRECTION, tmcc_id)
        else:
            self.make_request(TMCC2EngineCommandEnum.REVERSE_DIRECTION, tmcc_id)
        return {"status": f"{self.scope.title} {tmcc_id} reverse..."}

    def ring_bell(self, tmcc_id: int, option: BellOption):
        if self.is_tmcc(tmcc_id):
            self.make_request(TMCC1EngineCommandEnum.RING_BELL, tmcc_id)
        else:
            if option is None or option == BellOption.TOGGLE:
                self.make_request(TMCC2EngineCommandEnum.RING_BELL, tmcc_id)
            elif option == BellOption.ON:
                self.make_request(TMCC2EngineCommandEnum.BELL_ON, tmcc_id)
            elif option == BellOption.OFF:
                self.make_request(TMCC2EngineCommandEnum.BELL_OFF, tmcc_id)
            elif option == BellOption.ONCE:
                self.make_request(TMCC2EngineCommandEnum.BELL_ONE_SHOT_DING, tmcc_id, 3)
        return {"status": f"{self.scope.title} {tmcc_id} ringing bell..."}

    def blow_horn(self, tmcc_id: int, option: HornOption, intensity: int = 10):
        if self.is_tmcc(tmcc_id):
            self.make_request(TMCC1EngineCommandEnum.BLOW_HORN_ONE, tmcc_id)
        else:
            if option is None or option == HornOption.SOUND:
                self.make_request(TMCC2EngineCommandEnum.BLOW_HORN_ONE, tmcc_id)
            elif option == HornOption.GRADE:
                self.make_request(SequenceCommandEnum.GRADE_CROSSING_SEQ, tmcc_id)
            elif option == HornOption.QUILLING:
                self.make_request(TMCC2EngineCommandEnum.QUILLING_HORN, tmcc_id, intensity)
        return {"status": f"{self.scope.title} {tmcc_id} blowing horn..."}


@app.get("/engines", response_model=list[EngineInfo])
async def get_engines(contains: str = None, is_legacy: bool = None, is_tmcc: bool = None):
    return [
        EngineInfo(**d)
        for d in get_components(
            CommandScope.ENGINE,
            is_legacy=is_legacy,
            is_tmcc=is_tmcc,
            contains=contains,
        )
    ]


@cbv(router)
class Engine(PyTrainEngine):
    def __init__(self):
        super().__init__(CommandScope.ENGINE)

    @router.get("/engine/{tmcc_id:int}", response_model=EngineInfo)
    async def get_engine(self, tmcc_id: Annotated[int, Engine.id_path()]):
        return EngineInfo(**super().get(tmcc_id))

    @router.post("/engine/{tmcc_id:int}/bell_req")
    async def ring_bell(
        self,
        tmcc_id: Annotated[int, Engine.id_path()],
        option: Annotated[BellOption, Query(description="Bell effect")] = BellOption.TOGGLE,
    ):
        return super().ring_bell(tmcc_id, option)

    @router.post("/engine/{tmcc_id:int}/forward_req")
    async def forward(self, tmcc_id: Annotated[int, Engine.id_path()]):
        return super().forward(tmcc_id)

    @router.post("/engine/{tmcc_id:int}/horn_req")
    async def blow_horn(
        self,
        tmcc_id: Annotated[int, Engine.id_path()],
        option: Annotated[HornOption, Query(description="Horn effect")] = HornOption.SOUND,
        intensity: Annotated[int, Query(description="Quilling horn intensity (Legacy engines only)", ge=0, le=15)] = 10,
    ):
        return super().blow_horn(tmcc_id, option, intensity)

    @router.post("/engine/{tmcc_id:int}/reverse_req")
    async def reverse(self, tmcc_id: Annotated[int, Engine.id_path()]):
        return super().reverse(tmcc_id)

    @router.post("/engine/{tmcc_id:int}/shutdown_req")
    async def shutdown(self, tmcc_id: Annotated[int, Engine.id_path()], dialog: bool = False):
        return super().shutdown(tmcc_id, dialog=dialog)

    @router.post("/engine/{tmcc_id:int}/speed_req/{speed:int}")
    async def set_speed(
        self,
        tmcc_id: Annotated[int, Engine.id_path()],
        speed: Annotated[int, Path(description="New speed", ge=0, le=199)],
        immediate: bool = False,
        dialog: bool = False,
    ):
        return super().speed(tmcc_id, speed, immediate=immediate, dialog=dialog)

    @router.post("/engine/{tmcc_id:int}/startup_req")
    async def startup(self, tmcc_id: Annotated[int, Engine.id_path()], dialog: bool = False):
        return super().startup(tmcc_id, dialog=dialog)

    @router.post("/engine/{tmcc_id:int}/stop_req")
    async def stop(self, tmcc_id: Annotated[int, Engine.id_path()]):
        return super().stop(tmcc_id)


@app.get("/routes", response_model=list[RouteInfo])
async def get_routes(contains: str = None):
    return [RouteInfo(**d) for d in get_components(CommandScope.ROUTE, contains=contains)]


@cbv(router)
class Route(PyTrainComponent):
    def __init__(self):
        super().__init__(CommandScope.ROUTE)

    @router.get("/route/{tmcc_id}", response_model=RouteInfo)
    async def get_route(self, tmcc_id: Annotated[int, Route.id_path()]):
        return RouteInfo(**super().get(tmcc_id))

    @router.get("/route/{tmcc_id}/fire_req")
    async def fire(self, tmcc_id: Annotated[int, Route.id_path()]):
        self.make_request(TMCC1RouteCommandEnum.FIRE, tmcc_id)
        return {"status": f"{self.scope.title} {tmcc_id}  fired"}


@app.get("/switches", response_model=list[SwitchInfo])
async def get_switches(contains: str = None):
    return [SwitchInfo(**d) for d in get_components(CommandScope.SWITCH, contains=contains)]


@cbv(router)
class Switch(PyTrainComponent):
    def __init__(self):
        super().__init__(CommandScope.SWITCH)

    @router.get("/switch/{tmcc_id}", response_model=SwitchInfo)
    async def get_switch(self, tmcc_id: Annotated[int, Switch.id_path()]) -> SwitchInfo:
        return SwitchInfo(**super().get(tmcc_id))

    @router.post("/switch/{tmcc_id}/thru_req")
    async def thru(self, tmcc_id: Annotated[int, Switch.id_path()]):
        return self.send(TMCC1SwitchCommandEnum.THRU, tmcc_id)

    @router.post("/switch/{tmcc_id}/out_req")
    async def out(self, tmcc_id: Annotated[int, Switch.id_path()]):
        return self.send(TMCC1SwitchCommandEnum.OUT, tmcc_id)


@app.get("/trains", response_model=list[TrainInfo])
async def get_trains(contains: str = None, is_legacy: bool = None, is_tmcc: bool = None):
    return [
        TrainInfo(**d)
        for d in get_components(
            CommandScope.TRAIN,
            is_legacy=is_legacy,
            is_tmcc=is_tmcc,
            contains=contains,
        )
    ]


@cbv(router)
class Train(PyTrainEngine):
    def __init__(self):
        super().__init__(CommandScope.TRAIN)

    @router.get("/train/{tmcc_id:int}", response_model=TrainInfo)
    async def get_train(self, tmcc_id: Annotated[int, Train.id_path()]):
        return TrainInfo(**super().get(tmcc_id))

    @router.post("/train/{tmcc_id:int}/bell_req")
    async def ring_bell(
        self,
        tmcc_id: Annotated[int, Train.id_path()],
        option: Annotated[BellOption, Query(description="Bell effect")] = BellOption.TOGGLE,
    ):
        return super().ring_bell(tmcc_id, option)

    @router.post("/train/{tmcc_id:int}/forward_req")
    async def forward(self, tmcc_id: Annotated[int, Train.id_path()]):
        return super().forward(tmcc_id)

    @router.post("/train/{tmcc_id:int}/reverse_req")
    async def reverse(self, tmcc_id: Annotated[int, Train.id_path()]):
        return super().reverse(tmcc_id)

    @router.post("/train/{tmcc_id:int}/horn_req")
    async def blow_horn(
        self,
        tmcc_id: Annotated[int, Train.id_path()],
        option: Annotated[HornOption, Query(description="Horn effect")] = HornOption.SOUND,
        intensity: Annotated[int, Query(description="Quilling horn intensity (Legacy engines only)", ge=0, le=15)] = 10,
    ):
        return super().blow_horn(tmcc_id, option, intensity)

    @router.post("/train/{tmcc_id:int}/speed_req/{speed:int}")
    async def set_speed(
        self,
        tmcc_id: Annotated[int, Train.id_path()],
        speed: int,
        immediate: bool = False,
        dialog: bool = False,
    ):
        return super().speed(tmcc_id, speed, immediate=immediate, dialog=dialog)

    @router.post("/train/{tmcc_id:int}/startup_req")
    async def startup(self, tmcc_id: Annotated[int, Train.id_path()], dialog: bool = False):
        return super().startup(tmcc_id, dialog=dialog)

    @router.post("/train/{tmcc_id:int}/shutdown_req")
    async def shutdown(self, tmcc_id: Annotated[int, Train.id_path()], dialog: bool = False):
        return super().shutdown(tmcc_id, dialog=dialog)

    @router.post("/train/{tmcc_id:int}/stop_req")
    async def stop(self, tmcc_id: Annotated[int, Train.id_path()]):
        return super().stop(tmcc_id)


app.include_router(router)
