#
#  PyTrainApi: a restful api for controlling Lionel Legacy engines, trains, switches, and accessories
#
#  Copyright (c) 2025 Dave Swindell <pytraininfo.gmail.com>
#
#  SPDX-License-Identifier: LPGL
#
from __future__ import annotations

from datetime import timedelta, datetime, timezone
from enum import Enum
from typing import TypeVar, Annotated, Any

import jwt
from ask_sdk_core.dispatch_components import AbstractRequestHandler, AbstractExceptionHandler
from ask_sdk_core.handler_input import HandlerInput
from ask_sdk_core.skill_builder import SkillBuilder
from ask_sdk_core.utils import is_request_type, is_intent_name

# from ask_sdk_model import Request
from ask_sdk_model.ui import SimpleCard
from fastapi import HTTPException, APIRouter, Path, Query, Depends, status, FastAPI, Request, Body
from fastapi.security import OAuth2PasswordRequestForm, OAuth2PasswordBearer
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
)
from pytrain.cli.pytrain import PyTrain
from pytrain.db.component_state import ComponentState
from pytrain.protocol.command_def import CommandDefEnum
from starlette.responses import RedirectResponse

E = TypeVar("E", bound=CommandDefEnum)
API_NAME = "PyTrainApi"

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
app = FastAPI()
sb = SkillBuilder()


class LaunchRequestHandler(AbstractRequestHandler):
    def can_handle(self, handler_input):
        print("LaunchRequestHandler", handler_input)
        return is_request_type("LaunchRequest")(handler_input)

    def handle(self, handler_input):
        st = "Welcome to my FastAPI Alexa skill!"
        return handler_input.response_builder.speak(st).set_card(SimpleCard("Hello World", st)).response


class HelloWorldIntentHandler(AbstractRequestHandler):
    def can_handle(self, handler_input: HandlerInput):
        print("HelloWorldIntentHandler", handler_input)
        return is_intent_name("HelloWorldIntent")(handler_input)

    def handle(self, handler_input: HandlerInput):
        speech_text = "Hello World"
        handler_input.response_builder.speak(speech_text).set_card(
            SimpleCard("Hello World", speech_text)
        ).set_should_end_session(True)
        return handler_input.response_builder.response


class SessionEndedRequestHandler(AbstractRequestHandler):
    def can_handle(self, handler_input: HandlerInput):
        return is_request_type("SessionEndedRequest")(handler_input)

    def handle(self, handler_input: HandlerInput):
        # any cleanup logic goes here
        return handler_input.response_builder.response


class AllExceptionHandler(AbstractExceptionHandler):
    def can_handle(self, handler_input, exception):
        # type: (HandlerInput, Exception) -> bool
        return True

    def handle(self, handler_input, exception):
        # Log the exception in CloudWatch Logs
        print(exception)

        speech = "Sorry, I didn't get it. Can you please say it again!!"
        handler_input.response_builder.speak(speech).ask(speech)
        return handler_input.response_builder.response


sb.add_request_handler(LaunchRequestHandler())
sb.add_request_handler(HelloWorldIntentHandler())
sb.add_request_handler(SessionEndedRequestHandler())
sb.add_exception_handler(AllExceptionHandler())
skill = sb.create()


@app.post("/")
def alexa_endpoint(request):
    print("***", request)
    return skill.invoke(request, None)


#
# fastapi run src/pytrain_api/fastapi_ex.py
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


pytrain = PyTrain("-client -api -echo".split())


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


# router = APIRouter(prefix="/pytrain/v1", dependencies=[Depends(get_current_active_user)])
router = APIRouter(prefix="/pytrain/v1")


# @app.get("/", summary=f"Redirect to {API_NAME} Documentation")
# def index_redirect():
#     return RedirectResponse(url="/docs")


@app.get("/pytrain", summary=f"Redirect to {API_NAME} Documentation")
def pytrain_redirect():
    return RedirectResponse(url="/docs")


@router.get("/", summary=f"Redirect to {API_NAME} Documentation")
def redirect_to_doc():
    return RedirectResponse(url="/docs")


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
    pytrain.queue_command(f"echo {'on' if on else 'off'}")
    return {"status": f"Echo {'enabled' if on else 'disabled'}"}


@router.post("/system/stop_req")
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

    def do_request(
        self,
        cmd_def: E,
        tmcc_id: int,
        data: int = None,
        submit: bool = True,
        repeat: int = 1,
    ) -> CommandReq:
        cmd_req = CommandReq.build(cmd_def, tmcc_id, data, self.scope)
        if submit is True:
            cmd_req.send(repeat=repeat)
        return cmd_req

    @staticmethod
    def queue_command(cmd: str):
        pytrain.queue_command(cmd)


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
            self.do_request(TMCC1EngineCommandEnum.FORWARD_DIRECTION, tmcc_id)
        else:
            self.do_request(TMCC2EngineCommandEnum.FORWARD_DIRECTION, tmcc_id)
        return {"status": f"{self.scope.title} {tmcc_id} forward..."}

    def reset(self, tmcc_id: int, hold: bool = False):
        if self.is_tmcc(tmcc_id):
            self.do_request(TMCC1EngineCommandEnum.RESET, tmcc_id, repeat=30 if hold else 1)
        else:
            self.do_request(TMCC2EngineCommandEnum.RESET, tmcc_id, repeat=30 if hold else 1)
        return {"status": f"{self.scope.title} {tmcc_id} {'reset and refueled' if hold else 'reset'}..."}

    def reverse(self, tmcc_id: int):
        if self.is_tmcc(tmcc_id):
            self.do_request(TMCC1EngineCommandEnum.REVERSE_DIRECTION, tmcc_id)
        else:
            self.do_request(TMCC2EngineCommandEnum.REVERSE_DIRECTION, tmcc_id)
        return {"status": f"{self.scope.title} {tmcc_id} reverse..."}

    def ring_bell(self, tmcc_id: int, option: BellOption):
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
                self.do_request(TMCC2EngineCommandEnum.BELL_ONE_SHOT_DING, tmcc_id, 3)
        return {"status": f"{self.scope.title} {tmcc_id} ringing bell..."}

    def blow_horn(self, tmcc_id: int, option: HornOption, intensity: int = 10):
        if self.is_tmcc(tmcc_id):
            self.do_request(TMCC1EngineCommandEnum.BLOW_HORN_ONE, tmcc_id)
        else:
            if option is None or option == HornOption.SOUND:
                self.do_request(TMCC2EngineCommandEnum.BLOW_HORN_ONE, tmcc_id)
            elif option == HornOption.GRADE:
                self.do_request(SequenceCommandEnum.GRADE_CROSSING_SEQ, tmcc_id)
            elif option == HornOption.QUILLING:
                self.do_request(TMCC2EngineCommandEnum.QUILLING_HORN, tmcc_id, intensity)
        return {"status": f"{self.scope.title} {tmcc_id} blowing horn..."}


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
    def __init__(self):
        super().__init__(CommandScope.ENGINE)

    @router.get("/engine/{tmcc_id:int}")
    async def get_engine(self, tmcc_id: Annotated[int, Engine.id_path()]) -> EngineInfo:
        return EngineInfo(**super().get(tmcc_id))

    @router.post("/engine/{tmcc_id:int}/bell_req")
    async def ring_bell(
        self,
        tmcc_id: Annotated[int, Engine.id_path()],
        option: Annotated[BellOption, Query(description="Bell effect")] = BellOption.TOGGLE,
    ):
        return super().ring_bell(tmcc_id, option)

    @router.post("/enginex/{tmcc_id:int}/bell_req")
    async def ring_bellx(
        self,
        tmcc_id: Annotated[int, Body()],
        option: Annotated[BellOption, Body()] = BellOption.TOGGLE,
        request: Request = None,
    ):
        body = await request.json()
        print("*** Body: ", body)
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

    @router.post("/engine/{tmcc_id:int}/speed_req/{speed:int}")
    async def speed(
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
        return self.send(TMCC1SwitchCommandEnum.THRU, tmcc_id)

    @router.post("/switch/{tmcc_id}/out_req")
    async def out(self, tmcc_id: Annotated[int, Switch.id_path()]):
        return self.send(TMCC1SwitchCommandEnum.OUT, tmcc_id)


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
        option: Annotated[BellOption, Query(description="Bell effect")] = BellOption.TOGGLE,
    ):
        return super().ring_bell(tmcc_id, option)

    @router.post("/train/{tmcc_id:int}/forward_req")
    async def forward(self, tmcc_id: Annotated[int, Train.id_path()]):
        return super().forward(tmcc_id)

    @router.post("/train/{tmcc_id:int}/horn_req")
    async def blow_horn(
        self,
        tmcc_id: Annotated[int, Train.id_path()],
        option: Annotated[HornOption, Query(description="Horn effect")] = HornOption.SOUND,
        intensity: Annotated[int, Query(description="Quilling horn intensity (Legacy engines only)", ge=0, le=15)] = 10,
    ):
        return super().blow_horn(tmcc_id, option, intensity)

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

    @router.post("/train/{tmcc_id:int}/speed_req/{speed:int}")
    async def speed(
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

    @router.post("/train/{tmcc_id:int}/stop_req")
    async def stop(self, tmcc_id: Annotated[int, Train.id_path()]):
        return super().stop(tmcc_id)


app.include_router(router)
