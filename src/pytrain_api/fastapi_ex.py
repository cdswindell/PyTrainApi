#
#  PyTrainApi: a restful api for controlling Lionel Legacy engines, trains, switches, and accessories
#
#  Copyright (c) 2025 Dave Swindell <pytraininfo.gmail.com>
#
#  SPDX-License-Identifier: LPGL
#
from typing import List, Dict, Any

from fastapi import FastAPI, HTTPException, APIRouter
from fastapi_utils.cbv import cbv
from pytrain import CommandScope
from pytrain.cli.pytrain import PyTrain
from pytrain.db.component_state import ComponentState

pytrain = PyTrain("-client -api -echo".split())
app = FastAPI()
router = APIRouter()


@app.get("/system/halt")
async def halt():
    pytrain.queue_command("halt")
    return {"status": "Halt command sent"}


@app.get("/system/stop")
async def stop():
    pytrain.queue_command("tr 99 -s")
    pytrain.queue_command("en 99 -s")
    pytrain.queue_command("en 99 -tmcc -s")
    return {"status": "Stop all engines and trains command sent"}


@app.post("/system/echo")
async def echo(on: bool = True):
    pytrain.queue_command(f"echo {'on' if on else 'off'}")
    return {"status": f"Echo {'enabled' if on else 'disabled'}"}


def get_components(
    scope: CommandScope,
    name: str = None,
    is_legacy: bool = None,
    is_tmcc: bool = None,
) -> List[Dict[str, Any]]:
    states = pytrain.store.query(scope)
    if states is None:
        HTTPException(status_code=404, detail=f"No {scope} found")
    else:
        ret = list()
        for state in states:
            if is_legacy is not None and state.is_legacy != is_legacy:
                continue
            if is_tmcc is not None and state.is_tmcc != is_tmcc:
                continue
            if name and state.name and name.lower() not in state.name.lower():
                continue
            ret.append(state.as_dict())
        return ret


@app.get("/engines")
async def get_engines(name: str = None, is_legacy: bool = None, is_tmcc: bool = None):
    return get_components(CommandScope.ENGINE, name=name, is_legacy=is_legacy, is_tmcc=is_tmcc)


@app.get("/trains")
async def get_trains(name: str = None, is_legacy: bool = None, is_tmcc: bool = None):
    return get_components(CommandScope.TRAIN, name=name, is_legacy=is_legacy, is_tmcc=is_tmcc)


@app.get("/switches")
async def get_switches(name: str = None):
    return get_components(CommandScope.SWITCH, name=name)


@app.get("/accessories")
async def get_accessories(name: str = None):
    return get_components(CommandScope.ACC, name=name)


@app.get("/routes")
async def get_routes(name: str = None):
    return get_components(CommandScope.ROUTE, name=name)


class PyTrainComponent:
    def __init__(self, scope: CommandScope):
        super().__init__()
        self._scope = scope

    @property
    def scope(self) -> CommandScope:
        return self._scope

    def get(self, tmcc_id: int):
        state: ComponentState = pytrain.store.query(self.scope, tmcc_id)
        if state is None:
            raise HTTPException(status_code=404, detail=f"{self.scope.title} {tmcc_id} not found")
        else:
            return state.as_dict()

    @staticmethod
    def queue_command(cmd: str):
        pytrain.queue_command(cmd)


class PyTrainEngine(PyTrainComponent):
    def __init__(self, scope: CommandScope):
        super().__init__(scope=scope)

    @property
    def prefix(self) -> str:
        return "engine" if self.scope == CommandScope.ENGINE else "train"

    def tmcc(self, tmcc_id: int) -> str:
        state = pytrain.store.query(self.scope, tmcc_id)
        return " -tmcc" if state and state.is_tmcc else ""

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


@cbv(router)
class Engine(PyTrainEngine):
    def __init__(self):
        super().__init__(CommandScope.ENGINE)

    @router.get("/engine/{tmcc_id:int}")
    async def get_engine(self, tmcc_id: int):
        return super().get(tmcc_id)

    @router.post("/engine/{tmcc_id:int}/speed/{speed:int}")
    async def set_speed(self, tmcc_id: int, speed: int, immediate: bool = False, dialog: bool = False):
        return super().speed(tmcc_id, speed, immediate=immediate, dialog=dialog)

    @router.post("/engine/{tmcc_id:int}/startup")
    async def startup(self, tmcc_id: int, dialog: bool = False):
        return super().startup(tmcc_id, dialog=dialog)

    @router.post("/engine/{tmcc_id:int}/shutdown")
    async def shutdown(self, tmcc_id: int, dialog: bool = False):
        return super().shutdown(tmcc_id, dialog=dialog)

    @router.post("/engine/{tmcc_id:int}/stop")
    async def stop(self, tmcc_id: int):
        return super().stop(tmcc_id)


@cbv(router)
class Train(PyTrainEngine):
    def __init__(self):
        super().__init__(CommandScope.TRAIN)

    @router.get("/train/{tmcc_id:int}")
    async def get_train(self, tmcc_id: int):
        return super().get(tmcc_id)

    @router.post("/train/{tmcc_id:int}/speed/{speed:int}")
    async def set_speed(self, tmcc_id: int, speed: int, immediate: bool = False, dialog: bool = False):
        return super().speed(tmcc_id, speed, immediate=immediate, dialog=dialog)

    @router.post("/train/{tmcc_id:int}/startup")
    async def startup(self, tmcc_id: int, dialog: bool = False):
        return super().startup(tmcc_id, dialog=dialog)

    @router.post("/train/{tmcc_id:int}/shutdown")
    async def shutdown(self, tmcc_id: int, dialog: bool = False):
        return super().shutdown(tmcc_id, dialog=dialog)

    @router.post("/train/{tmcc_id:int}/stop")
    async def stop(self, tmcc_id: int):
        return super().stop(tmcc_id)


@cbv(router)
class Switch(PyTrainComponent):
    def __init__(self):
        super().__init__(CommandScope.SWITCH)

    @router.get("/switch/{tmcc_id}")
    async def get_switch(self, tmcc_id: int):
        return super().get(tmcc_id)


@cbv(router)
class Accessory(PyTrainComponent):
    def __init__(self):
        super().__init__(CommandScope.ACC)

    @router.get("/accessory/{tmcc_id}")
    async def get_accessory(self, tmcc_id: int):
        return super().get(tmcc_id)


@cbv(router)
class Route(PyTrainComponent):
    def __init__(self):
        super().__init__(CommandScope.ROUTE)

    @router.get("/route/{tmcc_id}")
    async def get_route(self, tmcc_id: int):
        return super().get(tmcc_id)


app.include_router(router)
