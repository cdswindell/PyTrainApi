#
#  PyTrainApi: a restful api for controlling Lionel Legacy engines, trains, switches, and accessories
#
#  Copyright (c) 2025 Dave Swindell <pytraininfo.gmail.com>
#
#  SPDX-License-Identifier: LPGL
#
from __future__ import annotations

import asyncio
import logging
import os
import re
import secrets
import uuid
from contextlib import asynccontextmanager
from datetime import datetime, timedelta, timezone
from typing import Annotated, Any, Callable, TypeVar

import jwt
from dotenv import find_dotenv, load_dotenv
from fastapi import APIRouter, Body, Depends, FastAPI, HTTPException, Path, Query, Request, Security, status
from fastapi.openapi.docs import get_swagger_ui_html
from fastapi.responses import FileResponse, JSONResponse
from fastapi.security import APIKeyHeader, OAuth2PasswordBearer
from fastapi_utils.cbv import cbv
from jwt import DecodeError, ExpiredSignatureError, InvalidSignatureError, InvalidTokenError
from pydantic import BaseModel, ValidationError
from pytrain import (
    PROGRAM_NAME,
    CommandReq,
    CommandScope,
    TMCC1AuxCommandEnum,
    TMCC1HaltCommandEnum,
    TMCC1RouteCommandEnum,
    TMCC2EngineCommandEnum,
)
from pytrain import get_version as pytrain_get_version
from pytrain.protocol.command_def import CommandDefEnum
from pytrain.protocol.tmcc1.tmcc1_constants import TMCC1EngineCommandEnum, TMCC1SyncCommandEnum
from pytrain.utils.path_utils import find_dir
from starlette.exceptions import HTTPException as StarletteHTTPException
from starlette.responses import RedirectResponse
from starlette.staticfiles import StaticFiles

from . import get_version
from .pytrain_api import API_NAME, PyTrainApi
from .pytrain_component import (
    AuxOption,
    BellOption,
    Component,
    DialogOption,
    HornOption,
    OnOffOption,
    PyTrainAccessory,
    PyTrainComponent,
    PyTrainEngine,
    PyTrainSwitch,
    SmokeOption,
    SwitchPosition,
)
from .pytrain_info import (
    AccessoryInfo,
    Amc2LampCommand,
    Amc2MotorCommand,
    Asc2Command,
    AuxCommand,
    BellCommand,
    BlockInfo,
    Bpc2Command,
    EngineInfo,
    HornCommand,
    NumericCommand,
    ProductInfo,
    RelativeSpeedCommand,
    ResetCommand,
    RouteInfo,
    SpeedCommand,
    SwitchInfo,
    TrainInfo,
)

log = logging.getLogger(__name__)

E = TypeVar("E", bound=CommandDefEnum)
F = TypeVar("F", bound=Callable[..., Any])

DEFAULT_API_SERVER_VALUE = "[SERVER DOMAIN/IP ADDRESS NAME YOU GAVE TO ALEXA SKILL]"

# to get a secret key,
# openssl rand -hex 32
API_KEYS: dict[str, str] = dict()

# Load environment variables that drive behavior
load_dotenv(find_dotenv())
SECRET_KEY = os.environ.get("SECRET_KEY")
SECRET_PHRASE = os.environ.get("SECRET_PHRASE") if os.environ.get("SECRET_PHRASE") else "PYTRAINAPI"
API_TOKEN = os.environ.get("API_TOKEN")
UNSECURE_TOKENS = os.environ.get("UNSECURE_TOKENS")
ALGORITHM = os.environ.get("ALGORITHM")
API_SERVER = os.environ.get("API_SERVER")
ALEXA_TOKEN_EXP_MIN = os.environ.get("ALEXA_TOKEN_EXP_MIN")
if ALEXA_TOKEN_EXP_MIN is None or int(ALEXA_TOKEN_EXP_MIN) <= 0:
    ALEXA_TOKEN_EXP_MIN = 15
else:
    ALEXA_TOKEN_EXP_MIN = int(ALEXA_TOKEN_EXP_MIN)

if not API_SERVER or API_SERVER == DEFAULT_API_SERVER_VALUE:
    log.error("API_SERVER not set in .env; Alexa skill will not work")

# UNSECURE_TOKENS allows you to specify a comma-separated list of tokens that will bypass the API_TOKEN check.
# This is useful for testing, but should never be used in production.
if UNSECURE_TOKENS:
    tokens = UNSECURE_TOKENS.split(",")
    for token in tokens:
        token = token.strip()
        if token:
            API_KEYS[token] = token


class Token(BaseModel):
    access_token: str
    token_type: str


oauth2_scheme = OAuth2PasswordBearer(tokenUrl="token")


#
# This code is run when the uvicorn web server starts.
#
# noinspection PyUnusedLocal
@asynccontextmanager
async def lifespan(fapp: FastAPI):
    api = PyTrainApi.get()

    # register API server via zeroconf, enabling bonjour discovery
    await asyncio.to_thread(api.create_service)
    local_key = api.service_info.properties.get("Guid".encode("utf-8"), None)
    if local_key:
        local_key = local_key.decode("utf-8")
        API_KEYS[local_key] = local_key

    try:
        yield
    finally:
        # shutdown
        await asyncio.to_thread(api.shutdown_service)


app = FastAPI(
    title=f"{PROGRAM_NAME} API",
    description="Operate and control Lionel Legacy/TMCC engines, trains, switches, accessories, routes, "
    "and LCS components",
    version=get_version(),
    docs_url=None,
    lifespan=lifespan,
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


# def get_api_token(api_key: str = Security(api_key_header)) -> bool:
#     # see if it's a jwt token
#     try:
#         payload = jwt.decode(api_key, SECRET_KEY, algorithms=[ALGORITHM])
#     except InvalidSignatureError as e:
#         raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail=str(e))
#     except ExpiredSignatureError as es:
#         raise HTTPException(status_code=498, detail=str(es))
#     if payload:
#         if api_key and (api_key == API_TOKEN or api_key in API_KEYS) and payload.get("magic") == API_NAME:
#             return True
#         if payload.get("SERVER", None) == API_SERVER:
#             guid = payload.get("GUID", None)
#             if guid in API_KEYS and API_KEYS[guid] == api_key:
#                 return True
#             if guid:
#                 log.info(f"{guid} not in API Keys,but other info checks out")
#                 API_KEYS[guid] = api_key
#                 return True
#     log.warning(f"Invalid Access attempt: payload: {payload} key: {api_key}")
#     raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Missing or invalid API key")


def get_api_token(api_key: str = Security(api_key_header)) -> bool:
    """
    Accepts either:
      1) Raw API key (API_TOKEN, API_KEYS, etc.), OR
      2) A JWT (Authorization-style bearer token) signed with SECRET_KEY.

    Returns True if authorized, otherwise raises HTTPException.
    """

    # ---- normalize / basic checks ----
    if not api_key or not isinstance(api_key, str):
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Missing or invalid API key")

    api_key = api_key.strip()

    # If someone passed "Bearer <token>" in the header value, strip it.
    if api_key.lower().startswith("bearer "):
        api_key = api_key.split(" ", 1)[1].strip()

    # ---- path A: RAW API KEY ----
    # Treat anything not JWT-shaped as a raw key.
    is_jwt_shaped = api_key.count(".") == 2

    if not is_jwt_shaped:
        if api_key == API_TOKEN:
            return True

        # API_KEYS might be:
        #  - a dict {guid: key}
        #  - a set/list of keys
        try:
            if isinstance(API_KEYS, dict):
                if api_key in API_KEYS.values():
                    return True
            else:
                if api_key in API_KEYS:
                    return True
        except TypeError:
            # In case API_KEYS isn't iterable / is misconfigured
            pass

        log.warning(f"Invalid raw key access attempt: key={api_key!r}")
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Missing or invalid API key")

    # ---- path B: JWT ----
    try:
        payload = jwt.decode(api_key, SECRET_KEY, algorithms=[ALGORITHM])
    except ExpiredSignatureError as es:
        # Keep your 498 convention
        raise HTTPException(status_code=498, detail=str(es))
    except (InvalidSignatureError, DecodeError, InvalidTokenError) as e:
        # Covers "Not enough segments" and other malformed/invalid JWTs
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail=str(e))

    # ---- JWT authorization rules ----
    if not payload:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Missing or invalid API key")

    # 1) Magic/name check (you already had this)
    if payload.get("magic") == API_NAME:
        # For JWTs, consider them valid based on claims alone,
        # OR optionally also require the token string to be known.
        # Keeping your original behavior, but more flexible:
        if api_key == API_TOKEN:
            return True
        try:
            if isinstance(API_KEYS, dict):
                if api_key in API_KEYS.values():
                    return True
            else:
                if api_key in API_KEYS:
                    return True
        except TypeError:
            pass

        # If you want JWTs with correct signature+claims to be enough, uncomment this:
        return True

    # 2) Server/GUID flow you already had
    if payload.get("SERVER") == API_SERVER:
        guid = payload.get("GUID")
        if guid:
            if isinstance(API_KEYS, dict):
                # If we already have this GUID and it matches, accept
                if guid in API_KEYS and API_KEYS[guid] == api_key:
                    return True

                # If GUID exists but not stored yet, accept and store it
                log.info(f"{guid} not in API_KEYS (or mismatch); storing JWT for future requests")
                API_KEYS[guid] = api_key
                return True
            else:
                # If API_KEYS isn't dict, we can't do GUID->token mapping
                log.warning("API_KEYS is not a dict; cannot store GUID->token mapping for JWT auth")
                raise HTTPException(
                    status_code=status.HTTP_401_UNAUTHORIZED, detail="Server token mapping not supported"
                )

    log.warning(f"Invalid JWT access attempt: payload={payload} token={api_key!r}")
    raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Missing or invalid API key")


_CAMEL_RE = re.compile(r"(?<!^)(?=[A-Z])")


def _split_camel(word: str) -> str:
    """
    Split CamelCase words into space-separated words.
    Examples:
      VolumeUp -> Volume Up
      Aux1On   -> Aux1 On
    """
    return _CAMEL_RE.sub(" ", word)


def _operation_id_from_name(name: str) -> str:
    """
    Engine.FrontCoupler -> Engine_front_coupler
    Engine.VolumeUp     -> Engine_volume_up
    """
    parts = name.split(".")
    op_parts = []
    for p in parts:
        # split camel case, lowercase, join with underscores
        words = _split_camel(p).split()
        op_parts.append("_".join(w.lower() for w in words))
    return "_".join(op_parts)


def _summary_from_name(name: str) -> str:
    """
    Convert dotted endpoint names into human-friendly summaries.

    Examples:
      Engine.Forward      -> Engine Forward
      Engine.VolumeUp     -> Engine Volume Up
      Train.FrontCoupler  -> Train Front Coupler
    """
    parts = [p for p in name.split(".") if p]
    return " ".join(_split_camel(p) for p in parts)


def infer_category(name: str) -> str:
    return name.split(".", 1)[0] if name else "Misc"


def mobile_post(
    api: APIRouter,
    path: str,
    *,
    name: str,
    operation_id: str | None = None,
    summary: str | None = None,
    category: str | None = None,
    **kwargs: Any,
) -> Callable[[F], F]:
    operation_id = operation_id or _operation_id_from_name(name)
    summary = summary or _summary_from_name(name)
    category = category or infer_category(name)
    return api.post(
        path,
        tags=[f"Mobile.{category}"],
        name=name,
        operation_id=operation_id,
        summary=summary,
        **kwargs,
    )


def legacy_post(
    api: APIRouter,
    path: str,
    *,
    name: str,
    operation_id: str | None = None,
    summary: str | None = None,
    category: str | None = None,
    **kwargs: Any,
) -> Callable[[F], F]:
    if operation_id is None:
        operation_id = _operation_id_from_name(name)

    if summary is None:
        summary = _summary_from_name(name)

    category = category or infer_category(name)

    return api.post(
        path,
        tags=[f"Legacy.{category}"],
        operation_id=operation_id,
        name=name,
        summary=summary,
        **kwargs,
    )


router = APIRouter(prefix="/pytrain/v1", dependencies=[Depends(get_api_token)])
# router = APIRouter(prefix="/pytrain/v1")

FAVICON_PATH = None
APPLE_ICON_PATH = None
STATIC_DIR = find_dir("static", (".", "../"))
if STATIC_DIR:
    if os.path.isfile(f"{STATIC_DIR}/favicon.ico"):
        app.mount("/static", StaticFiles(directory=STATIC_DIR), name="static")
        FAVICON_PATH = f"{STATIC_DIR}/favicon.ico"
    if os.path.isfile(f"{STATIC_DIR}/apple-touch-icon.png"):
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
@app.exception_handler(ValidationError)
async def validation_exception_handler(request: Request, exc: ValidationError):
    if isinstance(exc, ValidationError):
        detail = ""
        for error in exc.errors():
            detail += "; " if detail else ""
            detail += error["msg"]
        detail = detail.replace("Value error, ", "")
    else:
        detail = str(exc)
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


@app.get("/docs", include_in_schema=False, tags=["Docs"])
async def swagger_ui_html():
    return get_swagger_ui_html(
        openapi_url="/openapi.json",
        title=f"{PROGRAM_NAME} API",
        swagger_favicon_url="/static/favicon.ico",
    )


@app.get("/pytrain", summary=f"Redirect to {API_NAME} Documentation", include_in_schema=False)
@app.get("/pytrain/v1", summary=f"Redirect to {API_NAME} Documentation", include_in_schema=False)
def pytrain_doc():
    return RedirectResponse(url="/docs", status_code=status.HTTP_301_MOVED_PERMANENTLY)


@router.get(
    "/system/halt",
    summary="Emergency Stop",
    description="Stops all engines and trains, in their tracks; turns off all power districts.",
    tags=["Legacy.System", "Mobile.System"],
    operation_id="system_halt",
    name="System.Halt",
)
async def halt():
    try:
        CommandReq(TMCC1HaltCommandEnum.HALT).send()
        return {"status": "HALT command sent"}
    except Exception as e:
        raise HTTPException(status_code=400, detail=str(e))


@legacy_post(
    router,
    "/system/debug_req",
    summary="Enable/Disable Debugging Mode",
    description=f"Enable/disable {PROGRAM_NAME} debugging mode. ",
    name="System.DebugReq",
)
@mobile_post(
    router,
    "/system/debug",
    summary="Enable/Disable Debugging Mode",
    description=f"Enable/disable {PROGRAM_NAME} debugging mode. ",
    name="System.Debug",
)
async def debug(on: bool = True):
    PyTrainApi.get().pytrain.queue_command(f"debug {'on' if on else 'off'}")
    return {"status": f"Debugging {'enabled' if on else 'disabled'}"}


@legacy_post(
    router,
    "/system/echo_req",
    summary="Enable/Disable Command Echoing",
    description=f"Enable/disable echoing of {PROGRAM_NAME} commands to log file. ",
    name="System.EchoReq",
)
@mobile_post(
    router,
    "/system/echo",
    summary="Enable/Disable Command Echoing",
    description=f"Enable/disable echoing of {PROGRAM_NAME} commands to log file. ",
    name="System.Echo",
)
async def echo(on: bool = True):
    PyTrainApi.get().pytrain.queue_command(f"echo {'on' if on else 'off'}")
    return {"status": f"Echo {'enabled' if on else 'disabled'}"}


@legacy_post(
    router,
    "/system/reboot_req",
    summary=f"Reboot {PROGRAM_NAME}",
    description=f"Reboot {PROGRAM_NAME} server and all clients.",
    name="System.RebootReq",
)
@mobile_post(
    router,
    "/system/reboot",
    summary=f"Reboot {PROGRAM_NAME}",
    description=f"Reboot {PROGRAM_NAME} server and all clients.",
    name="System.Reboot",
)
async def reboot():
    try:
        CommandReq(TMCC1SyncCommandEnum.REBOOT).send()
        return {"status": "REBOOT command sent"}
    except Exception as e:
        raise HTTPException(status_code=400, detail=str(e))


@legacy_post(
    router,
    "/system/restart_req",
    summary=f"Restart {PROGRAM_NAME}",
    description=f"Restart {PROGRAM_NAME} server and all clients.",
    name="System.RestartReq",
)
@mobile_post(
    router,
    "/system/restart",
    summary=f"Restart {PROGRAM_NAME}",
    description=f"Restart {PROGRAM_NAME} server and all clients.",
    name="System.Restart",
)
async def restart():
    try:
        CommandReq(TMCC1SyncCommandEnum.RESTART).send()
        return {"status": "RESTART command sent"}
    except Exception as e:
        raise HTTPException(status_code=400, detail=str(e))


@legacy_post(
    router,
    "/system/resync_req",
    summary="Resynchronize with Base 3",
    description="Reload all state information from your Lionel Base 3.",
    name="System.ResyncReq",
)
@mobile_post(
    router,
    "/system/resync",
    summary="Resynchronize with Base 3",
    description="Reload all state information from your Lionel Base 3.",
    name="System.Resync",
)
async def resync():
    try:
        CommandReq(TMCC1SyncCommandEnum.RESYNC).send()
        return {"status": "RESYNC command sent"}
    except Exception as e:
        raise HTTPException(status_code=400, detail=str(e))


@legacy_post(
    router,
    "/system/shutdown_req",
    summary=f"Shutdown {PROGRAM_NAME}",
    description=f"Shutdown {PROGRAM_NAME} server and all clients.",
    name="System.ShutdownReq",
)
@mobile_post(
    router,
    "/system/shutdown",
    summary=f"Shutdown {PROGRAM_NAME}",
    description=f"Shutdown {PROGRAM_NAME} server and all clients.",
    name="System.Shutdown",
)
async def shutdown():
    try:
        CommandReq(TMCC1SyncCommandEnum.SHUTDOWN).send()
        return {"status": "SHUTDOWN command sent"}
    except Exception as e:
        raise HTTPException(status_code=400, detail=str(e))


@legacy_post(router, "/system/stop_all_req", name="System.StopAllReq", summary="Stop All Engines and Trains")
@mobile_post(router, "/system/stop_all", name="System.StopAll", summary="Stop All Engines and Trains")
async def stop_all():
    CommandReq(TMCC1EngineCommandEnum.STOP_IMMEDIATE, 99).send()
    CommandReq(TMCC2EngineCommandEnum.STOP_IMMEDIATE, 99, scope=CommandScope.TRAIN).send()
    return {"status": "Sent 'stop' command to all engines and trains..."}


@legacy_post(
    router,
    "/system/update_req",
    summary=f"Update {API_NAME}",
    description=f"Update {API_NAME} software from PyPi or Git Hub repository.",
    name="System.UpdateReq",
)
@mobile_post(
    router,
    "/system/update",
    summary=f"Update {API_NAME}",
    description=f"Update {API_NAME} software from PyPi or Git Hub repository.",
    name="System.Update",
)
async def update():
    try:
        CommandReq(TMCC1SyncCommandEnum.UPDATE).send()
        return {"status": "UPDATE command sent"}
    except Exception as e:
        raise HTTPException(status_code=400, detail=str(e))


@legacy_post(
    router,
    "/system/version_req",
    summary=f"Get {API_NAME} Version",
    description=f"Get {API_NAME} software version.",
    name="System.VersionReq",
)
@mobile_post(
    router,
    "/system/version",
    summary=f"Get {API_NAME} Version",
    description=f"Get {API_NAME} software version.",
    name="System.Version",
)
async def get_version():
    try:
        from . import get_version

        return {
            "pytrain": pytrain_get_version(),
            "pytrain_api": get_version(),
        }
    except Exception as e:
        raise HTTPException(status_code=400, detail=str(e))


@router.post(
    "/{component}/{tmcc_id:int}/cli_req",
    summary=f"Send {PROGRAM_NAME} CLI command",
    description=f"Send a {PROGRAM_NAME} CLI command to control trains, switches, and accessories.",
    include_in_schema=False,
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
        contains = contains.lower() if contains else None
        for state in states:
            if is_legacy is not None and state.is_legacy != is_legacy:
                continue
            if is_tmcc is not None and state.is_tmcc != is_tmcc:
                continue
            # noinspection PyUnresolvedReferences
            if contains and state and contains not in str(state).lower():
                continue
            ret.append(state.as_dict())
        if not ret:
            headers = {"X-Error": "404"}
            raise HTTPException(status_code=404, headers=headers, detail=f"No matching {scope.label} found")
        return ret


@router.get(
    "/accessories",
    tags=["Legacy.Accessory", "Mobile.Accessory"],
    operation_id="accessories_list",
    name="Accessories.List",
    summary="List all accessories",
)
async def get_accessories(contains: str = None) -> list[AccessoryInfo]:
    return [AccessoryInfo(**d) for d in get_components(CommandScope.ACC, contains=contains)]


@cbv(router)
class Accessory(PyTrainAccessory):
    def __init__(self):
        super().__init__(CommandScope.ACC)

    @router.get(
        "/accessory/{tmcc_id:int}",
        tags=["Legacy.Accessory", "Mobile.Accessory"],
        operation_id="accessory_get",
        name="Accessory.Get",
        summary="Get accessory state",
    )
    async def get_accessory(
        self,
        tmcc_id: Annotated[int, PyTrainComponent.id_path(label="Accessory")],
    ) -> AccessoryInfo:
        return AccessoryInfo(**super().get(tmcc_id))

    @legacy_post(router, "/accessory/{tmcc_id:int}/amc2_motor_req", name="Accessory.Amc2MotorReq")
    async def amc2_motor_req(
        self,
        tmcc_id: Annotated[int, PyTrainComponent.id_path(label="Accessory")],
        motor: Annotated[int, Query(description="Motor (1 - 2)", ge=1, le=2)],
        state: Annotated[OnOffOption | None, Query(description="On or Off")] = None,
        speed: Annotated[int | None, Query(description="Speed (0 - 100)", ge=0, le=100)] = None,
    ):
        return self.amc2_motor(tmcc_id, motor, state, speed)

    @mobile_post(router, "/accessory/{tmcc_id:int}/amc2_motor", name="Accessory.Amc2Motor")
    async def amc2_motor_cmd(
        self,
        tmcc_id: Annotated[int, PyTrainComponent.id_path(label="Accessory")],
        cmd: Amc2MotorCommand = Body(...),
    ):
        motor = cmd.motor
        state = cmd.state if cmd.mode == "state" else None
        speed = cmd.speed if cmd.mode == "speed" else None
        strict = cmd.strict
        return self.amc2_motor(tmcc_id, motor, state, speed, strict=strict)

    @legacy_post(router, "/accessory/{tmcc_id:int}/amc2_lamp_req", name="Accessory.Amc2LampReq")
    async def amc2_lamp_req(
        self,
        tmcc_id: Annotated[int, PyTrainComponent.id_path(label="Accessory")],
        lamp: Annotated[int, Query(description="Lamp (1 - 4)", ge=1, le=4)],
        state: Annotated[OnOffOption | None, Query(description="On or Off")] = None,
        level: Annotated[int | None, Query(description="Brightness Level (0 - 100)", ge=0, le=100)] = None,
    ):
        return self.amc2_lamp(tmcc_id, lamp, state, level)

    @mobile_post(router, "/accessory/{tmcc_id:int}/amc2_lamp", name="Accessory.Amc2Lamp")
    async def amc2_lamp_cmd(
        self,
        tmcc_id: Annotated[int, PyTrainComponent.id_path(label="Accessory")],
        cmd: Amc2LampCommand = Body(...),
    ):
        lamp = cmd.lamp
        state = cmd.state if cmd.mode == "state" else None
        level = cmd.level if cmd.mode == "level" else None
        strict = cmd.strict
        return self.amc2_lamp(tmcc_id, lamp, state, level, strict=strict)

    @legacy_post(router, "/accessory/{tmcc_id:int}/asc2_req", name="Accessory.Asc2Req")
    async def asc2_req(
        self,
        tmcc_id: Annotated[int, PyTrainComponent.id_path(label="Accessory")],
        state: Annotated[OnOffOption | None, Query(description="On or Off")],
        duration: Annotated[float | None, Query(description="Duration (seconds)", gt=0.0)] = None,
    ):
        return super().asc2(tmcc_id, state, duration)

    @mobile_post(router, "/accessory/{tmcc_id:int}/asc2", name="Accessory.Asc2")
    async def asc2_cmd(
        self,
        tmcc_id: Annotated[int, PyTrainComponent.id_path(label="Accessory")],
        cmd: Asc2Command = Body(...),
    ):
        state = cmd.state
        duration = cmd.duration
        strict = cmd.strict
        return super().asc2(tmcc_id, state, duration, strict=strict)

    @mobile_post(router, "/accessory/{tmcc_id:int}/aux", name="Accessory.Aux")
    async def aux_cmd(
        self,
        tmcc_id: Annotated[int, PyTrainComponent.id_path(label="Accessory")],
        cmd: AuxCommand = Body(...),
    ):
        return super().aux(tmcc_id, cmd.aux_req, cmd.number, cmd.duration)

    @legacy_post(router, "/accessory/{tmcc_id:int}/boost_req", name="Accessory.BoostReq")
    @mobile_post(router, "/accessory/{tmcc_id:int}/boost", name="Accessory.Boost")
    async def boost(
        self,
        tmcc_id: Annotated[int, PyTrainComponent.id_path(label="Accessory")],
        duration: Annotated[float | None, Query(description="Duration (seconds)", gt=0.0)] = None,
    ):
        return super().boost(tmcc_id, duration)

    @legacy_post(router, "/accessory/{tmcc_id:int}/brake_req", name="Accessory.BrakeReq")
    @mobile_post(router, "/accessory/{tmcc_id:int}/brake", name="Accessory.Brake")
    async def brake(
        self,
        tmcc_id: Annotated[int, PyTrainComponent.id_path(label="Accessory")],
        duration: Annotated[float | None, Query(description="Duration (seconds)", gt=0.0)] = None,
    ):
        return super().brake(tmcc_id, duration)

    @legacy_post(router, "/accessory/{tmcc_id:int}/bpc2_req", name="Accessory.Bpc2Req")
    async def bpc2_req(
        self,
        tmcc_id: Annotated[int, PyTrainComponent.id_path(label="Accessory")],
        state: Annotated[OnOffOption, Query(description="On or Off")],
    ):
        return self.bpc2(tmcc_id, state)

    @mobile_post(router, "/accessory/{tmcc_id:int}/bpc", name="Accessory.Bpc2")
    async def bpc2_cmd(
        self,
        tmcc_id: Annotated[int, PyTrainComponent.id_path(label="Accessory")],
        cmd: Bpc2Command = Body(...),
    ):
        state = cmd.state
        strict = cmd.strict
        return super().bpc2(tmcc_id, state, strict=strict)

    @legacy_post(router, "/accessory/{tmcc_id:int}/front_coupler_req", name="Accessory.FrontCouplerReq")
    @mobile_post(router, "/accessory/{tmcc_id:int}/front_coupler", name="Accessory.FrontCoupler")
    async def front_coupler(
        self,
        tmcc_id: Annotated[int, PyTrainComponent.id_path(label="Accessory")],
        duration: Annotated[float | None, Query(description="Duration (seconds)", gt=0.0)] = None,
    ):
        return self.open_coupler(tmcc_id, TMCC1AuxCommandEnum.FRONT_COUPLER, duration)

    @legacy_post(router, "/accessory/{tmcc_id:int}/numeric_req", name="Accessory.NumericReq")
    async def numeric_req(
        self,
        tmcc_id: Annotated[int, PyTrainComponent.id_path(label="Accessory")],
        number: Annotated[int | None, Query(description="Number (0 - 9)", ge=0, le=9)] = None,
        duration: Annotated[float | None, Query(description="Duration (seconds)", gt=0.0)] = None,
    ):
        return self.do_numeric(TMCC1AuxCommandEnum.NUMERIC, tmcc_id, number, duration)

    @mobile_post(router, "/accessory/{tmcc_id:int}/numeric", name="Accessory.Numeric")
    async def numeric_cmd(
        self,
        tmcc_id: Annotated[int, PyTrainComponent.id_path(label="Accessory")],
        cmd: Annotated[NumericCommand, Body(...)],
    ):
        return self.do_numeric(TMCC1AuxCommandEnum.NUMERIC, tmcc_id, cmd.number, cmd.duration)

    @legacy_post(router, "/accessory/{tmcc_id:int}/rear_coupler_req", name="Accessory.RearCouplerReq")
    @mobile_post(router, "/accessory/{tmcc_id:int}/rear_coupler", name="Accessory.RearCoupler")
    async def rear_coupler(
        self,
        tmcc_id: Annotated[int, PyTrainComponent.id_path(label="Accessory")],
        duration: Annotated[float | None, Query(description="Duration (seconds)", gt=0.0)] = None,
    ):
        return self.open_coupler(tmcc_id, TMCC1AuxCommandEnum.REAR_COUPLER, duration)

    @legacy_post(router, "/accessory/{tmcc_id:int}/speed_req/{speed}", name="Accessory.SpeedReq")
    async def speed(
        self,
        tmcc_id: Annotated[int, PyTrainComponent.id_path(label="Accessory")],
        speed: Annotated[int, Path(description="Relative speed (-5 - 5)", ge=-5, le=5)],
        duration: Annotated[float | None, Query(description="Duration (seconds)", gt=0.0)] = None,
    ):
        return self.relative_speed(tmcc_id, speed, duration)

    @mobile_post(router, "/accessory/{tmcc_id:int}/speed", name="Accessory.Speed")
    async def speed_cmd(
        self,
        tmcc_id: Annotated[int, PyTrainComponent.id_path(label="Accessory")],
        cmd: RelativeSpeedCommand = Body(...),
    ):
        return self.relative_speed(tmcc_id, cmd.speed, cmd.duration)

    @legacy_post(router, "/accessory/{tmcc_id:int}/{aux_req}", name="Accessory.AuxReq")
    async def operate_accessory(
        self,
        tmcc_id: Annotated[int, PyTrainComponent.id_path(label="Accessory")],
        aux_req: Annotated[AuxOption, Path(description="Aux 1, Aux2, or Aux 3")],
        duration: Annotated[float | None, Query(description="Duration (seconds)", gt=0.0)] = None,
    ):
        return self.aux(tmcc_id, aux_req, None, duration)


@router.get(
    "/blocks",
    tags=["Legacy.Block", "Mobile.Block"],
    operation_id="blocks_list",
    name="Blocks.List",
    summary="List all blocks",
    response_model=list[BlockInfo],
)
async def get_blocks(contains: str = None) -> list[BlockInfo]:
    return [BlockInfo(**d) for d in get_components(CommandScope.BLOCK, contains=contains)]


@cbv(router)
class Block(PyTrainComponent):
    # noinspection PyTypeHints
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

    @router.get(
        "/block/{block_id}",
        tags=["Legacy.Block", "Mobile.Block"],
        operation_id="block_get",
        name="Block.Get",
        summary="Get block state",
        response_model=BlockInfo,
    )
    async def get_block(
        self,
        block_id: Annotated[int, PyTrainComponent.id_path(label="Block")],
    ) -> BlockInfo:
        return BlockInfo(**super().get(block_id))


@router.get(
    "/engines",
    tags=["Legacy.Engine", "Mobile.Engine"],
    operation_id="engines_list",
    name="Engines.List",
    summary="List all engines",
    response_model=list[EngineInfo],
)
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
    # noinspection PyTypeHints
    @classmethod
    def id_path(cls, label: str = "Engine", min_val: int = 1, max_val: int = 9999) -> Path:
        label = label if label else cls.__name__.replace("PyTrain", "")
        return Path(
            title="TMCC ID",
            description=f"{label}'s TMCC ID",
            ge=min_val,
            le=max_val,
        )

    def __init__(self):
        super().__init__(CommandScope.ENGINE)

    @router.get(
        "/engine/{tmcc_id:int}",
        tags=["Legacy.Engine", "Mobile.Engine"],
        operation_id="engine_get",
        name="Engine.Get",
        summary="Get engine state",
        response_model=EngineInfo,
    )
    async def get_engine(
        self,
        tmcc_id: Annotated[int, Engine.id_path()],
    ) -> EngineInfo:
        return EngineInfo(**super().get(tmcc_id))

    @mobile_post(router, "/engine/{tmcc_id:int}/aux", name="Engine.Aux")
    async def aux_cmd(
        self,
        tmcc_id: Annotated[int, Engine.id_path()],
        cmd: AuxCommand = Body(...),
    ):
        return super().aux(tmcc_id, cmd.aux_req, cmd.number, cmd.duration)

    @legacy_post(router, "/engine/{tmcc_id:int}/bell_req", name="Engine.BellReq")
    async def ring_bell_req(
        self,
        tmcc_id: Annotated[int, Engine.id_path()],
        option: Annotated[
            BellOption | None,
            Query(description="Bell effect (omit to toggle)"),
        ] = None,
        duration: Annotated[
            float | None,
            Query(description="Duration (seconds, only with 'once' option)", gt=0.0),
        ] = None,
    ):
        return super().ring_bell(tmcc_id, option, duration)

    @mobile_post(router, "/engine/{tmcc_id:int}/bell", name="Engine.Bell")
    async def ring_bell_cmd(
        self,
        tmcc_id: Annotated[int, Engine.id_path()],
        cmd: Annotated[BellCommand, Body(..., discriminator="option")],
    ):
        option = cmd.option
        duration = getattr(cmd, "duration", None)
        ding = getattr(cmd, "ding", None)
        return super().ring_bell(tmcc_id, option, duration, ding)

    @legacy_post(router, "/engine/{tmcc_id:int}/boost_req", name="Engine.BoostReq")
    @mobile_post(router, "/engine/{tmcc_id:int}/boost", name="Engine.Boost")
    async def boost(
        self,
        tmcc_id: Annotated[int, Engine.id_path()],
        duration: Annotated[float | None, Query(description="Duration (seconds)", gt=0.0)] = None,
    ):
        return super().boost(tmcc_id, duration)

    @legacy_post(router, "/engine/{tmcc_id:int}/brake_req", name="Engine.BrakeReq")
    @mobile_post(router, "/engine/{tmcc_id:int}/brake", name="Engine.Brake")
    async def brake(
        self,
        tmcc_id: Annotated[int, Engine.id_path()],
        duration: Annotated[float | None, Query(description="Duration (seconds)", gt=0.0)] = None,
    ):
        return super().brake(tmcc_id, duration)

    @legacy_post(router, "/engine/{tmcc_id:int}/dialog_req", name="Engine.DialogReq")
    @mobile_post(router, "/engine/{tmcc_id:int}/dialog", name="Engine.Dialog")
    async def dialog_req(
        self,
        tmcc_id: Annotated[int, Engine.id_path()],
        dialog: DialogOption = Query(..., description="Dialog effect"),
    ):
        return self.dialog(tmcc_id, dialog)

    @legacy_post(router, "/engine/{tmcc_id:int}/forward_req", name="Engine.ForwardReq")
    @mobile_post(router, "/engine/{tmcc_id:int}/forward", name="Engine.Forward")
    async def forward_req(
        self,
        tmcc_id: Annotated[int, Engine.id_path()],
    ):
        return super().forward(tmcc_id)

    @legacy_post(router, "/engine/{tmcc_id:int}/front_coupler_req", name="Engine.FrontCouplerReq")
    @mobile_post(router, "/engine/{tmcc_id:int}/front_coupler", name="Engine.FrontCoupler")
    async def front_coupler(
        self,
        tmcc_id: Annotated[int, Engine.id_path()],
    ):
        return super().front_coupler(tmcc_id)

    @legacy_post(router, "/engine/{tmcc_id:int}/horn_req", name="Engine.HornReq")
    async def blow_horn_req(
        self,
        tmcc_id: Annotated[int, Engine.id_path()],
        option: Annotated[HornOption, Query(description="Horn/whistle effect")],
        intensity: Annotated[
            int | None,
            Query(
                description="Quilling horn intensity (Legacy engines only)",
                ge=0,
                le=15,
            ),
        ] = 10,
        duration: Annotated[
            float | None,
            Query(description="Duration (seconds)", gt=0.0),
        ] = None,
    ):
        return super().blow_horn(tmcc_id, option, intensity, duration)

    @mobile_post(router, "/engine/{tmcc_id:int}/horn", name="Engine.Horn")
    async def blow_horn_cmd(
        self,
        tmcc_id: Annotated[int, Engine.id_path()],
        cmd: Annotated[HornCommand, Body(..., discriminator="option")],
    ):
        option = cmd.option
        intensity = getattr(cmd, "intensity", None)
        duration = getattr(cmd, "duration", None)
        return super().blow_horn(tmcc_id, option, intensity, duration)

    @router.get(
        "/engine/{tmcc_id:int}/info",
        tags=["Legacy.Engine", "Mobile.Engine"],
        name="Engine.Info",
        operation_id="engine_info",
        summary="Get engine product information",
    )
    async def get_info(
        self,
        tmcc_id: Annotated[int, Engine.id_path()],
    ) -> ProductInfo:
        return ProductInfo(**super().get_engine_info(tmcc_id))

    @legacy_post(router, "/engine/{tmcc_id:int}/momentum_req", name="Engine.MomentumReq")
    @mobile_post(router, "/engine/{tmcc_id:int}/momentum", name="Engine.Momentum")
    async def momentum(
        self,
        tmcc_id: Annotated[int, Engine.id_path()],
        level: int = Query(..., ge=0, le=7, description="Momentum level (0 - 7)"),
    ):
        return super().momentum(tmcc_id, level)

    @legacy_post(router, "/engine/{tmcc_id:int}/numeric_req", name="Engine.NumericReq")
    async def numeric_req(
        self,
        tmcc_id: Annotated[int, Engine.id_path()],
        number: Annotated[int | None, Query(description="Number (0 - 9)", ge=0, le=9)],
        duration: Annotated[float | None, Query(description="Duration (seconds)", gt=0.0)] = None,
    ):
        return super().numeric(tmcc_id, number, duration)

    @mobile_post(router, "/engine/{tmcc_id:int}/numeric", name="Engine.Numeric")
    async def numeric_cmd(
        self,
        tmcc_id: Annotated[int, Engine.id_path()],
        cmd: Annotated[NumericCommand, Body(...)],
    ):
        return super().numeric(tmcc_id, cmd.number, cmd.duration)

    @legacy_post(router, "/engine/{tmcc_id:int}/rear_coupler_req", name="Engine.RearCouplerReq")
    @mobile_post(router, "/engine/{tmcc_id:int}/rear_coupler", name="Engine.RearCoupler")
    async def rear_coupler(
        self,
        tmcc_id: Annotated[int, Engine.id_path()],
    ):
        return super().rear_coupler(tmcc_id)

    @legacy_post(router, "/engine/{tmcc_id:int}/reset_req", name="Engine.ResetReq")
    async def reset_req(
        self,
        tmcc_id: Annotated[int, Engine.id_path()],
        hold: Annotated[bool, Query(title="refuel", description="If true, perform refuel operation")] = False,
        duration: Annotated[int | None, Query(description="Refueling time (seconds)", ge=3)] = 3,
    ):
        duration = (duration if duration and duration >= 3 else 3) if hold else None
        return super().reset(tmcc_id, duration)

    @mobile_post(router, "/engine/{tmcc_id:int}/reset", name="Engine.Reset")
    async def reset_cmd(
        self,
        tmcc_id: Annotated[int, Engine.id_path()],
        cmd: ResetCommand | None = Body(None),
    ):
        # Apply defaults if body omitted
        if cmd is None:
            cmd = ResetCommand.model_validate({})
        duration = (cmd.duration if cmd.duration and cmd.duration >= 3 else 3) if cmd.hold else None
        return super().reset(tmcc_id, duration=duration)

    @legacy_post(router, "/engine/{tmcc_id:int}/reverse_req", name="Engine.ReverseReq")
    @mobile_post(router, "/engine/{tmcc_id:int}/reverse", name="Engine.Reverse")
    async def reverse(
        self,
        tmcc_id: Annotated[int, Engine.id_path()],
    ):
        return super().reverse(tmcc_id)

    @legacy_post(router, "/engine/{tmcc_id:int}/shutdown_req", name="Engine.ShutdownReq")
    @mobile_post(router, "/engine/{tmcc_id:int}/shutdown", name="Engine.Shutdown")
    async def shutdown(
        self,
        tmcc_id: Annotated[int, Engine.id_path()],
        dialog: bool = Query(False, description="If true, include shutdown dialog"),
    ):
        return super().shutdown(tmcc_id, dialog=dialog)

    @legacy_post(router, "/engine/{tmcc_id:int}/smoke_level_req", name="Engine.SmokeLevelReq")
    @legacy_post(router, "/engine/{tmcc_id:int}/smoke_req", name="Engine.SmokeReq")
    @mobile_post(router, "/engine/{tmcc_id:int}/smoke", name="Engine.Smoke")
    async def smoke(
        self,
        tmcc_id: Annotated[int, Engine.id_path()],
        level: SmokeOption = Query(..., description="Smoke output level"),
    ):
        return super().smoke(tmcc_id, level=level)

    @legacy_post(router, "/engine/{tmcc_id:int}/speed_req/{speed}", name="Engine.SpeedReq")
    async def speed_req(
        self,
        tmcc_id: Annotated[int, Engine.id_path()],
        speed: Annotated[
            int | str,
            Path(description="New speed (0 to 195, roll, restricted, slow, medium, limited, normal, highball)"),
        ],
        immediate: bool = None,
        dialog: bool = None,
    ):
        return super().speed(tmcc_id, speed, immediate=immediate, dialog=dialog)

    @mobile_post(router, "/engine/{tmcc_id:int}/speed", name="Engine.Speed")
    async def speed_cmd(
        self,
        tmcc_id: Annotated[int, Engine.id_path()],
        cmd: SpeedCommand = Body(...),
    ):
        return await self._set_speed(tmcc_id, cmd.speed, cmd.immediate, cmd.dialog)

    @legacy_post(router, "/engine/{tmcc_id:int}/startup_req", name="Engine.StartupReq")
    @mobile_post(router, "/engine/{tmcc_id:int}/startup", name="Engine.Startup")
    async def startup_cmd(
        self,
        tmcc_id: Annotated[int, Engine.id_path()],
        dialog: bool = Query(False, description="If true, include startup dialog"),
    ):
        return super().startup(tmcc_id, dialog=dialog)

    @legacy_post(router, "/engine/{tmcc_id:int}/stop_req", name="Engine.StopReq")
    @mobile_post(router, "/engine/{tmcc_id:int}/stop", name="Engine.Stop")
    async def stop(
        self,
        tmcc_id: Annotated[int, Engine.id_path()],
    ):
        return super().stop(tmcc_id)

    @legacy_post(router, "/engine/{tmcc_id:int}/toggle_direction_req", name="Engine.ToggleDirectionReq")
    @mobile_post(router, "/engine/{tmcc_id:int}/toggle_direction", name="Engine.ToggleDirection")
    async def toggle_direction(
        self,
        tmcc_id: Annotated[int, Engine.id_path()],
    ):
        return super().toggle_direction(tmcc_id)

    @legacy_post(router, "/engine/{tmcc_id:int}/volume_down_req", name="Engine.VolumeDownReq")
    @mobile_post(router, "/engine/{tmcc_id:int}/volume_down", name="Engine.VolumeDown")
    async def volume_down(
        self,
        tmcc_id: Annotated[int, Engine.id_path()],
    ):
        return super().volume_down(tmcc_id)

    @legacy_post(router, "/engine/{tmcc_id:int}/volume_up_req", name="Engine.VolumeUpReq")
    @mobile_post(router, "/engine/{tmcc_id:int}/volume_up", name="Engine.VolumeUp")
    async def volume_up(
        self,
        tmcc_id: Annotated[int, Engine.id_path()],
    ):
        return super().volume_up(tmcc_id)

    @legacy_post(router, "/engine/{tmcc_id:int}/{aux_req}", name="Engine.AuxReq")
    async def aux_req(
        self,
        tmcc_id: Annotated[int, Engine.id_path()],
        aux_req: Annotated[AuxOption, Path(description="Aux 1, Aux2, or Aux 3")],
        number: Annotated[int | None, Query(description="Number (0 - 9)", ge=0, le=9)] = None,
        duration: Annotated[float | None, Query(description="Duration (seconds)", gt=0.0)] = None,
    ):
        return super().aux(tmcc_id, aux_req, number, duration)


@router.get(
    "/routes",
    tags=["Legacy.Route", "Mobile.Route"],
    operation_id="routes_list",
    name="Routes.List",
    summary="List all routes",
    response_model=list[RouteInfo],
)
async def get_routes(contains: str = None):
    return [RouteInfo(**d) for d in get_components(CommandScope.ROUTE, contains=contains)]


@cbv(router)
class Route(PyTrainComponent):
    def __init__(self):
        super().__init__(CommandScope.ROUTE)

    @router.get(
        "/route/{tmcc_id:int}",
        tags=["Legacy.Route", "Mobile.Route"],
        operation_id="route_get",
        name="Route.Get",
        summary="Get route state",
        response_model=RouteInfo,
    )
    async def get_route(self, tmcc_id: Annotated[int, PyTrainComponent.id_path(label="Route")]):
        return RouteInfo(**super().get(tmcc_id))

    @legacy_post(router, "/route/{tmcc_id:int}/fire_req", name="Route.FireReq")
    @mobile_post(router, "/route/{tmcc_id:int}/fire", name="Route.Fire")
    async def fire(
        self,
        tmcc_id: Annotated[int, PyTrainComponent.id_path(label="Route")],
    ):
        self.do_request(TMCC1RouteCommandEnum.FIRE, tmcc_id)
        return {"status": f"{self.scope.title} {tmcc_id:int} fired"}


@router.get(
    "/switches",
    tags=["Legacy.Switch", "Mobile.Switch"],
    operation_id="switches_list",
    name="Switches.List",
    summary="List all switches",
    response_model=list[SwitchInfo],
)
async def get_switches(contains: str = None):
    return [SwitchInfo(**d) for d in get_components(CommandScope.SWITCH, contains=contains)]


@cbv(router)
class Switch(PyTrainSwitch):
    def __init__(self):
        super().__init__(CommandScope.SWITCH)

    @router.get(
        "/switch/{tmcc_id:int}",
        tags=["Legacy.Switch", "Mobile.Switch"],
        operation_id="switch_get",
        name="Switch.Get",
        summary="Get switch state",
        response_model=SwitchInfo,
    )
    async def get_switch(
        self,
        tmcc_id: Annotated[int, PyTrainComponent.id_path(label="Switch")],
    ) -> SwitchInfo:
        return SwitchInfo(**super().get(tmcc_id))

    @legacy_post(router, "/switch/{tmcc_id:int}/thru_req", name="Switch.ThruReq", summary="Throw switch thru")
    async def thru(
        self,
        tmcc_id: Annotated[int, PyTrainComponent.id_path(label="Switch")],
    ):
        return self.throw(tmcc_id, SwitchPosition.THRU)

    @legacy_post(router, "/switch/{tmcc_id:int}/out_req", name="Switch.OutReq", summary="Throw switch out")
    async def out(
        self,
        tmcc_id: Annotated[int, PyTrainComponent.id_path(label="Switch")],
    ):
        return self.throw(tmcc_id, SwitchPosition.OUT)

    @legacy_post(router, "/switch/{tmcc_id:int}/throw_req", name="Switch.ThrowReq", summary="Throw switch thru or out")
    @mobile_post(router, "/switch/{tmcc_id:int}/throw", name="Switch.Throw", summary="Throw switch thru or out")
    async def throw_cmd(
        self,
        tmcc_id: Annotated[int, PyTrainComponent.id_path(label="Switch")],
        position: Annotated[
            SwitchPosition,
            Query(description="Desired switch position"),
        ],
    ):
        return self.throw(tmcc_id, position)


@router.get(
    "/trains",
    tags=["Legacy.Train", "Mobile.Train"],
    operation_id="trains_list",
    name="Trains.List",
    summary="List all trains",
    response_model=list[TrainInfo],
)
async def get_trains(contains: str = None, is_legacy: bool = None, is_tmcc: bool = None) -> list[EngineInfo]:
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
    # noinspection PyTypeHints
    @classmethod
    def id_path(cls, label: str = "Train", min_val: int = 1, max_val: int = 9999) -> Path:
        label = label if label else cls.__name__.replace("PyTrain", "")
        return Path(
            title="TMCC ID",
            description=f"{label}'s TMCC ID",
            ge=min_val,
            le=max_val,
        )

    def __init__(self):
        super().__init__(CommandScope.TRAIN)

    @router.get(
        "/train/{tmcc_id:int}",
        tags=["Legacy.Train", "Mobile.Train"],
        operation_id="train_get",
        name="Train.Get",
        summary="Get train state",
        response_model=TrainInfo,
    )
    async def get_train(
        self,
        tmcc_id: Annotated[int, Train.id_path()],
    ):
        return TrainInfo(**super().get(tmcc_id))

    @mobile_post(router, "/train/{tmcc_id:int}/aux", name="Train.Aux")
    async def aux_cmd(
        self,
        tmcc_id: Annotated[int, Train.id_path()],
        cmd: AuxCommand = Body(...),
    ):
        return super().aux(tmcc_id, cmd.aux_req, cmd.number, cmd.duration)

    @legacy_post(router, "/train/{tmcc_id:int}/bell_req", name="Train.BellReq")
    async def ring_bell_req(
        self,
        tmcc_id: Annotated[int, Train.id_path()],
        option: Annotated[
            BellOption | None,
            Query(description="Bell effect"),
        ] = None,
        duration: Annotated[
            float | None,
            Query(description="Duration (seconds, only with 'once' option)", gt=0.0),
        ] = None,
    ):
        return super().ring_bell(tmcc_id, option, duration)

    @mobile_post(router, "/train/{tmcc_id:int}/bell", name="Train.Bell")
    async def ring_bell_cmd(
        self,
        tmcc_id: Annotated[int, Train.id_path()],
        cmd: Annotated[BellCommand, Body(..., discriminator="option")],
    ):
        option = cmd.option
        duration = getattr(cmd, "duration", None)
        ding = getattr(cmd, "ding", None)
        return super().ring_bell(tmcc_id, option, duration, ding)

    @legacy_post(router, "/train/{tmcc_id:int}/boost_req", name="Train.BoostReq")
    @mobile_post(router, "/train/{tmcc_id:int}/boost", name="Train.Boost")
    async def boost(
        self,
        tmcc_id: Annotated[int, Train.id_path()],
        duration: Annotated[float | None, Query(description="Duration (seconds)", gt=0.0)] = None,
    ):
        return super().boost(tmcc_id, duration)

    @legacy_post(router, "/train/{tmcc_id:int}/brake_req", name="Train.BrakeReq")
    @mobile_post(router, "/train/{tmcc_id:int}/brake", name="Train.Brake")
    async def brake(
        self,
        tmcc_id: Annotated[int, Train.id_path()],
        duration: Annotated[float | None, Query(description="Duration (seconds)", gt=0.0)] = None,
    ):
        return super().brake(tmcc_id, duration)

    @legacy_post(router, "/train/{tmcc_id:int}/dialog_req", name="Train.DialogReq")
    @mobile_post(router, "/train/{tmcc_id:int}/dialog", name="Train.Dialog")
    async def dialog_req(
        self,
        tmcc_id: Annotated[int, Train.id_path()],
        option: DialogOption = Query(..., description="Dialog effect"),
    ):
        return super().dialog(tmcc_id, option)

    @legacy_post(router, "/train/{tmcc_id:int}/forward_req", name="Train.ForwardReq")
    @mobile_post(router, "/train/{tmcc_id:int}/forward", name="Train.Forward")
    async def forward(
        self,
        tmcc_id: Annotated[int, Train.id_path()],
    ):
        return super().forward(tmcc_id)

    @legacy_post(router, "/train/{tmcc_id:int}/front_coupler_req", name="Train.FrontCouplerReq")
    @mobile_post(router, "/train/{tmcc_id:int}/front_coupler", name="Train.FrontCoupler")
    async def front_coupler(
        self,
        tmcc_id: Annotated[int, Train.id_path()],
    ):
        return super().front_coupler(tmcc_id)

    @legacy_post(router, "/train/{tmcc_id:int}/horn_req", name="Train.HornReq")
    async def blow_horn_req(
        self,
        tmcc_id: Annotated[int, Train.id_path()],
        option: Annotated[HornOption, Query(description="Horn/whistle effect")],
        intensity: Annotated[
            int | None,
            Query(description="Quilling horn intensity (Legacy engines only)", ge=0, le=15),
        ] = 10,
        duration: Annotated[float | None, Query(description="Duration (seconds, Legacy engines only)", gt=0.0)] = None,
    ):
        return super().blow_horn(tmcc_id, option, intensity, duration)

    @mobile_post(router, "/train/{tmcc_id:int}/horn", name="Train.Horn")
    async def blow_horn_cmd(
        self,
        tmcc_id: Annotated[int, Train.id_path()],
        cmd: Annotated[
            HornCommand,
            Body(
                ...,
                discriminator="option",
                examples=[
                    {"option": "quilling", "intensity": 10, "duration": 1.0},
                    {"option": "sound", "duration": 1.0},
                    {"option": "sound"},
                    {"option": "grade"},
                ],
            ),
        ],
    ):
        option = cmd.option
        intensity = getattr(cmd, "intensity", None)
        duration = getattr(cmd, "duration", None)
        return super().blow_horn(tmcc_id, option, intensity, duration)

    @legacy_post(router, "/train/{tmcc_id:int}/momentum_req", name="Train.MomentumReq")
    @mobile_post(router, "/train/{tmcc_id:int}/momentum", name="Train.Momentum")
    async def momentum(
        self,
        tmcc_id: Annotated[int, Train.id_path()],
        level: Annotated[int, Query(..., description="Momentum level (0 - 7)", ge=0, le=7)],
    ):
        return super().momentum(tmcc_id, level)

    @legacy_post(router, "/train/{tmcc_id:int}/numeric_req", name="Train.NumericReq")
    async def numeric_req(
        self,
        tmcc_id: Annotated[int, Train.id_path()],
        number: Annotated[int | None, Query(description="Number (0 - 9)", ge=0, le=9)] = None,
        duration: Annotated[float | None, Query(description="Duration (seconds)", gt=0.0)] = None,
    ):
        return super().numeric(tmcc_id, number, duration)

    @mobile_post(router, "/train/{tmcc_id:int}/numeric", name="Train.Numeric")
    async def numeric_cmd(
        self,
        tmcc_id: Annotated[int, Train.id_path()],
        cmd: Annotated[NumericCommand, Body(...)],
    ):
        return super().numeric(tmcc_id, cmd.number, cmd.duration)

    @legacy_post(router, "/train/{tmcc_id:int}/rear_coupler_req", name="Train.RearCouplerReq")
    @mobile_post(router, "/train/{tmcc_id:int}/rear_coupler", name="Train.RearCoupler")
    async def rear_coupler(
        self,
        tmcc_id: Annotated[int, Train.id_path()],
    ):
        return super().rear_coupler(tmcc_id)

    @legacy_post(router, "/train/{tmcc_id:int}/reset_req", name="Train.ResetReq")
    async def reset_req(
        self,
        tmcc_id: Annotated[int, Train.id_path()],
        hold: Annotated[bool, Query(title="refuel", description="If true, perform refuel operation")] = False,
        duration: Annotated[int, Query(description="Refueling time (seconds)", ge=3)] = 3,
    ):
        duration = (duration if duration and duration >= 3 else 3) if hold else None
        return super().reset(tmcc_id, duration)

    @mobile_post(router, "/train/{tmcc_id:int}/reset", name="Train.Reset")
    async def reset_cmd(
        self,
        tmcc_id: Annotated[int, Train.id_path()],
        cmd: ResetCommand | None = Body(None),
    ):
        # Apply defaults if body omitted
        if cmd is None:
            cmd = ResetCommand.model_validate({})
        duration = (cmd.duration if cmd.duration and cmd.duration >= 3 else 3) if cmd.hold else None
        return super().reset(tmcc_id, duration=duration)

    @legacy_post(router, "/train/{tmcc_id:int}/reverse_req", name="Train.ReverseReq")
    @mobile_post(router, "/train/{tmcc_id:int}/reverse", name="Train.Reverse")
    async def reverse(
        self,
        tmcc_id: Annotated[int, Train.id_path()],
    ):
        return super().reverse(tmcc_id)

    @legacy_post(router, "/train/{tmcc_id:int}/shutdown_req", name="Train.ShutdownReq")
    @mobile_post(router, "/train/{tmcc_id:int}/shutdown", name="Train.Shutdown")
    async def shutdown(
        self,
        tmcc_id: Annotated[int, Train.id_path()],
        dialog: bool = False,
    ):
        return super().shutdown(tmcc_id, dialog=dialog)

    @legacy_post(router, "/train/{tmcc_id:int}/smoke_level_req", name="Train.SmokeLevelReq")
    @legacy_post(router, "/train/{tmcc_id:int}/smoke_req", name="Train.SmokeReq")
    @mobile_post(router, "/train/{tmcc_id:int}/smoke", name="Train.Smoke")
    async def smoke(
        self,
        tmcc_id: Annotated[int, Train.id_path()],
        level: SmokeOption,
    ):
        return super().smoke(tmcc_id, level=level)

    @legacy_post(router, "/train/{tmcc_id:int}/speed_req/{speed}", name="Train.SpeedReq")
    async def speed_req(
        self,
        tmcc_id: Annotated[int, Train.id_path()],
        speed: Annotated[
            int | str,
            Path(description="New speed (0 to 195, roll, restricted, slow, medium, limited, normal, highball)"),
        ],
        immediate: bool = None,
        dialog: bool = None,
    ):
        return super().speed(tmcc_id, speed, immediate=immediate, dialog=dialog)

    @mobile_post(router, "/train/{tmcc_id:int}/speed", name="Train.Speed")
    async def speed_cmd(
        self,
        tmcc_id: Annotated[int, Train.id_path()],
        cmd: SpeedCommand = Body(...),
    ):
        return await self._set_speed(tmcc_id, cmd.speed, cmd.immediate, cmd.dialog)

    @legacy_post(router, "/train/{tmcc_id:int}/startup_req", name="Train.StartupReq")
    @mobile_post(router, "/train/{tmcc_id:int}/startup", name="Train.Startup")
    async def startup(
        self,
        tmcc_id: Annotated[int, Train.id_path()],
        dialog: bool = False,
    ):
        return super().startup(tmcc_id, dialog=dialog)

    @legacy_post(router, "/train/{tmcc_id:int}/stop_req", name="Train.StopReq")
    @mobile_post(router, "/train/{tmcc_id:int}/stop", name="Train.Stop")
    async def stop(
        self,
        tmcc_id: Annotated[int, Train.id_path()],
    ):
        return super().stop(tmcc_id)

    @legacy_post(router, "/train/{tmcc_id:int}/toggle_direction_req", name="Train.ToggleDirectionReq")
    @mobile_post(router, "/train/{tmcc_id:int}/toggle_direction", name="Train.ToggleDirection")
    async def toggle_direction(
        self,
        tmcc_id: Annotated[int, Train.id_path()],
    ):
        return super().toggle_direction(tmcc_id)

    @legacy_post(router, "/train/{tmcc_id:int}/volume_down_req", name="Train.VolumeDownReq")
    @mobile_post(router, "/train/{tmcc_id:int}/volume_down", name="Train.VolumeDown")
    async def volume_down(
        self,
        tmcc_id: Annotated[int, Train.id_path()],
    ):
        return super().volume_down(tmcc_id)

    @legacy_post(router, "/train/{tmcc_id:int}/volume_up_req", name="Train.VolumeUpReq")
    @mobile_post(router, "/train/{tmcc_id:int}/volume_up", name="Train.VolumeUp")
    async def volume_up(
        self,
        tmcc_id: Annotated[int, Train.id_path()],
    ):
        return super().volume_up(tmcc_id)

    @legacy_post(router, "/train/{tmcc_id:int}/{aux_req}", name="Train.AuxReq")
    async def aux_req(
        self,
        tmcc_id: Annotated[int, Train.id_path()],
        aux_req: Annotated[AuxOption, Path(description="Aux 1, Aux2, or Aux 3")],
        number: Annotated[int | None, Query(description="Number (0 - 9)", ge=0, le=9)] = None,
        duration: Annotated[float | None, Query(description="Duration (seconds)", gt=0.0)] = None,
    ):
        return super().aux(tmcc_id, aux_req, number, duration)


app.include_router(router)
