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
from typing import Annotated, Any, Callable, Iterable, TypeVar

import jwt
from dotenv import find_dotenv, load_dotenv
from fastapi import APIRouter, Body, Depends, FastAPI, HTTPException, Path, Query, Request, Security, status
from fastapi.openapi.docs import get_swagger_ui_html
from fastapi.responses import FileResponse, JSONResponse
from fastapi.security import APIKeyHeader, OAuth2PasswordBearer
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
from pytrain import (
    get_version as pytrain_get_version,
)
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
    ComponentInfo,
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
from .response_models import ErrorResponse, StatusResponse, SuccessResponse, VersionResponse, ok_response

log = logging.getLogger(__name__)

E = TypeVar("E", bound=CommandDefEnum)
F = TypeVar("F", bound=Callable[..., Any])
C = TypeVar("C", bound=ComponentInfo)

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
    local_key = api.service_info.properties.get("uuid".encode("utf-8"), None)
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
    "and LCS components.\n\n"
    "This API is used by the Alexa skill and mobile apps. "
    "It is also used by the web UI and other third-party integrations.\n\n"
    "For more information, visit the [GitHub repository](https://github.com/cdswindell/pytrainapi).",
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
      1) Raw API key (API_TOKEN, API_KEYS, etc.) OR
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

BASE_ERROR_RESPONSES: dict[int, Any] = {
    400: {"model": ErrorResponse, "description": "Bad Request"},
    401: {"model": ErrorResponse, "description": "Unauthorized"},
    498: {"model": ErrorResponse, "description": "Token Expired"},
}

OPTIONAL_ERROR_RESPONSES: dict[int, Any] = {
    403: {"model": ErrorResponse, "description": "Forbidden"},
    404: {"model": ErrorResponse, "description": "Not Found"},
}


def _split_camel(word: str) -> str:
    return _CAMEL_RE.sub(" ", word)


def _operation_id_from_name(name: str) -> str:
    parts = name.split(".")
    op_parts = []
    for p in parts:
        words = _split_camel(p).split()
        op_parts.append("_".join(w.lower() for w in words))
    return "_".join(op_parts)


def _summary_from_name(name: str) -> str:
    parts = [p for p in name.split(".") if p]
    return " ".join(_split_camel(p) for p in parts)


def infer_category(name: str) -> str:
    return name.split(".", 1)[0] if name else "Misc"


def _default_success_response(model: Any) -> dict[int, Any]:
    return {200: {"model": model, "description": "Success"}}


def _error_responses_for(codes: Iterable[int] | None) -> dict[int, Any]:
    """
    Start with BASE_ERROR_RESPONSES (400/401/498) and add any OPTIONAL_ERROR_RESPONSES requested.
    """
    merged: dict[int, Any] = dict(BASE_ERROR_RESPONSES)
    if codes:
        for code in codes:
            spec = OPTIONAL_ERROR_RESPONSES.get(code)
            if spec:
                merged[code] = spec
    return merged


def _merge_responses(
    user: dict[int, Any] | None = None,
    *,
    success_model: Any | None = None,
    base: dict[int, Any] | None = None,
    error_codes: Iterable[int] | None = None,
) -> dict[int, Any]:
    """
    One merge function for both GET and POST.

    Merge order (later wins):
      1) success response (200) if success_model
      2) base (route-type defaults like AUTH, or empty)
      3) error_codes-expanded models (BASE + requested OPTIONAL)
      4) user overrides
    """
    merged: dict[int, Any] = {}

    if success_model is not None:
        merged.update(_default_success_response(success_model))

    if base:
        merged.update(base)

    # BASE (400/401/498) + requested optional errors
    if error_codes is not None:
        merged.update(_error_responses_for(error_codes))

    if isinstance(user, dict):
        merged.update(user)

    return merged


def _route_helper(
    label: str,
    api: APIRouter,
    path: str,
    *,
    name: str,
    operation_id: str | None = None,
    summary: str | None = None,
    category: str | None = None,
    response_model: Any | None = None,
    default_success_model: Any | None = SuccessResponse,
    errors: tuple[int, ...] = (),
    **kwargs: Any,
) -> Callable[[F], F]:
    operation_id = operation_id or _operation_id_from_name(name)
    summary = summary or _summary_from_name(name)
    category = category or infer_category(name)

    # Response model selection
    if response_model is None and "response_model" not in kwargs:
        if default_success_model is not None:
            kwargs["response_model"] = default_success_model
    else:
        kwargs["response_model"] = response_model

    # POST behavior:
    # If caller didn't supply responses, still add standard errors (+ optional ones from `errors`)
    # and let any user-supplied responses override.
    success_model = response_model if response_model is not None else default_success_model

    kwargs["responses"] = _merge_responses(
        kwargs.get("responses"),
        success_model=success_model,
        # include BASE (400/401/498) + requested optional error models
        error_codes=errors,
    )

    return api.post(
        path,
        tags=[f"{label}.{category}"],
        name=name,
        operation_id=operation_id,
        summary=summary,
        **kwargs,
    )


def mobile_post(
    api: APIRouter,
    path: str,
    *,
    name: str,
    operation_id: str | None = None,
    summary: str | None = None,
    category: str | None = None,
    response_model: Any | None = None,
    default_success_model: Any | None = SuccessResponse,
    errors: tuple[int, ...] = (),
    responses: dict[int, Any] | None = None,
    **kwargs: Any,
) -> Callable[[F], F]:
    return _route_helper(
        "Mobile",
        api,
        path,
        name=name,
        operation_id=operation_id,
        summary=summary,
        category=category,
        response_model=response_model,
        default_success_model=default_success_model,
        errors=errors,
        responses=responses,
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
    response_model: Any | None = None,
    default_success_model: Any | None = SuccessResponse,
    errors: tuple[int, ...] = (),
    responses: dict[int, Any] | None = None,
    **kwargs: Any,
) -> Callable[[F], F]:
    return _route_helper(
        "Legacy",
        api,
        path,
        name=name,
        operation_id=operation_id,
        summary=summary,
        category=category,
        response_model=response_model,
        default_success_model=default_success_model,
        errors=errors,
        responses=responses,
        **kwargs,
    )


# GET-specific “base” response bundles
AUTH_RESPONSES = {
    401: {"model": ErrorResponse, "description": "Unauthorized"},
    498: {"model": ErrorResponse, "description": "Token Expired"},
}

COMMON_READ_ERRORS = {
    404: {"model": ErrorResponse, "description": "Not Found"},
}

COMMON_RUNTIME_ERRORS = {
    400: {"model": ErrorResponse, "description": "Bad Request"},
}


def api_get(
    api: APIRouter,
    path: str,
    *,
    name: str,
    operation_id: str | None = None,
    summary: str | None = None,
    category: str | None = None,
    response_model: Any | None = None,
    default_success_model: Any | None = SuccessResponse,
    include_404: bool = False,
    include_400: bool = True,
    responses: dict[int, Any] | None = None,
    labels: list[str] = ("Legacy", "Mobile"),
    **kwargs: Any,
) -> Callable[[F], F]:
    tags: list[str] = []
    operation_id = operation_id or _operation_id_from_name(name)
    summary = summary or _summary_from_name(name)
    category = category or infer_category(name)
    for label in labels:
        tags.append(f"{label}.{category}")

    # Build the GET base response set
    base: dict[int, Any] = dict(AUTH_RESPONSES)
    if include_404:
        base.update(COMMON_READ_ERRORS)
    if include_400:
        base.update(COMMON_RUNTIME_ERRORS)

    # Select success model for the 200 response
    if response_model is None and "response_model" not in kwargs:
        if default_success_model is not None:
            kwargs["response_model"] = default_success_model
    else:
        kwargs["response_model"] = response_model

    success_model = response_model if response_model is not None else default_success_model

    # GET behavior: success + base + user overrides.
    # (No BASE_ERROR_RESPONSES injection here unless you want it.)
    kwargs["responses"] = _merge_responses(
        responses,
        success_model=success_model,
        base=base,
    )

    return api.get(
        path,
        tags=tags,
        name=name,
        operation_id=operation_id,
        summary=summary,
        **kwargs,
    )


router = APIRouter(prefix="/pytrain/v1", dependencies=[Depends(get_api_token)])

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
    # allow APIs that issue a legitimate 404 to send a 404 response
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
        status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
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


@api_get(
    router,
    "/system/halt",
    summary="Emergency Stop",
    description="Stops all engines and trains, in their tracks; turns off all power districts.",
    name="System.Halt",
)
async def halt() -> StatusResponse:
    try:
        CommandReq(TMCC1HaltCommandEnum.HALT).send()
        return ok_response("HALT command sent")
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
async def debug(on: bool = True) -> StatusResponse:
    PyTrainApi.get().pytrain.queue_command(f"debug {'on' if on else 'off'}")
    return ok_response(f"Debugging {'enabled' if on else 'disabled'}")


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
async def echo(on: bool = True) -> StatusResponse:
    PyTrainApi.get().pytrain.queue_command(f"echo {'on' if on else 'off'}")
    return ok_response(f"Echo {'enabled' if on else 'disabled'}")


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
async def reboot() -> StatusResponse:
    try:
        CommandReq(TMCC1SyncCommandEnum.REBOOT).send()
        return ok_response("REBOOT command sent")
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
async def restart() -> StatusResponse:
    try:
        CommandReq(TMCC1SyncCommandEnum.RESTART).send()
        return ok_response("RESTART command sent")
    except Exception as e:
        raise HTTPException(status_code=400, detail=str(e))


@legacy_post(
    router,
    "/system/resync_req",
    summary="Resynchronize with Base 3",
    description="Reload all state information from Lionel Base 3.",
    name="System.ResyncReq",
)
@mobile_post(
    router,
    "/system/resync",
    summary="Resynchronize with Base 3",
    description="Reload all state information from Lionel Base 3.",
    name="System.Resync",
)
async def resync() -> StatusResponse:
    try:
        CommandReq(TMCC1SyncCommandEnum.RESYNC).send()
        return ok_response("RESYNC command sent")
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
async def shutdown() -> StatusResponse:
    try:
        CommandReq(TMCC1SyncCommandEnum.SHUTDOWN).send()
        return ok_response("SHUTDOWN command sent")
    except Exception as e:
        raise HTTPException(status_code=400, detail=str(e))


@legacy_post(router, "/system/stop_all_req", name="System.StopAllReq", summary="Stop All Engines and Trains")
@mobile_post(router, "/system/stop_all", name="System.StopAll", summary="Stop All Engines and Trains")
async def stop_all() -> StatusResponse:
    CommandReq(TMCC1EngineCommandEnum.STOP_IMMEDIATE, 99).send()
    CommandReq(TMCC2EngineCommandEnum.STOP_IMMEDIATE, 99, scope=CommandScope.TRAIN).send()
    return ok_response("Sent 'stop' command to all engines and trains...")


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
async def update() -> StatusResponse:
    try:
        CommandReq(TMCC1SyncCommandEnum.UPDATE).send()
        return ok_response("UPDATE command sent")
    except Exception as e:
        raise HTTPException(status_code=400, detail=str(e))


@legacy_post(
    router,
    "/system/version_req",
    summary=f"Get {API_NAME} Version",
    description=f"Get {API_NAME} software version.",
    name="System.VersionReq",
    default_success_model=None,
    response_model=VersionResponse,
)
@mobile_post(
    router,
    "/system/version",
    summary=f"Get {API_NAME} Version",
    description=f"Get {API_NAME} software version.",
    name="System.Version",
    default_success_model=None,
    response_model=VersionResponse,
)
async def get_version() -> VersionResponse:
    try:
        from . import get_version as api_get_version

        return VersionResponse(pytrain=pytrain_get_version(), pytrain_api=api_get_version())
    except Exception as e:
        raise HTTPException(status_code=400, detail=str(e))


# noinspection PathParameterInspection
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
) -> StatusResponse:
    try:
        if component in [Component.ENGINE, Component.TRAIN]:
            tmcc = " -tmcc" if is_tmcc is not None else ""
        else:
            tmcc = ""
        cmd = f"{component.value} {tmcc_id}{tmcc} {command}"
        parse_response = PyTrainApi.get().pytrain.parse_cli(cmd)
        if isinstance(parse_response, CommandReq):
            parse_response.send()
            return ok_response(f"'{cmd}' command sent")
        else:
            raise HTTPException(status_code=400, detail=f"Command is invalid: {parse_response}")
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


@api_get(
    router,
    "/accessories",
    name="Accessories.List",
    summary="List all accessories",
    response_model=list[AccessoryInfo],
    include_404=True,
)
async def get_accessories(contains: str = None) -> list[AccessoryInfo]:
    return [AccessoryInfo(**d) for d in get_components(CommandScope.ACC, contains=contains)]


class Accessory(PyTrainAccessory):
    def __init__(self):
        super().__init__(CommandScope.ACC)


_accessory = Accessory()


@api_get(
    router,
    "/accessory/{tmcc_id:int}",
    name="Accessory.Get",
    summary="Get accessory state",
    response_model=AccessoryInfo,
    include_404=True,
)
async def get_accessory(
    tmcc_id: Annotated[int, PyTrainComponent.id_path(label="Accessory")],
) -> AccessoryInfo:
    return AccessoryInfo(**_accessory.get(tmcc_id))


@legacy_post(router, "/accessory/{tmcc_id:int}/amc2_motor_req", name="Accessory.Amc2MotorReq")
async def acc_amc2_motor_req(
    tmcc_id: Annotated[int, PyTrainComponent.id_path(label="Accessory")],
    motor: Annotated[int, Query(description="Motor (1 - 2)", ge=1, le=2)],
    state: Annotated[OnOffOption | None, Query(description="On or Off")] = None,
    speed: Annotated[int | None, Query(description="Speed (0 - 100)", ge=0, le=100)] = None,
) -> StatusResponse:
    return _accessory.amc2_motor(tmcc_id, motor, state, speed)


@mobile_post(
    router,
    "/accessory/{tmcc_id:int}/amc2_motor",
    name="Accessory.Amc2Motor",
    errors=(404,),
)
async def acc_amc2_motor_cmd(
    tmcc_id: Annotated[int, PyTrainComponent.id_path(label="Accessory")],
    cmd: Amc2MotorCommand = Body(...),
) -> StatusResponse:
    motor = cmd.motor
    state = cmd.state if cmd.mode == "state" else None
    speed = cmd.speed if cmd.mode == "speed" else None
    strict = cmd.strict
    return _accessory.amc2_motor(tmcc_id, motor, state, speed, strict=strict)


@legacy_post(router, "/accessory/{tmcc_id:int}/amc2_lamp_req", name="Accessory.Amc2LampReq")
async def acc_amc2_lamp_req(
    tmcc_id: Annotated[int, PyTrainComponent.id_path(label="Accessory")],
    lamp: Annotated[int, Query(description="Lamp (1 - 4)", ge=1, le=4)],
    state: Annotated[OnOffOption | None, Query(description="On or Off")] = None,
    level: Annotated[int | None, Query(description="Brightness Level (0 - 100)", ge=0, le=100)] = None,
) -> StatusResponse:
    return _accessory.amc2_lamp(tmcc_id, lamp, state, level)


@mobile_post(
    router,
    "/accessory/{tmcc_id:int}/amc2_lamp",
    name="Accessory.Amc2Lamp",
    errors=(404,),
)
async def acc_amc2_lamp_cmd(
    tmcc_id: Annotated[int, PyTrainComponent.id_path(label="Accessory")],
    cmd: Amc2LampCommand = Body(...),
) -> StatusResponse:
    lamp = cmd.lamp
    state = cmd.state if cmd.mode == "state" else None
    level = cmd.level if cmd.mode == "level" else None
    strict = cmd.strict
    return _accessory.amc2_lamp(tmcc_id, lamp, state, level, strict=strict)


@legacy_post(router, "/accessory/{tmcc_id:int}/asc2_req", name="Accessory.Asc2Req")
async def acc_asc2_req(
    tmcc_id: Annotated[int, PyTrainComponent.id_path(label="Accessory")],
    state: Annotated[OnOffOption | None, Query(description="On or Off")],
    duration: Annotated[float | None, Query(description="Duration (seconds)", gt=0.0)] = None,
) -> StatusResponse:
    return _accessory.asc2(tmcc_id, state, duration)


@mobile_post(router, "/accessory/{tmcc_id:int}/asc2", name="Accessory.Asc2", errors=(404,))
async def acc_asc2_cmd(
    tmcc_id: Annotated[int, PyTrainComponent.id_path(label="Accessory")],
    cmd: Asc2Command = Body(...),
) -> StatusResponse:
    state = cmd.state
    duration = cmd.duration
    strict = cmd.strict
    return _accessory.asc2(tmcc_id, state, duration, strict=strict)


@mobile_post(router, "/accessory/{tmcc_id:int}/aux", name="Accessory.Aux")
async def acc_aux_cmd(
    tmcc_id: Annotated[int, PyTrainComponent.id_path(label="Accessory")],
    cmd: AuxCommand = Body(...),
) -> StatusResponse:
    return _accessory.aux(tmcc_id, cmd.aux_req, cmd.number, cmd.duration)


@legacy_post(router, "/accessory/{tmcc_id:int}/boost_req", name="Accessory.BoostReq")
@mobile_post(router, "/accessory/{tmcc_id:int}/boost", name="Accessory.Boost")
async def acc_boost(
    tmcc_id: Annotated[int, PyTrainComponent.id_path(label="Accessory")],
    duration: Annotated[float | None, Query(description="Duration (seconds)", gt=0.0)] = None,
) -> StatusResponse:
    return _accessory.boost(tmcc_id, duration)


@legacy_post(router, "/accessory/{tmcc_id:int}/brake_req", name="Accessory.BrakeReq")
@mobile_post(router, "/accessory/{tmcc_id:int}/brake", name="Accessory.Brake")
async def acc_brake(
    tmcc_id: Annotated[int, PyTrainComponent.id_path(label="Accessory")],
    duration: Annotated[float | None, Query(description="Duration (seconds)", gt=0.0)] = None,
) -> StatusResponse:
    return _accessory.brake(tmcc_id, duration)


@legacy_post(router, "/accessory/{tmcc_id:int}/bpc2_req", name="Accessory.Bpc2Req")
async def acc_bpc2_req(
    tmcc_id: Annotated[int, PyTrainComponent.id_path(label="Accessory")],
    state: Annotated[OnOffOption, Query(description="On or Off")],
) -> StatusResponse:
    return _accessory.bpc2(tmcc_id, state)


@mobile_post(router, "/accessory/{tmcc_id:int}/bpc", name="Accessory.Bpc2", errors=(404,))
async def acc_bpc2_cmd(
    tmcc_id: Annotated[int, PyTrainComponent.id_path(label="Accessory")],
    cmd: Bpc2Command = Body(...),
) -> StatusResponse:
    state = cmd.state
    strict = cmd.strict
    return _accessory.bpc2(tmcc_id, state, strict=strict)


@legacy_post(router, "/accessory/{tmcc_id:int}/front_coupler_req", name="Accessory.FrontCouplerReq")
@mobile_post(router, "/accessory/{tmcc_id:int}/front_coupler", name="Accessory.FrontCoupler")
async def acc_front_coupler(
    tmcc_id: Annotated[int, PyTrainComponent.id_path(label="Accessory")],
    duration: Annotated[float | None, Query(description="Duration (seconds)", gt=0.0)] = None,
) -> StatusResponse:
    return _accessory.open_coupler(tmcc_id, TMCC1AuxCommandEnum.FRONT_COUPLER, duration)


@legacy_post(router, "/accessory/{tmcc_id:int}/numeric_req", name="Accessory.NumericReq")
async def acc_numeric_req(
    tmcc_id: Annotated[int, PyTrainComponent.id_path(label="Accessory")],
    number: Annotated[int | None, Query(description="Number (0 - 9)", ge=0, le=9)] = None,
    duration: Annotated[float | None, Query(description="Duration (seconds)", gt=0.0)] = None,
) -> StatusResponse:
    return _accessory.do_numeric(TMCC1AuxCommandEnum.NUMERIC, tmcc_id, number, duration)


@mobile_post(router, "/accessory/{tmcc_id:int}/numeric", name="Accessory.Numeric")
async def acc_numeric_cmd(
    tmcc_id: Annotated[int, PyTrainComponent.id_path(label="Accessory")],
    cmd: Annotated[NumericCommand, Body(...)],
) -> StatusResponse:
    return _accessory.do_numeric(TMCC1AuxCommandEnum.NUMERIC, tmcc_id, cmd.number, cmd.duration)


@legacy_post(router, "/accessory/{tmcc_id:int}/rear_coupler_req", name="Accessory.RearCouplerReq")
@mobile_post(router, "/accessory/{tmcc_id:int}/rear_coupler", name="Accessory.RearCoupler")
async def acc_rear_coupler(
    tmcc_id: Annotated[int, PyTrainComponent.id_path(label="Accessory")],
    duration: Annotated[float | None, Query(description="Duration (seconds)", gt=0.0)] = None,
) -> StatusResponse:
    return _accessory.open_coupler(tmcc_id, TMCC1AuxCommandEnum.REAR_COUPLER, duration)


@legacy_post(router, "/accessory/{tmcc_id:int}/speed_req/{speed}", name="Accessory.SpeedReq")
async def acc_speed(
    tmcc_id: Annotated[int, PyTrainComponent.id_path(label="Accessory")],
    speed: Annotated[int, Path(description="Relative speed (-5 - 5)", ge=-5, le=5)],
    duration: Annotated[float | None, Query(description="Duration (seconds)", gt=0.0)] = None,
) -> StatusResponse:
    return _accessory.relative_speed(tmcc_id, speed, duration)


@mobile_post(router, "/accessory/{tmcc_id:int}/speed", name="Accessory.Speed")
async def acc_speed_cmd(
    tmcc_id: Annotated[int, PyTrainComponent.id_path(label="Accessory")],
    cmd: RelativeSpeedCommand = Body(...),
) -> StatusResponse:
    return _accessory.relative_speed(tmcc_id, cmd.speed, cmd.duration)


@legacy_post(router, "/accessory/{tmcc_id:int}/{aux_req}", name="Accessory.AuxReq")
async def acc_operate_accessory(
    tmcc_id: Annotated[int, PyTrainComponent.id_path(label="Accessory")],
    aux_req: Annotated[AuxOption, Path(description="Aux 1, Aux2, or Aux 3")],
    duration: Annotated[float | None, Query(description="Duration (seconds)", gt=0.0)] = None,
) -> StatusResponse:
    return _accessory.aux(tmcc_id, aux_req, None, duration)


@api_get(
    router,
    "/blocks",
    name="Blocks.List",
    summary="List all blocks",
    response_model=list[BlockInfo],
    include_404=True,
)
async def get_blocks(contains: str = None) -> list[BlockInfo]:
    return [BlockInfo(**d) for d in get_components(CommandScope.BLOCK, contains=contains)]


class Block(PyTrainComponent):
    # noinspection PyTypeHints
    @classmethod
    def id_path(cls, label: str = None, min_val: int = 1, max_val: int = 99):
        label = label if label else cls.__name__.replace("PyTrain", "")
        return Path(
            title="Block ID",
            description=f"{label}'s Block ID",
            ge=min_val,
            le=max_val,
        )

    def __init__(self):
        super().__init__(CommandScope.BLOCK)


_block = Block()


@api_get(
    router,
    "/block/{block_id}",
    name="Block.Get",
    summary="Get block state",
    response_model=BlockInfo,
    include_404=True,
)
async def get_block(
    block_id: Annotated[int, Block.id_path(label="Block")],
) -> BlockInfo:
    return BlockInfo(**_block.get(block_id))


@api_get(
    router,
    "/engines",
    name="Engines.List",
    summary="List all engines",
    response_model=list[EngineInfo],
    include_404=True,
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


_engine = Engine()


@api_get(
    router,
    "/engine/{tmcc_id:int}",
    name="Engine.Get",
    summary="Get engine state",
    response_model=EngineInfo,
    include_404=True,
)
async def get_engine(
    tmcc_id: Annotated[int, Engine.id_path()],
) -> EngineInfo:
    return EngineInfo(**_engine.get(tmcc_id))


@mobile_post(router, "/engine/{tmcc_id:int}/aux", name="Engine.Aux")
async def eng_aux_cmd(
    tmcc_id: Annotated[int, Engine.id_path()],
    cmd: AuxCommand = Body(...),
) -> StatusResponse:
    return _engine.aux(tmcc_id, cmd.aux_req, cmd.number, cmd.duration)


@legacy_post(router, "/engine/{tmcc_id:int}/bell_req", name="Engine.BellReq")
async def ring_bell_req(
    tmcc_id: Annotated[int, Engine.id_path()],
    option: Annotated[
        BellOption | None,
        Query(description="Bell effect (omit to toggle)"),
    ] = None,
    duration: Annotated[
        float | None,
        Query(description="Duration (seconds, only with 'once' option)", gt=0.0),
    ] = None,
) -> StatusResponse:
    return _engine.ring_bell(tmcc_id, option, duration)


@mobile_post(router, "/engine/{tmcc_id:int}/bell", name="Engine.Bell")
async def ring_bell_cmd(
    tmcc_id: Annotated[int, Engine.id_path()],
    cmd: Annotated[BellCommand, Body(..., discriminator="option")],
) -> StatusResponse:
    option = cmd.option
    duration = getattr(cmd, "duration", None)
    ding = getattr(cmd, "ding", None)
    return _engine.ring_bell(tmcc_id, option, duration, ding)


@legacy_post(router, "/engine/{tmcc_id:int}/boost_req", name="Engine.BoostReq")
@mobile_post(router, "/engine/{tmcc_id:int}/boost", name="Engine.Boost")
async def engine_boost(
    tmcc_id: Annotated[int, Engine.id_path()],
    duration: Annotated[float | None, Query(description="Duration (seconds)", gt=0.0)] = None,
) -> StatusResponse:
    return _engine.boost(tmcc_id, duration)


@legacy_post(router, "/engine/{tmcc_id:int}/brake_req", name="Engine.BrakeReq")
@mobile_post(router, "/engine/{tmcc_id:int}/brake", name="Engine.Brake")
async def engine_brake(
    tmcc_id: Annotated[int, Engine.id_path()],
    duration: Annotated[float | None, Query(description="Duration (seconds)", gt=0.0)] = None,
) -> StatusResponse:
    return _engine.brake(tmcc_id, duration)


@legacy_post(router, "/engine/{tmcc_id:int}/dialog_req", name="Engine.DialogReq")
@mobile_post(router, "/engine/{tmcc_id:int}/dialog", name="Engine.Dialog")
async def dialog_req(
    tmcc_id: Annotated[int, Engine.id_path()],
    dialog: DialogOption = Query(..., description="Dialog effect"),
) -> StatusResponse:
    return _engine.dialog(tmcc_id, dialog)


@legacy_post(router, "/engine/{tmcc_id:int}/forward_req", name="Engine.ForwardReq")
@mobile_post(router, "/engine/{tmcc_id:int}/forward", name="Engine.Forward")
async def forward_req(
    tmcc_id: Annotated[int, Engine.id_path()],
) -> StatusResponse:
    return _engine.forward(tmcc_id)


@legacy_post(router, "/engine/{tmcc_id:int}/front_coupler_req", name="Engine.FrontCouplerReq")
@mobile_post(router, "/engine/{tmcc_id:int}/front_coupler", name="Engine.FrontCoupler")
async def eng_front_coupler(
    tmcc_id: Annotated[int, Engine.id_path()],
) -> StatusResponse:
    return _engine.front_coupler(tmcc_id)


@legacy_post(router, "/engine/{tmcc_id:int}/horn_req", name="Engine.HornReq")
async def blow_horn_req(
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
) -> StatusResponse:
    return _engine.blow_horn(tmcc_id, option, intensity, duration)


@mobile_post(router, "/engine/{tmcc_id:int}/horn", name="Engine.Horn")
async def blow_horn_cmd(
    tmcc_id: Annotated[int, Engine.id_path()],
    cmd: Annotated[HornCommand, Body(..., discriminator="option")],
) -> StatusResponse:
    option = cmd.option
    intensity = getattr(cmd, "intensity", None)
    duration = getattr(cmd, "duration", None)
    return _engine.blow_horn(tmcc_id, option, intensity, duration)


@api_get(
    router,
    "/engine/{tmcc_id:int}/info",
    name="Engine.Info",
    summary="Get engine product information",
    response_model=ProductInfo,
    include_404=True,
)
async def get_info(
    tmcc_id: Annotated[int, Engine.id_path()],
) -> ProductInfo:
    return ProductInfo(**_engine.get_engine_info(tmcc_id))


@legacy_post(router, "/engine/{tmcc_id:int}/momentum_req", name="Engine.MomentumReq")
@mobile_post(router, "/engine/{tmcc_id:int}/momentum", name="Engine.Momentum")
async def momentum(
    tmcc_id: Annotated[int, Engine.id_path()],
    level: int = Query(..., ge=0, le=7, description="Momentum level (0 - 7)"),
) -> StatusResponse:
    return _engine.momentum(tmcc_id, level)


@legacy_post(router, "/engine/{tmcc_id:int}/numeric_req", name="Engine.NumericReq")
async def eng_numeric_req(
    tmcc_id: Annotated[int, Engine.id_path()],
    number: Annotated[int | None, Query(description="Number (0 - 9)", ge=0, le=9)],
    duration: Annotated[float | None, Query(description="Duration (seconds)", gt=0.0)] = None,
) -> StatusResponse:
    return _engine.numeric(tmcc_id, number, duration)


@mobile_post(router, "/engine/{tmcc_id:int}/numeric", name="Engine.Numeric")
async def eng_numeric_cmd(
    tmcc_id: Annotated[int, Engine.id_path()],
    cmd: Annotated[NumericCommand, Body(...)],
) -> StatusResponse:
    return _engine.numeric(tmcc_id, cmd.number, cmd.duration)


@legacy_post(router, "/engine/{tmcc_id:int}/rear_coupler_req", name="Engine.RearCouplerReq")
@mobile_post(router, "/engine/{tmcc_id:int}/rear_coupler", name="Engine.RearCoupler")
async def eng_rear_coupler(
    tmcc_id: Annotated[int, Engine.id_path()],
) -> StatusResponse:
    return _engine.rear_coupler(tmcc_id)


@legacy_post(router, "/engine/{tmcc_id:int}/reset_req", name="Engine.ResetReq")
async def reset_req(
    tmcc_id: Annotated[int, Engine.id_path()],
    hold: Annotated[bool, Query(title="refuel", description="If true, perform refuel operation")] = False,
    duration: Annotated[int | None, Query(description="Refueling time (seconds)", ge=3)] = 3,
) -> StatusResponse:
    duration = (duration if duration and duration >= 3 else 3) if hold else None
    return _engine.reset(tmcc_id, duration)


@mobile_post(router, "/engine/{tmcc_id:int}/reset", name="Engine.Reset")
async def reset_cmd(
    tmcc_id: Annotated[int, Engine.id_path()],
    cmd: ResetCommand | None = Body(None),
) -> StatusResponse:
    if cmd is None:
        cmd = ResetCommand.model_validate({})
    duration = (cmd.duration if cmd.duration and cmd.duration >= 3 else 3) if cmd.hold else None
    return _engine.reset(tmcc_id, duration=duration)


@legacy_post(router, "/engine/{tmcc_id:int}/reverse_req", name="Engine.ReverseReq")
@mobile_post(router, "/engine/{tmcc_id:int}/reverse", name="Engine.Reverse")
async def reverse(
    tmcc_id: Annotated[int, Engine.id_path()],
) -> StatusResponse:
    return _engine.reverse(tmcc_id)


@legacy_post(router, "/engine/{tmcc_id:int}/shutdown_req", name="Engine.ShutdownReq")
@mobile_post(router, "/engine/{tmcc_id:int}/shutdown", name="Engine.Shutdown")
async def eng_shutdown(
    tmcc_id: Annotated[int, Engine.id_path()],
    dialog: bool = Query(False, description="If true, include shutdown dialog"),
) -> StatusResponse:
    return _engine.shutdown(tmcc_id, dialog=dialog)


@legacy_post(
    router,
    "/engine/{tmcc_id:int}/smoke_level_req",
    name="Engine.SmokeLevelReq",
    deprecated=True,
    summary="(Deprecated) Use Engine.SmokeReq instead",
    description="Deprecated. Use Engine.SmokeReq instead.",
)
@legacy_post(router, "/engine/{tmcc_id:int}/smoke_req", name="Engine.SmokeReq")
@mobile_post(router, "/engine/{tmcc_id:int}/smoke", name="Engine.Smoke")
async def smoke(
    tmcc_id: Annotated[int, Engine.id_path()],
    level: SmokeOption = Query(..., description="Set smoke output level"),
) -> StatusResponse:
    return _engine.smoke(tmcc_id, level=level)


@legacy_post(router, "/engine/{tmcc_id:int}/speed_req/{speed}", name="Engine.SpeedReq")
async def eng_speed_req(
    tmcc_id: Annotated[int, Engine.id_path()],
    speed: Annotated[
        int | str,
        Path(description="New speed (0 to 195, roll, restricted, slow, medium, limited, normal, highball)"),
    ],
    immediate: bool = None,
    dialog: bool = None,
) -> StatusResponse:
    return _engine.speed(tmcc_id, speed, immediate=immediate, dialog=dialog)


@mobile_post(router, "/engine/{tmcc_id:int}/speed", name="Engine.Speed")
async def eng_speed_cmd(
    tmcc_id: Annotated[int, Engine.id_path()],
    cmd: SpeedCommand = Body(...),
) -> StatusResponse:
    return await _engine.set_speed(tmcc_id, cmd.speed, cmd.immediate, cmd.dialog)


@legacy_post(router, "/engine/{tmcc_id:int}/startup_req", name="Engine.StartupReq")
@mobile_post(router, "/engine/{tmcc_id:int}/startup", name="Engine.Startup")
async def eng_startup_cmd(
    tmcc_id: Annotated[int, Engine.id_path()],
    dialog: bool = Query(False, description="If true, include startup dialog"),
) -> StatusResponse:
    return _engine.startup(tmcc_id, dialog=dialog)


@legacy_post(router, "/engine/{tmcc_id:int}/stop_req", name="Engine.StopReq")
@mobile_post(router, "/engine/{tmcc_id:int}/stop", name="Engine.Stop")
async def eng_stop(
    tmcc_id: Annotated[int, Engine.id_path()],
) -> StatusResponse:
    return _engine.stop(tmcc_id)


@legacy_post(router, "/engine/{tmcc_id:int}/toggle_direction_req", name="Engine.ToggleDirectionReq")
@mobile_post(router, "/engine/{tmcc_id:int}/toggle_direction", name="Engine.ToggleDirection")
async def eng_toggle_direction(
    tmcc_id: Annotated[int, Engine.id_path()],
) -> StatusResponse:
    return _engine.toggle_direction(tmcc_id)


@legacy_post(router, "/engine/{tmcc_id:int}/volume_down_req", name="Engine.VolumeDownReq")
@mobile_post(router, "/engine/{tmcc_id:int}/volume_down", name="Engine.VolumeDown")
async def eng_volume_down(
    tmcc_id: Annotated[int, Engine.id_path()],
) -> StatusResponse:
    return _engine.volume_down(tmcc_id)


@legacy_post(router, "/engine/{tmcc_id:int}/volume_up_req", name="Engine.VolumeUpReq")
@mobile_post(router, "/engine/{tmcc_id:int}/volume_up", name="Engine.VolumeUp")
async def eng_volume_up(
    tmcc_id: Annotated[int, Engine.id_path()],
) -> StatusResponse:
    return _engine.volume_up(tmcc_id)


@legacy_post(router, "/engine/{tmcc_id:int}/{aux_req}", name="Engine.AuxReq")
async def eng_aux_req(
    tmcc_id: Annotated[int, Engine.id_path()],
    aux_req: Annotated[AuxOption, Path(description="Aux 1, Aux2, or Aux 3")],
    number: Annotated[int | None, Query(description="Number (0 - 9)", ge=0, le=9)] = None,
    duration: Annotated[float | None, Query(description="Duration (seconds)", gt=0.0)] = None,
) -> StatusResponse:
    return _engine.aux(tmcc_id, aux_req, number, duration)


@api_get(
    router,
    "/routes",
    name="Routes.List",
    summary="List all routes",
    response_model=list[RouteInfo],
    include_404=True,
)
async def get_routes(contains: str = None):
    return [RouteInfo(**d) for d in get_components(CommandScope.ROUTE, contains=contains)]


class Route(PyTrainComponent):
    def __init__(self):
        super().__init__(CommandScope.ROUTE)


_route = Route()


@api_get(
    router,
    "/route/{tmcc_id:int}",
    name="Route.Get",
    summary="Get route state",
    response_model=RouteInfo,
    include_404=True,
)
async def get_route(tmcc_id: Annotated[int, PyTrainComponent.id_path(label="Route")]):
    return RouteInfo(**_route.get(tmcc_id))


@legacy_post(router, "/route/{tmcc_id:int}/fire_req", name="Route.FireReq")
@mobile_post(router, "/route/{tmcc_id:int}/fire", name="Route.Fire")
async def fire(
    tmcc_id: Annotated[int, PyTrainComponent.id_path(label="Route")],
) -> StatusResponse:
    _route.do_request(TMCC1RouteCommandEnum.FIRE, tmcc_id)
    return ok_response(f"{_route.scope.title} {tmcc_id:int} fired")


@api_get(
    router,
    "/switches",
    name="Switches.List",
    summary="List all switches",
    response_model=list[SwitchInfo],
    include_404=True,
)
async def get_switches(contains: str = None):
    return [SwitchInfo(**d) for d in get_components(CommandScope.SWITCH, contains=contains)]


class Switch(PyTrainSwitch):
    def __init__(self):
        super().__init__(CommandScope.SWITCH)


_switch = Switch()


@api_get(
    router,
    "/switch/{tmcc_id:int}",
    name="Switch.Get",
    summary="Get switch state",
    response_model=SwitchInfo,
    include_404=True,
)
async def get_switch(
    tmcc_id: Annotated[int, PyTrainComponent.id_path(label="Switch")],
) -> SwitchInfo:
    return SwitchInfo(**_switch.get(tmcc_id))


@legacy_post(
    router,
    "/switch/{tmcc_id:int}/thru_req",
    name="Switch.ThruReq",
    deprecated=True,
    summary="(Deprecated) Use Switch.ThrowReq instead",
    description="Deprecated. Use Switch.ThrowReq instead.",
)
async def thru(
    tmcc_id: Annotated[int, PyTrainComponent.id_path(label="Switch")],
) -> StatusResponse:
    return _switch.throw(tmcc_id, SwitchPosition.THRU)


@legacy_post(
    router,
    "/switch/{tmcc_id:int}/out_req",
    name="Switch.OutReq",
    deprecated=True,
    summary="(Deprecated) Use Switch.ThrowReq instead",
    description="Deprecated. Use Switch.ThrowReq instead.",
)
async def out(
    tmcc_id: Annotated[int, PyTrainComponent.id_path(label="Switch")],
) -> StatusResponse:
    return _switch.throw(tmcc_id, SwitchPosition.OUT)


@legacy_post(
    router,
    "/switch/{tmcc_id:int}/throw_req",
    name="Switch.ThrowReq",
    summary="Throw switch thru or out",
)
@mobile_post(
    router,
    "/switch/{tmcc_id:int}/throw",
    name="Switch.Throw",
    summary="Throw switch thru or out",
)
async def throw_cmd(
    tmcc_id: Annotated[int, PyTrainComponent.id_path(label="Switch")],
    position: Annotated[
        SwitchPosition,
        Query(description="New switch position"),
    ],
) -> StatusResponse:
    return _switch.throw(tmcc_id, position)


@api_get(
    router,
    "/trains",
    name="Trains.List",
    summary="List all trains",
    response_model=list[TrainInfo],
    include_404=True,
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


_train = Train()


@api_get(
    router,
    "/train/{tmcc_id:int}",
    name="Train.Get",
    summary="Get train state",
    response_model=TrainInfo,
    include_404=True,
)
async def get_train(
    tmcc_id: Annotated[int, Train.id_path()],
) -> TrainInfo:
    return TrainInfo(**_train.get(tmcc_id))


@mobile_post(router, "/train/{tmcc_id:int}/aux", name="Train.Aux")
async def train_aux_cmd(
    tmcc_id: Annotated[int, Train.id_path()],
    cmd: AuxCommand = Body(...),
) -> StatusResponse:
    return _train.aux(tmcc_id, cmd.aux_req, cmd.number, cmd.duration)


@legacy_post(router, "/train/{tmcc_id:int}/bell_req", name="Train.BellReq")
async def train_ring_bell_req(
    tmcc_id: Annotated[int, Train.id_path()],
    option: Annotated[
        BellOption | None,
        Query(description="Bell effect"),
    ] = None,
    duration: Annotated[
        float | None,
        Query(description="Duration (seconds, only with 'once' option)", gt=0.0),
    ] = None,
) -> StatusResponse:
    return _train.ring_bell(tmcc_id, option, duration)


@mobile_post(router, "/train/{tmcc_id:int}/bell", name="Train.Bell")
async def train_ring_bell_cmd(
    tmcc_id: Annotated[int, Train.id_path()],
    cmd: Annotated[BellCommand, Body(..., discriminator="option")],
) -> StatusResponse:
    option = cmd.option
    duration = getattr(cmd, "duration", None)
    ding = getattr(cmd, "ding", None)
    return _train.ring_bell(tmcc_id, option, duration, ding)


@legacy_post(router, "/train/{tmcc_id:int}/boost_req", name="Train.BoostReq")
@mobile_post(router, "/train/{tmcc_id:int}/boost", name="Train.Boost")
async def train_boost(
    tmcc_id: Annotated[int, Train.id_path()],
    duration: Annotated[float | None, Query(description="Duration (seconds)", gt=0.0)] = None,
) -> StatusResponse:
    return _train.boost(tmcc_id, duration)


@legacy_post(router, "/train/{tmcc_id:int}/brake_req", name="Train.BrakeReq")
@mobile_post(router, "/train/{tmcc_id:int}/brake", name="Train.Brake")
async def train_brake(
    tmcc_id: Annotated[int, Train.id_path()],
    duration: Annotated[float | None, Query(description="Duration (seconds)", gt=0.0)] = None,
) -> StatusResponse:
    return _train.brake(tmcc_id, duration)


@legacy_post(router, "/train/{tmcc_id:int}/dialog_req", name="Train.DialogReq")
@mobile_post(router, "/train/{tmcc_id:int}/dialog", name="Train.Dialog")
async def train_dialog_req(
    tmcc_id: Annotated[int, Train.id_path()],
    option: DialogOption = Query(..., description="Dialog effect"),
) -> StatusResponse:
    return _train.dialog(tmcc_id, option)


@legacy_post(router, "/train/{tmcc_id:int}/forward_req", name="Train.ForwardReq")
@mobile_post(router, "/train/{tmcc_id:int}/forward", name="Train.Forward")
async def train_forward(
    tmcc_id: Annotated[int, Train.id_path()],
) -> StatusResponse:
    return _train.forward(tmcc_id)


@legacy_post(router, "/train/{tmcc_id:int}/front_coupler_req", name="Train.FrontCouplerReq")
@mobile_post(router, "/train/{tmcc_id:int}/front_coupler", name="Train.FrontCoupler")
async def train_front_coupler(
    tmcc_id: Annotated[int, Train.id_path()],
) -> StatusResponse:
    return _train.front_coupler(tmcc_id)


@legacy_post(router, "/train/{tmcc_id:int}/horn_req", name="Train.HornReq")
async def train_blow_horn_req(
    tmcc_id: Annotated[int, Train.id_path()],
    option: Annotated[HornOption, Query(description="Horn/whistle effect")],
    intensity: Annotated[
        int | None,
        Query(description="Quilling horn intensity (Legacy engines only)", ge=0, le=15),
    ] = 10,
    duration: Annotated[float | None, Query(description="Duration (seconds, Legacy engines only)", gt=0.0)] = None,
) -> StatusResponse:
    return _train.blow_horn(tmcc_id, option, intensity, duration)


@mobile_post(router, "/train/{tmcc_id:int}/horn", name="Train.Horn")
async def train_blow_horn_cmd(
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
) -> StatusResponse:
    option = cmd.option
    intensity = getattr(cmd, "intensity", None)
    duration = getattr(cmd, "duration", None)
    return _train.blow_horn(tmcc_id, option, intensity, duration)


@legacy_post(router, "/train/{tmcc_id:int}/momentum_req", name="Train.MomentumReq")
@mobile_post(router, "/train/{tmcc_id:int}/momentum", name="Train.Momentum")
async def train_momentum(
    tmcc_id: Annotated[int, Train.id_path()],
    level: Annotated[int, Query(..., description="Momentum level (0 - 7)", ge=0, le=7)],
) -> StatusResponse:
    return _train.momentum(tmcc_id, level)


@legacy_post(router, "/train/{tmcc_id:int}/numeric_req", name="Train.NumericReq")
async def train_numeric_req(
    tmcc_id: Annotated[int, Train.id_path()],
    number: Annotated[int | None, Query(description="Number (0 - 9)", ge=0, le=9)] = None,
    duration: Annotated[float | None, Query(description="Duration (seconds)", gt=0.0)] = None,
) -> StatusResponse:
    return _train.numeric(tmcc_id, number, duration)


@mobile_post(router, "/train/{tmcc_id:int}/numeric", name="Train.Numeric")
async def train_numeric_cmd(
    tmcc_id: Annotated[int, Train.id_path()],
    cmd: Annotated[NumericCommand, Body(...)],
) -> StatusResponse:
    return _train.numeric(tmcc_id, cmd.number, cmd.duration)


@legacy_post(router, "/train/{tmcc_id:int}/rear_coupler_req", name="Train.RearCouplerReq")
@mobile_post(router, "/train/{tmcc_id:int}/rear_coupler", name="Train.RearCoupler")
async def train_rear_coupler(
    tmcc_id: Annotated[int, Train.id_path()],
) -> StatusResponse:
    return _train.rear_coupler(tmcc_id)


@legacy_post(router, "/train/{tmcc_id:int}/reset_req", name="Train.ResetReq")
async def train_reset_req(
    tmcc_id: Annotated[int, Train.id_path()],
    hold: Annotated[bool, Query(title="refuel", description="If true, perform refuel operation")] = False,
    duration: Annotated[int, Query(description="Refueling time (seconds)", ge=3)] = 3,
) -> StatusResponse:
    duration = (duration if duration and duration >= 3 else 3) if hold else None
    return _train.reset(tmcc_id, duration)


@mobile_post(router, "/train/{tmcc_id:int}/reset", name="Train.Reset")
async def train_reset_cmd(
    tmcc_id: Annotated[int, Train.id_path()],
    cmd: ResetCommand | None = Body(None),
) -> StatusResponse:
    # Apply defaults if body omitted
    if cmd is None:
        cmd = ResetCommand.model_validate({})
    duration = (cmd.duration if cmd.duration and cmd.duration >= 3 else 3) if cmd.hold else None
    return _train.reset(tmcc_id, duration=duration)


@legacy_post(router, "/train/{tmcc_id:int}/reverse_req", name="Train.ReverseReq")
@mobile_post(router, "/train/{tmcc_id:int}/reverse", name="Train.Reverse")
async def train_reverse(
    tmcc_id: Annotated[int, Train.id_path()],
) -> StatusResponse:
    return _train.reverse(tmcc_id)


@legacy_post(router, "/train/{tmcc_id:int}/shutdown_req", name="Train.ShutdownReq")
@mobile_post(router, "/train/{tmcc_id:int}/shutdown", name="Train.Shutdown")
async def train_shutdown(
    tmcc_id: Annotated[int, Train.id_path()],
    dialog: bool = False,
) -> StatusResponse:
    return _train.shutdown(tmcc_id, dialog=dialog)


@legacy_post(
    router,
    "/train/{tmcc_id:int}/smoke_level_req",
    name="Train.SmokeLevelReq",
    deprecated=True,
    summary="(Deprecated) Use Train.SmokeReq instead",
    description="Deprecated. Use Train.SmokeReq instead.",
)
@legacy_post(router, "/train/{tmcc_id:int}/smoke_req", name="Train.SmokeReq")
@mobile_post(router, "/train/{tmcc_id:int}/smoke", name="Train.Smoke")
async def train_smoke(
    tmcc_id: Annotated[int, Train.id_path()],
    level: SmokeOption = Query(..., description="Set smoke output level"),
) -> StatusResponse:
    return _train.smoke(tmcc_id, level=level)


@legacy_post(router, "/train/{tmcc_id:int}/speed_req/{speed}", name="Train.SpeedReq")
async def train_speed_req(
    tmcc_id: Annotated[int, Train.id_path()],
    speed: Annotated[
        int | str,
        Path(description="New speed (0 to 195, roll, restricted, slow, medium, limited, normal, highball)"),
    ],
    immediate: bool = None,
    dialog: bool = None,
) -> StatusResponse:
    return _train.speed(tmcc_id, speed, immediate=immediate, dialog=dialog)


@mobile_post(router, "/train/{tmcc_id:int}/speed", name="Train.Speed")
async def train_speed_cmd(
    tmcc_id: Annotated[int, Train.id_path()],
    cmd: SpeedCommand = Body(...),
) -> StatusResponse:
    return train_speed_req(tmcc_id, cmd.speed, immediate=cmd.immediate, dialog=cmd.dialog)


@legacy_post(router, "/train/{tmcc_id:int}/startup_req", name="Train.StartupReq")
@mobile_post(router, "/train/{tmcc_id:int}/startup", name="Train.Startup")
async def train_startup(
    tmcc_id: Annotated[int, Train.id_path()],
    dialog: bool = False,
) -> StatusResponse:
    return _train.startup(tmcc_id, dialog=dialog)


@legacy_post(router, "/train/{tmcc_id:int}/stop_req", name="Train.StopReq")
@mobile_post(router, "/train/{tmcc_id:int}/stop", name="Train.Stop")
async def train_stop(
    tmcc_id: Annotated[int, Train.id_path()],
) -> StatusResponse:
    return _train.stop(tmcc_id)


@legacy_post(router, "/train/{tmcc_id:int}/toggle_direction_req", name="Train.ToggleDirectionReq")
@mobile_post(router, "/train/{tmcc_id:int}/toggle_direction", name="Train.ToggleDirection")
async def train_toggle_direction(
    tmcc_id: Annotated[int, Train.id_path()],
) -> StatusResponse:
    return _train.toggle_direction(tmcc_id)


@legacy_post(router, "/train/{tmcc_id:int}/volume_down_req", name="Train.VolumeDownReq")
@mobile_post(router, "/train/{tmcc_id:int}/volume_down", name="Train.VolumeDown")
async def train_volume_down(
    tmcc_id: Annotated[int, Train.id_path()],
) -> StatusResponse:
    return _train.volume_down(tmcc_id)


@legacy_post(router, "/train/{tmcc_id:int}/volume_up_req", name="Train.VolumeUpReq")
@mobile_post(router, "/train/{tmcc_id:int}/volume_up", name="Train.VolumeUp")
async def train_volume_up(
    tmcc_id: Annotated[int, Train.id_path()],
) -> StatusResponse:
    return _train.volume_up(tmcc_id)


@legacy_post(router, "/train/{tmcc_id:int}/{aux_req}", name="Train.AuxReq")
async def train_aux_req(
    tmcc_id: Annotated[int, Train.id_path()],
    aux_req: Annotated[AuxOption, Path(description="Aux 1, Aux2, or Aux 3")],
    number: Annotated[int | None, Query(description="Number (0 - 9)", ge=0, le=9)] = None,
    duration: Annotated[float | None, Query(description="Duration (seconds)", gt=0.0)] = None,
) -> StatusResponse:
    return _train.aux(tmcc_id, aux_req, number, duration)


app.include_router(router)
