#
#  PyTrainApi: a restful api for controlling Lionel Legacy engines, trains, switches, and accessories
#
#  Copyright (c) 2025 Dave Swindell <pytraininfo.gmail.com>
#
#  SPDX-License-Identifier: LPGL
#
from __future__ import annotations

import logging
import os
import secrets
import uuid
from datetime import timedelta, datetime, timezone
from typing import TypeVar, Annotated, Any

import jwt
from dotenv import load_dotenv, find_dotenv
from fastapi import HTTPException, Request, APIRouter, Path, Query, Depends, status, FastAPI, Security, Body
from fastapi.openapi.docs import get_swagger_ui_html
from fastapi.responses import JSONResponse, FileResponse
from fastapi.security import OAuth2PasswordBearer, APIKeyHeader
from fastapi_utils.cbv import cbv
from jwt import InvalidSignatureError, ExpiredSignatureError
from pydantic import BaseModel, ValidationError
from pytrain import (
    CommandScope,
    TMCC1SwitchCommandEnum,
    CommandReq,
    TMCC1HaltCommandEnum,
    PROGRAM_NAME,
    TMCC1RouteCommandEnum,
    TMCC1AuxCommandEnum,
)
from pytrain import get_version as pytrain_get_version
from pytrain.pdi.asc2_req import Asc2Req
from pytrain.pdi.bpc2_req import Bpc2Req
from pytrain.pdi.constants import PdiCommand, Bpc2Action, Asc2Action
from pytrain.protocol.command_def import CommandDefEnum
from pytrain.protocol.tmcc1.tmcc1_constants import TMCC1SyncCommandEnum
from pytrain.utils.path_utils import find_dir
from starlette.exceptions import HTTPException as StarletteHTTPException
from starlette.responses import RedirectResponse
from starlette.staticfiles import StaticFiles

from . import get_version
from .pytrain_api import API_NAME, PyTrainApi
from .pytrain_component import (
    PyTrainComponent,
    PyTrainEngine,
    AuxOption,
    BellOption,
    Component,
    DialogOption,
    HornOption,
    OnOffOption,
    SmokeOption,
)
from .pytrain_info import RouteInfo, SwitchInfo, AccessoryInfo, EngineInfo, TrainInfo, BlockInfo, ProductInfo

log = logging.getLogger(__name__)

E = TypeVar("E", bound=CommandDefEnum)

DEFAULT_API_SERVER_VALUE = "[SERVER DOMAIN/IP ADDRESS NAME YOU GAVE TO ALEXA SKILL]"

# to get a secret key,
# openssl rand -hex 32
API_KEYS: dict[str, str] = dict()

# Load environment variables that drive behavior
load_dotenv(find_dotenv())
SECRET_KEY = os.environ.get("SECRET_KEY")
SECRET_PHRASE = os.environ.get("SECRET_PHRASE") if os.environ.get("SECRET_PHRASE") else "PYTRAINAPI"
API_TOKEN = os.environ.get("API_TOKEN")
API_TOKENS = os.environ.get("API_TOKENS")
ALGORITHM = os.environ.get("ALGORITHM")
API_SERVER = os.environ.get("API_SERVER")
ALEXA_TOKEN_EXP_MIN = os.environ.get("ALEXA_TOKEN_EXP_MIN")
if ALEXA_TOKEN_EXP_MIN is None or int(ALEXA_TOKEN_EXP_MIN) <= 0:
    ALEXA_TOKEN_EXP_MIN = 15
else:
    ALEXA_TOKEN_EXP_MIN = int(ALEXA_TOKEN_EXP_MIN)

if not API_SERVER or API_SERVER == DEFAULT_API_SERVER_VALUE:
    log.error("API_SERVER not set in .env; Alexa skill will not work")

if API_TOKENS:
    tokens = API_TOKENS.split(",")
    for token in tokens:
        token = token.strip()
        API_KEYS[token] = token


class Token(BaseModel):
    access_token: str
    token_type: str


oauth2_scheme = OAuth2PasswordBearer(tokenUrl="token")
app = FastAPI(
    title=f"{PROGRAM_NAME} API",
    description="Operate and control Lionel Legacy/TMCC engines, trains, switches, accessories, routes, "
    "and LCS components",
    version=get_version(),
    docs_url=None,
)

api_key_header = APIKeyHeader(name="X-API-Key")


def create_api_token(data: dict = None, expires_delta: timedelta | None = None, secret=SECRET_KEY):
    """
    Creates a JSON Web Token (JWT) for API authentication. The method encodes the
    provided payload data and includes an expiration time for the token. Additionally,
    a magic identifier is added for API confirmation.

    :param data: A dictionary containing the payload to encode into the token. Defaults to an
        empty dictionary if no data is provided.
    :param expires_delta: An optional timedelta specifying how long the token is valid. If
        not provided, the token defaults to expiring in 365 days.
    :param secret: A string value representing the secret key used to encode the token. Defaults
        to SECRET_KEY if no secret is supplied.
    :return: A string representing the encoded JWT.
    """
    if data is None:
        to_encode = {}
    else:
        to_encode = data.copy()
    if expires_delta:
        expire: datetime = datetime.now(timezone.utc) + expires_delta
    else:
        expire: datetime = datetime.now(timezone.utc) + timedelta(days=365)
    to_encode.update({"exp": expire})
    to_encode.update({"magic": API_NAME})
    encoded_jwt = jwt.encode(to_encode, secret, algorithm=ALGORITHM)
    return encoded_jwt


def create_secret(length: int = 32) -> str:
    return secrets.token_hex(length)


def get_api_token(api_key: str = Security(api_key_header)) -> bool:
    # see if it's a jwt token
    try:
        payload = jwt.decode(api_key, SECRET_KEY, algorithms=[ALGORITHM])
    except InvalidSignatureError as e:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail=str(e))
    except ExpiredSignatureError as es:
        raise HTTPException(status_code=498, detail=str(es))
    if payload:
        if api_key and (api_key == API_TOKEN or api_key in API_KEYS) and payload.get("magic") == API_NAME:
            return True
        if payload.get("SERVER", None) == API_SERVER:
            guid = payload.get("GUID", None)
            if guid in API_KEYS and API_KEYS[guid] == api_key:
                return True
            if guid:
                log.info(f"{guid} not in API Keys,but other info checks out")
                API_KEYS[guid] = api_key
                return True
    log.warning(f"Invalid Access attempt: payload: {payload} key: {api_key}")
    raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Missing or invalid API key")


router = APIRouter(prefix="/pytrain/v1", dependencies=[Depends(get_api_token)])
# router = APIRouter(prefix="/pytrain/v1")

FAVICON_PATH = None
APPLE_ICON_PATH = None
STATIC_DIR = find_dir("static", (".", "../"))
if STATIC_DIR:
    if os.path.isfile(f"{STATIC_DIR}/favicon.ico") is True:
        app.mount("/static", StaticFiles(directory=STATIC_DIR), name="static")
        FAVICON_PATH = f"{STATIC_DIR}/favicon.ico"
    if os.path.isfile(f"{STATIC_DIR}/apple-touch-icon.png") is True:
        APPLE_ICON_PATH = FAVICON_PATH = f"{STATIC_DIR}/apple-touch-icon.png"


@app.get("/apple-touch-icon.png", include_in_schema=False)
@app.get("/apple-touch-icon-precomposed.png", include_in_schema=False)
async def apple_icon():
    if APPLE_ICON_PATH:
        return FileResponse(APPLE_ICON_PATH)
    raise HTTPException(status_code=403)


@app.get("/favicon.ico", include_in_schema=False)
async def favicon():
    if FAVICON_PATH:
        return FileResponse(FAVICON_PATH)
    raise HTTPException(status_code=403)


# noinspection PyUnusedLocal
@app.exception_handler(StarletteHTTPException)
async def http_exception_handler(request: Request, exc: StarletteHTTPException):
    # allow APis that issue a legitimate 404 to send a 404 response
    if exc.status_code in [404] and (not exc.headers or exc.headers.get("X-Error", None) not in {"404"}):
        return JSONResponse(content={"detail": "Forbidden"}, status_code=403)
    return JSONResponse(content={"detail": exc.detail}, status_code=exc.status_code)


# noinspection PyUnusedLocal
@app.exception_handler(ValueError)
async def validation_exception_handler(request: Request, exc: ValidationError):
    detail = ""
    for error in exc.errors():
        detail += "; " if detail else ""
        detail += error["msg"]
    detail = detail.replace("Value error, ", "")
    return JSONResponse(
        content={"detail": detail},
        status_code=status.HTTP_404_NOT_FOUND,
    )


class Uid(BaseModel):
    uid: str


@app.post("/version", summary=f"Get {PROGRAM_NAME} Version", include_in_schema=False)
def version(uid: Annotated[Uid, Body()]):
    from . import get_version

    try:
        uid_decoded = jwt.decode(uid.uid, API_SERVER, algorithms=[ALGORITHM])
    except InvalidSignatureError:
        try:
            uid_decoded = jwt.decode(uid.uid, SECRET_PHRASE, algorithms=[ALGORITHM])
        except InvalidSignatureError:
            raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Forbidden")
    token_server = uid_decoded.get("SERVER", None)
    if token_server is None or API_SERVER != token_server.lower():
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Forbidden")

    # Encode as jwt token and return to Alexa/user
    guid = str(uuid.uuid4())
    api_key = create_api_token(
        {
            "GUID": guid,
            "SERVER": token_server,
        },
        timedelta(minutes=ALEXA_TOKEN_EXP_MIN),
    )
    API_KEYS[guid] = api_key
    return {
        "api-token": api_key,
        "pytrain": pytrain_get_version(),
        "pytrain_api": get_version(),
    }


@app.get("/docs", include_in_schema=False)
async def swagger_ui_html():
    return get_swagger_ui_html(
        openapi_url="/openapi.json",
        title=f"{PROGRAM_NAME} API",
        swagger_favicon_url="/static/favicon.ico",
    )


@app.get("/pytrain", summary=f"Redirect to {API_NAME} Documentation")
@app.get("/pytrain/v1", summary=f"Redirect to {API_NAME} Documentation")
def pytrain_doc():
    return RedirectResponse(url="/docs", status_code=status.HTTP_301_MOVED_PERMANENTLY)


@router.get(
    "/system/halt",
    summary="Emergency Stop",
    description="Stops all engines and trains, in their tracks; turns off all power districts.",
)
async def halt():
    try:
        CommandReq(TMCC1HaltCommandEnum.HALT).send()
        return {"status": "HALT command sent"}
    except Exception as e:
        raise HTTPException(status_code=400, detail=str(e))


@router.post(
    "/system/echo_req",
    summary="Enable/Disable Command Echoing",
    description=f"Enable/disable echoing of {PROGRAM_NAME} commands to log file. ",
)
async def echo(on: bool = True):
    PyTrainApi.get().pytrain.queue_command(f"echo {'on' if on else 'off'}")
    return {"status": f"Echo {'enabled' if on else 'disabled'}"}


@router.post(
    "/system/resync",
    summary="Resynchronize with Base 3",
    description="Reload all state information from your Lionel Base 3.",
)
async def resync():
    try:
        CommandReq(TMCC1SyncCommandEnum.RESYNC).send()
        return {"status": "RESYNC command sent"}
    except Exception as e:
        raise HTTPException(status_code=400, detail=str(e))


@router.post("/system/stop_req")
async def stop():
    PyTrainApi.get().pytrain.queue_command("tr 99 -s")
    PyTrainApi.get().pytrain.queue_command("en 99 -s")
    PyTrainApi.get().pytrain.queue_command("en 99 -tmcc -s")
    return {"status": "Stop all engines and trains command sent"}


@router.post(
    "/system/update",
    summary=f"Update {API_NAME}",
    description="Update {API_NAME} software from PyPi or Git Hub repository.",
)
async def update():
    try:
        CommandReq(TMCC1SyncCommandEnum.UPDATE).send()
        return {"status": "UPDATE command sent"}
    except Exception as e:
        raise HTTPException(status_code=400, detail=str(e))


@router.post(
    "/{component}/{tmcc_id:int}/cli_req",
    summary=f"Send {PROGRAM_NAME} CLI command",
    description=f"Send a {PROGRAM_NAME} CLI command to control trains, switches, and accessories.",
)
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
        parse_response = PyTrainApi.get().pytrain.parse_cli(cmd)
        if isinstance(parse_response, CommandReq):
            parse_response.send()
            return {"status": f"'{cmd}' command sent"}
        else:
            raise HTTPException(status_code=422, detail=f"Command is invalid: {parse_response}")
    except HTTPException as he:
        raise he
    except Exception as e:
        raise HTTPException(status_code=400, detail=str(e))


def get_components(
    scope: CommandScope,
    contains: str = None,
    is_legacy: bool = None,
    is_tmcc: bool = None,
) -> list[dict[str, Any]]:
    states = PyTrainApi.get().pytrain.store.query(scope)
    if states is None:
        headers = {"X-Error": "404"}
        raise HTTPException(status_code=404, headers=headers, detail=f"No {scope.label} found")
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
            headers = {"X-Error": "404"}
            raise HTTPException(status_code=404, headers=headers, detail=f"No matching {scope.label} found")
        return ret


@router.get("/accessories")
async def get_accessories(contains: str = None) -> list[AccessoryInfo]:
    return [AccessoryInfo(**d) for d in get_components(CommandScope.ACC, contains=contains)]


@cbv(router)
class Accessory(PyTrainComponent):
    def __init__(self):
        super().__init__(CommandScope.ACC)

    @router.get("/accessory/{tmcc_id}")
    async def get_accessory(self, tmcc_id: Annotated[int, Accessory.id_path()]) -> AccessoryInfo:
        return AccessoryInfo(**super().get(tmcc_id))

    @router.post("/accessory/{tmcc_id}/asc2_req")
    async def asc2(
        self,
        tmcc_id: Annotated[int, Accessory.id_path()],
        state: Annotated[OnOffOption, Query(description="On or Off")],
        duration: Annotated[float, Query(description="Duration (seconds)", gt=0.0)] = None,
    ):
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
            return {"status": f"Sending Asc2 {tmcc_id} {state.name} request{tmcc_id}{d}"}
        except Exception as e:
            raise HTTPException(status_code=400, detail=str(e))

    @router.post("/accessory/{tmcc_id}/boost_req")
    async def boost(
        self,
        tmcc_id: Annotated[int, Accessory.id_path()],
        duration: Annotated[float, Query(description="Duration (seconds)", gt=0.0)] = None,
    ):
        self.do_request(TMCC1AuxCommandEnum.BOOST, tmcc_id, duration=duration)
        d = f" for {duration} second(s)" if duration else ""
        return {"status": f"Sending Boost request to {self.scope.title} {tmcc_id}{d}"}

    @router.post("/accessory/{tmcc_id}/bpc2_req")
    async def bpc2(
        self,
        tmcc_id: Annotated[int, Accessory.id_path()],
        state: Annotated[OnOffOption, Query(description="On or Off")],
    ):
        try:
            int_state = 0 if state == OnOffOption.OFF else 1
            req = Bpc2Req(tmcc_id, PdiCommand.BPC2_SET, Bpc2Action.CONTROL3, state=int_state)
            req.send()
            return {"status": f"Sending Bpc2 {tmcc_id} {state.name} request"}
        except Exception as e:
            raise HTTPException(status_code=400, detail=str(e))

    @router.post("/accessory/{tmcc_id}/brake_req")
    async def brake(
        self,
        tmcc_id: Annotated[int, Accessory.id_path()],
        duration: Annotated[float, Query(description="Duration (seconds)", gt=0.0)] = None,
    ):
        self.do_request(TMCC1AuxCommandEnum.BRAKE, tmcc_id, duration=duration)
        d = f" for {duration} second(s)" if duration else ""
        return {"status": f"Sending Brake request to {self.scope.title} {tmcc_id}{d}"}

    @router.post("/accessory/{tmcc_id}/front_coupler_req")
    async def front_coupler(
        self,
        tmcc_id: Annotated[int, Accessory.id_path()],
        duration: Annotated[float, Query(description="Duration (seconds)", gt=0.0)] = None,
    ):
        self.do_request(TMCC1AuxCommandEnum.FRONT_COUPLER, tmcc_id, duration=duration)
        d = f" for {duration} second(s)" if duration else ""
        return {"status": f"Sending Front Coupler request to {self.scope.title} {tmcc_id}{d}"}

    @router.post("/accessory/{tmcc_id}/numeric_req")
    async def numeric(
        self,
        tmcc_id: Annotated[int, Accessory.id_path()],
        number: Annotated[int, Query(description="Number (0 - 9)", ge=0, le=9)] = None,
        duration: Annotated[float, Query(description="Duration (seconds)", gt=0.0)] = None,
    ):
        self.do_request(TMCC1AuxCommandEnum.NUMERIC, tmcc_id, data=number, duration=duration)
        d = f" for {duration} second(s)" if duration else ""
        return {"status": f"Sending Numeric {number} request to {self.scope.title} {tmcc_id}{d}"}

    @router.post("/accessory/{tmcc_id}/rear_coupler_req")
    async def rear_coupler(
        self,
        tmcc_id: Annotated[int, Accessory.id_path()],
        duration: Annotated[float, Query(description="Duration (seconds)", gt=0.0)] = None,
    ):
        self.do_request(TMCC1AuxCommandEnum.REAR_COUPLER, tmcc_id, duration=duration)
        d = f" for {duration} second(s)" if duration else ""
        return {"status": f"Sending Rear Coupler request to {self.scope.title} {tmcc_id}{d}"}

    @router.post("/accessory/{tmcc_id}/speed_req/{speed}")
    async def speed(
        self,
        tmcc_id: Annotated[int, Accessory.id_path()],
        speed: Annotated[int, Path(description="Relative speed (-5 - 5)", ge=-5, le=5)],
        duration: Annotated[float, Query(description="Duration (seconds)", gt=0.0)] = None,
    ):
        self.do_request(TMCC1AuxCommandEnum.RELATIVE_SPEED, tmcc_id, data=speed, duration=duration)
        d = f" for {duration} second(s)" if duration else ""
        return {"status": f"Sending Speed {speed} request to {self.scope.title} {tmcc_id}{d}"}

    @router.post("/accessory/{tmcc_id}/{aux_req}")
    async def operate_accessory(
        self,
        tmcc_id: Annotated[int, Accessory.id_path()],
        aux_req: Annotated[AuxOption, Path(description="Aux 1, Aux2, or Aux 3")],
        duration: Annotated[float, Query(description="Duration (seconds)", gt=0.0)] = None,
    ):
        cmd = TMCC1AuxCommandEnum.by_name(f"{aux_req.name}_OPT_ONE")
        if cmd:
            self.do_request(cmd, tmcc_id, duration=duration)
            d = f" for {duration} second(s)" if duration else ""
            return {"status": f"Sending {aux_req.name} to {self.scope.title} {tmcc_id}{d}"}
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail=f"Aux option '{aux_req.value}' not supported on {self.scope.title} {tmcc_id}",
        )


@router.get("/blocks")
async def get_blocks(contains: str = None) -> list[BlockInfo]:
    return [BlockInfo(**d) for d in get_components(CommandScope.BLOCK, contains=contains)]


@cbv(router)
class Block(PyTrainComponent):
    @classmethod
    def id_path(cls, label: str = None, min_val: int = 1, max_val: int = 99) -> Path:
        label = label if label else cls.__name__.replace("PyTrain", "")
        return Path(
            title="Block ID",
            description=f"{label}'s Block ID",
            ge=min_val,
            le=max_val,
        )

    def __init__(self):
        super().__init__(CommandScope.BLOCK)

    @router.get("/block/{block_id}")
    async def get_block(self, block_id: Annotated[int, Block.id_path()]) -> BlockInfo:
        return BlockInfo(**super().get(block_id))


@router.get("/engines")
async def get_engines(contains: str = None, is_legacy: bool = None, is_tmcc: bool = None) -> list[EngineInfo]:
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
    @classmethod
    def id_path(cls, label: str = None, min_val: int = 1, max_val: int = 9999) -> Path:
        label = label if label else cls.__name__.replace("PyTrain", "")
        return Path(
            title="TMCC ID",
            description=f"{label}'s TMCC ID",
            ge=min_val,
            le=max_val,
        )

    def __init__(self):
        super().__init__(CommandScope.ENGINE)

    @router.get("/engine/{tmcc_id:int}")
    async def get_engine(self, tmcc_id: Annotated[int, Engine.id_path()]) -> EngineInfo:
        return EngineInfo(**super().get(tmcc_id))

    @router.post("/engine/{tmcc_id:int}/bell_req")
    async def ring_bell(
        self,
        tmcc_id: Annotated[int, Engine.id_path()],
        option: Annotated[BellOption, Query(description="Bell effect")],
        duration: Annotated[float, Query(description="Duration (seconds, only with 'once' option)", gt=0.0)] = None,
    ):
        return super().ring_bell(tmcc_id, option, duration)

    @router.post("/engine/{tmcc_id}/boost_req")
    async def boost(
        self,
        tmcc_id: Annotated[int, Engine.id_path()],
        duration: Annotated[float, Query(description="Duration (seconds)", gt=0.0)] = None,
    ):
        return super().boost(tmcc_id, duration)

    @router.post("/engine/{tmcc_id}/brake_req")
    async def brake(
        self,
        tmcc_id: Annotated[int, Engine.id_path()],
        duration: Annotated[float, Query(description="Duration (seconds)", gt=0.0)] = None,
    ):
        return super().brake(tmcc_id, duration)

    @router.post("/engine/{tmcc_id:int}/dialog_req")
    async def do_dialog(
        self,
        tmcc_id: Annotated[int, Engine.id_path()],
        option: Annotated[DialogOption, Query(description="Dialog effect")],
    ):
        return super().dialog(tmcc_id, option)

    @router.post("/engine/{tmcc_id:int}/forward_req")
    async def forward(self, tmcc_id: Annotated[int, Engine.id_path()]):
        return super().forward(tmcc_id)

    @router.post("/engine/{tmcc_id:int}/front_coupler_req")
    async def front_coupler(self, tmcc_id: Annotated[int, Engine.id_path()]):
        return super().front_coupler(tmcc_id)

    @router.post("/engine/{tmcc_id:int}/horn_req")
    async def blow_horn(
        self,
        tmcc_id: Annotated[int, Engine.id_path()],
        option: Annotated[HornOption, Query(description="Horn effect")],
        intensity: Annotated[int, Query(description="Quilling horn intensity (Legacy engines only)", ge=0, le=15)] = 10,
        duration: Annotated[float, Query(description="Duration (seconds)", gt=0.0)] = None,
    ):
        return super().blow_horn(tmcc_id, option, intensity, duration)

    @router.get("/engine/{tmcc_id:int}/info")
    async def get_info(self, tmcc_id: Annotated[int, Engine.id_path()]) -> ProductInfo:
        return ProductInfo(**super().get_engine_info(tmcc_id))

    @router.post("/engine/{tmcc_id:int}/numeric_req")
    async def numeric_req(
        self,
        tmcc_id: Annotated[int, Engine.id_path()],
        number: Annotated[int, Query(description="Number (0 - 9)", ge=0, le=9)] = None,
        duration: Annotated[float, Query(description="Duration (seconds)", gt=0.0)] = None,
    ):
        return super().numeric_req(tmcc_id, number, duration)

    @router.post("/engine/{tmcc_id:int}/momentum_req")
    async def momentum(
        self,
        tmcc_id: Annotated[int, Engine.id_path()],
        level: Annotated[int, Query(description="Momentum level (0 - 7)", ge=0, le=7)] = None,
    ):
        return super().momentum(tmcc_id, level)

    @router.post("/engine/{tmcc_id:int}/rear_coupler_req")
    async def rear_coupler(self, tmcc_id: Annotated[int, Engine.id_path()]):
        return super().rear_coupler(tmcc_id)

    @router.post("/engine/{tmcc_id:int}/reset_req")
    async def reset(
        self,
        tmcc_id: Annotated[int, Engine.id_path()],
        hold: Annotated[bool, Query(title="refuel", description="If true, perform refuel operation")] = False,
        duration: Annotated[int, Query(description="Refueling time (seconds)", ge=3)] = 3,
    ):
        if hold is True:
            duration = duration if duration and duration > 3 else 3
        else:
            duration = None
        return super().reset(tmcc_id, duration)

    @router.post("/engine/{tmcc_id:int}/reverse_req")
    async def reverse(self, tmcc_id: Annotated[int, Engine.id_path()]):
        return super().reverse(tmcc_id)

    @router.post("/engine/{tmcc_id:int}/shutdown_req")
    async def shutdown(self, tmcc_id: Annotated[int, Engine.id_path()], dialog: bool = False):
        return super().shutdown(tmcc_id, dialog=dialog)

    @router.post("/engine/{tmcc_id:int}/smoke_level_req")
    async def smoke_level(self, tmcc_id: Annotated[int, Engine.id_path()], level: SmokeOption):
        return super().smoke(tmcc_id, level=level)

    @router.post("/engine/{tmcc_id:int}/speed_req/{speed}")
    async def speed(
        self,
        tmcc_id: Annotated[int, Engine.id_path()],
        speed: Annotated[
            int | str,
            Path(description="New speed (0 to 195, roll, restricted, slow, medium, limited, normal, highball"),
        ],
        immediate: bool = None,
        dialog: bool = None,
    ):
        return super().speed(tmcc_id, speed, immediate=immediate, dialog=dialog)

    @router.post("/engine/{tmcc_id:int}/startup_req")
    async def startup(self, tmcc_id: Annotated[int, Engine.id_path()], dialog: bool = False):
        return super().startup(tmcc_id, dialog=dialog)

    @router.post("/engine/{tmcc_id:int}/stop_req")
    async def stop(self, tmcc_id: Annotated[int, Engine.id_path()]):
        return super().stop(tmcc_id)

    @router.post("/engine/{tmcc_id:int}/toggle_direction_req")
    async def toggle_direction(self, tmcc_id: Annotated[int, Engine.id_path()]):
        return super().toggle_direction(tmcc_id)

    @router.post("/engine/{tmcc_id:int}/volume_down_req")
    async def volume_down(self, tmcc_id: Annotated[int, Engine.id_path()]):
        return super().volume_down(tmcc_id)

    @router.post("/engine/{tmcc_id:int}/volume_up_req")
    async def volume_up(self, tmcc_id: Annotated[int, Engine.id_path()]):
        return super().volume_up(tmcc_id)

    @router.post("/engine/{tmcc_id:int}/{aux_req}")
    async def aux_req(
        self,
        tmcc_id: Annotated[int, Engine.id_path()],
        aux_req: Annotated[AuxOption, Path(description="Aux 1, Aux2, or Aux 3")],
        number: Annotated[int, Query(description="Number (0 - 9)", ge=0, le=9)] = None,
        duration: Annotated[float, Query(description="Duration (seconds)", gt=0.0)] = None,
    ):
        return super().aux_req(tmcc_id, aux_req, number, duration)


@router.get("/routes", response_model=list[RouteInfo])
async def get_routes(contains: str = None):
    return [RouteInfo(**d) for d in get_components(CommandScope.ROUTE, contains=contains)]


@cbv(router)
class Route(PyTrainComponent):
    def __init__(self):
        super().__init__(CommandScope.ROUTE)

    @router.get("/route/{tmcc_id}", response_model=RouteInfo)
    async def get_route(self, tmcc_id: Annotated[int, Route.id_path()]):
        return RouteInfo(**super().get(tmcc_id))

    @router.post("/route/{tmcc_id}/fire_req")
    async def fire(self, tmcc_id: Annotated[int, Route.id_path()]):
        self.do_request(TMCC1RouteCommandEnum.FIRE, tmcc_id)
        return {"status": f"{self.scope.title} {tmcc_id} fired"}


@router.get("/switches", response_model=list[SwitchInfo])
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
        self.do_request(TMCC1SwitchCommandEnum.THRU, tmcc_id)
        return {"status": f"{self.scope.title} {tmcc_id} thrown thru"}

    @router.post("/switch/{tmcc_id}/out_req")
    async def out(self, tmcc_id: Annotated[int, Switch.id_path()]):
        self.do_request(TMCC1SwitchCommandEnum.OUT, tmcc_id)
        return {"status": f"{self.scope.title} {tmcc_id} thrown out"}


@router.get("/trains", response_model=list[TrainInfo])
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
        option: Annotated[BellOption, Query(description="Bell effect")],
        duration: Annotated[float, Query(description="Duration (seconds, only with 'once' option)", gt=0.0)] = None,
    ):
        return super().ring_bell(tmcc_id, option, duration)

    @router.post("/train/{tmcc_id}/boost_req")
    async def boost(
        self,
        tmcc_id: Annotated[int, Train.id_path()],
        duration: Annotated[float, Query(description="Duration (seconds)", gt=0.0)] = None,
    ):
        return super().boost(tmcc_id, duration)

    @router.post("/train/{tmcc_id}/brake_req")
    async def brake(
        self,
        tmcc_id: Annotated[int, Train.id_path()],
        duration: Annotated[float, Query(description="Duration (seconds)", gt=0.0)] = None,
    ):
        return super().brake(tmcc_id, duration)

    @router.post("/train/{tmcc_id:int}/dialog_req")
    async def do_dialog(
        self,
        tmcc_id: Annotated[int, Train.id_path()],
        option: Annotated[DialogOption, Query(description="Dialog effect")],
    ):
        return super().dialog(tmcc_id, option)

    @router.post("/train/{tmcc_id:int}/forward_req")
    async def forward(self, tmcc_id: Annotated[int, Train.id_path()]):
        return super().forward(tmcc_id)

    @router.post("/train/{tmcc_id:int}/front_coupler_req")
    async def front_coupler(self, tmcc_id: Annotated[int, Train.id_path()]):
        return super().front_coupler(tmcc_id)

    @router.post("/train/{tmcc_id:int}/momentum_req")
    async def momentum(
        self,
        tmcc_id: Annotated[int, Train.id_path()],
        level: Annotated[int, Query(description="Momentum level (0 - 7)", ge=0, le=7)] = None,
    ):
        return super().momentum(tmcc_id, level)

    @router.post("/train/{tmcc_id:int}/numeric_req")
    async def numeric_req(
        self,
        tmcc_id: Annotated[int, Train.id_path()],
        number: Annotated[int, Query(description="Number (0 - 9)", ge=0, le=9)] = None,
        duration: Annotated[float, Query(description="Duration (seconds)", gt=0.0)] = None,
    ):
        return super().numeric_req(tmcc_id, number, duration)

    @router.post("/train/{tmcc_id:int}/rear_coupler_req")
    async def rear_coupler(self, tmcc_id: Annotated[int, Train.id_path()]):
        return super().rear_coupler(tmcc_id)

    @router.post("/train/{tmcc_id:int}/horn_req")
    async def blow_horn(
        self,
        tmcc_id: Annotated[int, Train.id_path()],
        option: Annotated[HornOption, Query(description="Horn effect")],
        intensity: Annotated[int, Query(description="Quilling horn intensity (Legacy engines only)", ge=0, le=15)] = 10,
        duration: Annotated[float, Query(description="Duration (seconds, Legacy engines only)", gt=0.0)] = None,
    ):
        return super().blow_horn(tmcc_id, option, intensity, duration)

    @router.post("/train/{tmcc_id:int}/reset_req")
    async def reset(
        self,
        tmcc_id: Annotated[int, Train.id_path()],
        hold: Annotated[bool, Query(title="refuel", description="If true, perform refuel operation")] = False,
        duration: Annotated[int, Query(description="Refueling time (seconds)", ge=3)] = 3,
    ):
        if hold is True:
            duration = duration if duration and duration > 3 else 3
        else:
            duration = None
        return super().reset(tmcc_id, duration)

    @router.post("/train/{tmcc_id:int}/reverse_req")
    async def reverse(self, tmcc_id: Annotated[int, Train.id_path()]):
        return super().reverse(tmcc_id)

    @router.post("/train/{tmcc_id:int}/shutdown_req")
    async def shutdown(self, tmcc_id: Annotated[int, Train.id_path()], dialog: bool = False):
        return super().shutdown(tmcc_id, dialog=dialog)

    @router.post("/train/{tmcc_id:int}/smoke_level_req")
    async def smoke_level(self, tmcc_id: Annotated[int, Train.id_path()], level: SmokeOption):
        return super().smoke(tmcc_id, level=level)

    @router.post("/train/{tmcc_id:int}/speed_req/{speed}")
    async def speed(
        self,
        tmcc_id: Annotated[int, Train.id_path()],
        speed: int | str,
        immediate: bool = None,
        dialog: bool = None,
    ):
        return super().speed(tmcc_id, speed, immediate=immediate, dialog=dialog)

    @router.post("/train/{tmcc_id:int}/startup_req")
    async def startup(self, tmcc_id: Annotated[int, Train.id_path()], dialog: bool = False):
        return super().startup(tmcc_id, dialog=dialog)

    @router.post("/train/{tmcc_id:int}/stop_req")
    async def stop(self, tmcc_id: Annotated[int, Train.id_path()]):
        return super().stop(tmcc_id)

    @router.post("/train/{tmcc_id:int}/toggle_direction_req")
    async def toggle_direction(self, tmcc_id: Annotated[int, Train.id_path()]):
        return super().toggle_direction(tmcc_id)

    @router.post("/train/{tmcc_id:int}/volume_down_req")
    async def volume_down(self, tmcc_id: Annotated[int, Train.id_path()]):
        return super().volume_down(tmcc_id)

    @router.post("/train/{tmcc_id:int}/volume_up_req")
    async def volume_up(self, tmcc_id: Annotated[int, Train.id_path()]):
        return super().volume_up(tmcc_id)

    @router.post("/train/{tmcc_id:int}/{aux_req}")
    async def aux_req(
        self,
        tmcc_id: Annotated[int, Train.id_path()],
        aux_req: Annotated[AuxOption, Path(description="Aux 1, Aux2, or Aux 3")],
        number: Annotated[int, Query(description="Number (0 - 9)", ge=0, le=9)] = None,
        duration: Annotated[float, Query(description="Duration (seconds)", gt=0.0)] = None,
    ):
        return super().aux_req(tmcc_id, aux_req, number, duration)


app.include_router(router)
