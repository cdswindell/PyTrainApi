#
#  PyTrainApi: a restful api for controlling Lionel Legacy engines, trains, switches, and accessories
#
#  Copyright (c) 2025 Dave Swindell <pytraininfo.gmail.com>
#
#  SPDX-License-Identifier: LPGL
#
from __future__ import annotations

import os
import sys
from datetime import timedelta, datetime, timezone
from enum import Enum
from typing import TypeVar, Annotated, Any, cast

import jwt
import uvicorn
from fastapi import HTTPException, Request, APIRouter, Path, Query, Depends, status, FastAPI, Security
from fastapi.openapi.docs import get_swagger_ui_html
from fastapi.responses import JSONResponse, FileResponse
from fastapi.security import OAuth2PasswordRequestForm, OAuth2PasswordBearer, APIKeyHeader
from fastapi_utils.cbv import cbv
from jwt import InvalidTokenError
from passlib.context import CryptContext
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
    TMCC2RRSpeedsEnum,
    TMCC1RRSpeedsEnum,
    TMCC2EffectsControl,
    is_package,
    TMCC2RailSoundsDialogControl,
    TMCC1AuxCommandEnum,
)
from pytrain import get_version as pytrain_get_version
from pytrain.cli.pytrain import PyTrain
from pytrain.db.component_state import ComponentState
from pytrain.protocol.command_def import CommandDefEnum
from pytrain.utils.argument_parser import PyTrainArgumentParser
from pytrain.utils.path_utils import find_dir
from starlette.exceptions import HTTPException as StarletteHTTPException
from starlette.responses import RedirectResponse
from starlette.staticfiles import StaticFiles

from . import get_version

E = TypeVar("E", bound=CommandDefEnum)
API_NAME = "PyTrainApi"
DEFAULT_API_SERVER_PORT: int = 8000

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

# to get a string like this run:
# openssl rand -hex 32
# TODO: Read key from env variable
SECRET_KEY = "9b9cc80647ed32596c289ebc8a7e7b22a93259cbaaca96417532c271ced8f1fa"
ALGORITHM = "HS256"
ACCESS_TOKEN_EXPIRE_MINUTES = 30

# password is:"secret" (without the quotes)
fake_users_db = {
    "cdswindell": {
        "username": "cdswindell",
        "full_name": "Dave Swindell",
        "email": "pytraininfo@gmail.com",
        "hashed_password": "$2b$12$EixZaYVK1fsbw1ZfbX3OXePaWxn96p36WQoeG6Lruj3vjPGga31lW",
        "disabled": False,
    },
}

api_keys = {
    "e54d4431-5dab-474e-b71a-0db1fcb9e659": "7oDYjo3d9r58EJKYi5x4E8",
    "5f0c7127-3be9-4488-b801-c7b6415b45e9": "mUP7PpTHmFAkxcQLWKMY8t",
}

users = {
    "7oDYjo3d9r58EJKYi5x4E8": {"name": "Dave"},
    "mUP7PpTHmFAkxcQLWKMY8t": {"name": "Alice"},
}


class Token(BaseModel):
    access_token: str
    token_type: str


class TokenData(BaseModel):
    username: str | None = None


class User(BaseModel):
    username: str
    email: str | None = None
    full_name: str | None = None
    disabled: bool | None = None


class UserInDB(User):
    hashed_password: str


pwd_context = CryptContext(schemes=["bcrypt"], deprecated="auto")

oauth2_scheme = OAuth2PasswordBearer(tokenUrl="token")
app = FastAPI(
    title=f"{PROGRAM_NAME} API",
    description="Operate and control Lionel Legacy/TMCC engines, trains, switches, accessories, routes, "
    "and LCS components",
    version=get_version(),
    docs_url=None,
)


#
# fastapi run src/pytrain_api/pytrain_api.py
#
def verify_password(plain_password, hashed_password):
    return pwd_context.verify(plain_password, hashed_password)


def get_password_hash(password):
    return pwd_context.hash(password)


def get_user(db, username: str):
    if username and username.startswith("pytrain:"):
        username = username.split(":")[1]
    if username in db:
        user_dict = db[username]
        return UserInDB(**user_dict)


def authenticate_user(fake_db, username: str, password: str):
    user = get_user(fake_db, username)
    if not user:
        return False
    if not verify_password(password, user.hashed_password):
        return False
    return user


def create_access_token(data: dict, expires_delta: timedelta | None = None):
    to_encode = data.copy()
    if expires_delta:
        expire = datetime.now(timezone.utc) + expires_delta
    else:
        expire = datetime.now(timezone.utc) + timedelta(minutes=15)
    to_encode.update({"exp": expire})
    encoded_jwt = jwt.encode(to_encode, SECRET_KEY, algorithm=ALGORITHM)
    return encoded_jwt


async def get_current_user(token: Annotated[str, Depends(oauth2_scheme)]):
    credentials_exception = HTTPException(
        status_code=status.HTTP_401_UNAUTHORIZED,
        detail="Could not validate credentials",
        headers={"WWW-Authenticate": "Bearer"},
    )
    try:
        payload = jwt.decode(token, SECRET_KEY, algorithms=[ALGORITHM])
        username: str = payload.get("sub")
        if username is None:
            raise credentials_exception
        token_data = TokenData(username=username)
    except InvalidTokenError:
        raise credentials_exception
    user = get_user(fake_users_db, username=token_data.username)
    if user is None:
        raise credentials_exception
    return user


async def get_current_active_user(
    current_user: Annotated[User, Depends(get_current_user)],
):
    if current_user.disabled:
        raise HTTPException(status_code=400, detail="Inactive user")
    return current_user


api_key_header = APIKeyHeader(name="X-API-Key")


def get_api_user(api_header: str = Security(api_key_header)):
    if check_api_key(api_header):
        user = get_user_from_api_key(api_header)
        return user
    raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Missing or invalid API key")


def check_api_key(api_key: str):
    return api_key in api_keys


def get_user_from_api_key(api_key: str):
    return users[api_keys[api_key]]


@app.post("/token", include_in_schema=False)
async def login_for_access_token(
    form_data: Annotated[OAuth2PasswordRequestForm, Depends()],
) -> Token:
    user = authenticate_user(fake_users_db, form_data.username, form_data.password)
    if not user:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Incorrect username or password",
            headers={"WWW-Authenticate": "Bearer"},
        )
    access_token_expires = timedelta(minutes=ACCESS_TOKEN_EXPIRE_MINUTES)
    access_token = create_access_token(data={"sub": f"pytrain:{user.username}"}, expires_delta=access_token_expires)
    return Token(access_token=access_token, token_type="bearer")


@app.get("/users/me", response_model=User, include_in_schema=False)
async def read_users_me(
    current_user: Annotated[User, Depends(get_current_active_user)],
):
    return current_user


PYTRAIN_SERVER: PyTrain | None = None


class BellOption(str, Enum):
    TOGGLE = "toggle"
    OFF = "off"
    ON = "on"
    ONCE = "once"


class Component(str, Enum):
    ACCESSORY = "accessory"
    ENGINE = "engine"
    ROUTE = "route"
    SWITCH = "switch"
    TRAIN = "train"


class HornOption(str, Enum):
    SOUND = "sound"
    GRADE = "grade"
    QUILLING = "quilling"


class SmokeOption(str, Enum):
    OFF = "off"
    ON = "on"
    LOW = "low"
    MEDIUM = "medium"
    HIGH = "high"


class AuxOption(str, Enum):
    AUX1 = "aux1"
    AUX2 = "aux2"
    AUX3 = "aux3"


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


class TrainInfo(EngineInfo):
    scope: Component = Component.TRAIN
    flags: int | None
    components: dict[int, str] | None


router = APIRouter(prefix="/pytrain/v1", dependencies=[Depends(get_api_user)])
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
    if exc.status_code in [404]:
        return JSONResponse(content={"detail": "Forbidden"}, status_code=403)
    return JSONResponse(content={"detail": exc.detail}, status_code=exc.status_code)


@app.post("/version", summary=f"Get {PROGRAM_NAME} Version", include_in_schema=False)
def version(server: str = None):
    from . import get_version

    return {
        "pytrain": pytrain_get_version(),
        "pytrain_api": get_version(),
        "server": server,
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
    return RedirectResponse(url="/docs")


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
    PYTRAIN_SERVER.queue_command(f"echo {'on' if on else 'off'}")
    return {"status": f"Echo {'enabled' if on else 'disabled'}"}


@router.post("/system/stop_req")
async def stop():
    PYTRAIN_SERVER.queue_command("tr 99 -s")
    PYTRAIN_SERVER.queue_command("en 99 -s")
    PYTRAIN_SERVER.queue_command("en 99 -tmcc -s")
    return {"status": "Stop all engines and trains command sent"}


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
        parse_response = PYTRAIN_SERVER.parse_cli(cmd)
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
) -> list[dict[str, any]]:
    states = PYTRAIN_SERVER.store.query(scope)
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
        state: ComponentState = PYTRAIN_SERVER.store.query(self.scope, tmcc_id)
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
        PYTRAIN_SERVER.queue_command(cmd)


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

    @router.post("/accessory/{tmcc_id}/boost_req")
    async def boost(
        self,
        tmcc_id: Annotated[int, Accessory.id_path()],
        duration: Annotated[float, Query(description="Duration (seconds)", gt=0.0)] = None,
    ):
        self.do_request(TMCC1AuxCommandEnum.BOOST, tmcc_id, duration=duration)
        d = f" for {duration} second(s)" if duration else ""
        return {"status": f"Sending Boost request to {self.scope.title} {tmcc_id}{d}"}

    @router.post("/accessory/{tmcc_id}/brake_req")
    async def brake(
        self,
        tmcc_id: Annotated[int, Accessory.id_path()],
        duration: Annotated[float, Query(description="Duration (seconds)", gt=0.0)] = None,
    ):
        self.do_request(TMCC1AuxCommandEnum.BRAKE, tmcc_id, duration=duration)
        d = f" for {duration} second(s)" if duration else ""
        return {"status": f"Sending Brake request to {self.scope.title} {tmcc_id}{d}"}

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


class PyTrainEngine(PyTrainComponent):
    def __init__(self, scope: CommandScope):
        super().__init__(scope=scope)

    @property
    def prefix(self) -> str:
        return "engine" if self.scope == CommandScope.ENGINE else "train"

    def is_tmcc(self, tmcc_id: int) -> bool:
        state = PYTRAIN_SERVER.store.query(self.scope, tmcc_id)
        return state.is_tmcc if state and state else True

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
                raise HTTPException(
                    status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
                    detail=f"TMCC speeds must be between 0 and 31 inclusive: {speed}",
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
                raise HTTPException(
                    status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
                    detail=f"Legacy speeds must be between 0 and 199 inclusive: {speed}",
                )
        self.do_request(cmd)
        return {"status": f"{self.scope.title} {tmcc_id} speed now: {cmd.data}"}

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

    def rear_coupler(self, tmcc_id: int):
        if self.is_tmcc(tmcc_id):
            self.do_request(TMCC1EngineCommandEnum.REAR_COUPLER, tmcc_id)
        else:
            self.do_request(TMCC2EngineCommandEnum.REAR_COUPLER, tmcc_id)
        return {"status": f"{self.scope.title} {tmcc_id} rear coupler..."}

    def reset(self, tmcc_id: int, hold: bool = False):
        if self.is_tmcc(tmcc_id):
            self.do_request(TMCC1EngineCommandEnum.RESET, tmcc_id, duration=3 if hold else 0)
        else:
            self.do_request(TMCC2EngineCommandEnum.RESET, tmcc_id, duration=3 if hold else 0)
        return {"status": f"{self.scope.title} {tmcc_id} {'reset and refueled' if hold else 'reset'}..."}

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

    def numeric_req(self, tmcc_id, number, duration):
        if self.is_tmcc(tmcc_id):
            cmd = TMCC1EngineCommandEnum.NUMERIC
        else:
            cmd = TMCC2EngineCommandEnum.NUMERIC
        self.do_request(cmd, tmcc_id, data=number, duration=duration)
        d = f" for {duration} second(s)" if duration else ""
        return {"status": f"Sending Numeric {number} to {self.scope.title} {tmcc_id}{d}"}


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

    @router.post("/engine/{tmcc_id:int}/numeric_req")
    async def numeric_req(
        self,
        tmcc_id: Annotated[int, Engine.id_path()],
        number: Annotated[int, Query(description="Number (0 - 9)", ge=0, le=9)] = None,
        duration: Annotated[float, Query(description="Duration (seconds)", gt=0.0)] = None,
    ):
        return super().numeric_req(tmcc_id, number, duration)

    @router.post("/engine/{tmcc_id:int}/rear_coupler_req")
    async def rear_coupler(self, tmcc_id: Annotated[int, Engine.id_path()]):
        return super().rear_coupler(tmcc_id)

    @router.post("/engine/{tmcc_id:int}/reset_req")
    async def reset(
        self,
        tmcc_id: Annotated[int, Engine.id_path()],
        hold: Annotated[bool, Query(title="refuel", description="If true, perform refuel operation")] = False,
    ):
        return super().reset(tmcc_id, hold=hold)

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
    ):
        return super().reset(tmcc_id, hold=hold)

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


class PyTrainApi:
    def __init__(self, cmd_line: list[str] | None = None) -> None:
        from . import get_version

        try:
            # parse command line args
            if cmd_line:
                args = self.command_line_parser().parse_args(cmd_line)
            else:
                args = self.command_line_parser().parse_args()

            pytrain_args = "-api"
            if args.ser2 is True:
                pytrain_args += " -ser2"
                if args.baudrate:
                    pytrain_args += f" -baudrate {args.baudrate}"
                if args.port:
                    pytrain_args += f" -port {args.port}"
            if args.base is not None:
                pytrain_args += " -base"
                if isinstance(args.base, list) and len(args.base):
                    pytrain_args += f" {args.base[0]}"
            elif args.client is True:
                pytrain_args += " -client"
            elif args.server:
                pytrain_args += f" -server {args.server}"

            if (args.base is not None or args.ser2 is True) and args.server_port:
                pytrain_args += f" -server_port {args.server_port}"

            if args.echo is True:
                pytrain_args += " -echo"
            if args.buttons_file:
                pytrain_args += f" -buttons_file {args.buttons_file}"

            # create a PyTrain process to handle commands
            print(f"{API_NAME} {get_version()}")
            global PYTRAIN_SERVER
            PYTRAIN_SERVER = PyTrain(pytrain_args.split())
            port = args.api_port if args.api_port else DEFAULT_API_SERVER_PORT
            host = args.api_host if args.api_host else "0.0.0.0"
            uvicorn.run(f"{__name__}:app", host=host, port=port, reload=False)
        except Exception as e:
            # Output anything else nicely formatted on stderr and exit code 1
            sys.exit(f"{__file__}: error: {e}\n")

    @classmethod
    def command_line_parser(cls) -> PyTrainArgumentParser:
        from . import get_version

        prog = "pytrain_api" if is_package() else "pytrain_api.py"
        parser = PyTrainArgumentParser(add_help=False)
        parser.add_argument(
            "-version",
            action="version",
            version=f"{cls.__qualname__} {get_version()}",
            help="Show version and exit",
        )
        server_opts = parser.add_argument_group("Api server options")
        server_opts.add_argument(
            "-api_host",
            type=str,
            default="0.0.0.0",
            help="Web server Host IP address (default: 0.0.0.0; listen on all IP addresses)",
        )
        server_opts.add_argument(
            "-api_port",
            type=int,
            default=DEFAULT_API_SERVER_PORT,
            help=f"Web server API port (default: {DEFAULT_API_SERVER_PORT})",
        )
        # remove args we don't want user to see
        ptp = cast(PyTrainArgumentParser, PyTrain.command_line_parser())
        ptp.remove_args(["-headless", "-replay_file", "-no_wait", "-version"])
        return PyTrainArgumentParser(
            prog=prog,
            add_help=False,
            description=f"Run the {PROGRAM_NAME} Api Server",
            parents=[
                parser,
                ptp,
            ],
        )
