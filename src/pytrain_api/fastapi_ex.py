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

pytrain = PyTrain("-client -api".split())
app = FastAPI()
router = APIRouter()


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


@cbv(router)
class Engine(PyTrainComponent):
    def __init__(self):
        super().__init__(CommandScope.ENGINE)

    @router.get("/engine/{tmcc_id}")
    async def get_engine(self, tmcc_id: int):
        return super().get(tmcc_id)


@cbv(router)
class Train(PyTrainComponent):
    def __init__(self):
        super().__init__(CommandScope.TRAIN)

    @router.get("/train/{tmcc_id}")
    async def get_train(self, tmcc_id: int):
        return super().get(tmcc_id)


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


app.include_router(router)
