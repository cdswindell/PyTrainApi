#
#  PyTrainApi: a restful API for controlling Lionel Legacy engines, trains, switches, and accessories
#
#  Copyright (c) 2024-2025 Dave Swindell <pytraininfo.gmail.com>
#
#  SPDX-License-Identifier: LPGL
#
#

from __future__ import annotations

from typing import Annotated, Any, Literal, Union

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from .pytrain_component import AuxOption, BellOption, Component, HornOption, OnOffOption


def command_config(*, examples: list[dict[str, Any]] | None = None) -> ConfigDict:
    """
    Helper to generate a ConfigDict with examples.
    :param examples:
    :return: ConfigDict
    """
    cfg: dict[str, Any] = {"extra": "forbid"}
    if examples:
        cfg["json_schema_extra"] = {"examples": examples}
    return ConfigDict(**cfg)


class ProductInfo(BaseModel):
    # noinspection PyMethodParameters
    @model_validator(mode="before")
    def validate_model(cls, data: Any) -> Any:
        if isinstance(data, dict) and len(data) == 0:
            raise ValueError("Product information not available")
        return data

    id: Annotated[int, Field(title="Product ID")]
    skuNumber: Annotated[int, Field(title="Sku Number", description="SKU Number assigned by Lionel")]
    blE_DecId: Annotated[int, Field(title="Bluetooth Decimal ID")]
    blE_HexId: Annotated[str, Field(title="Bluetooth Hexadecimal ID")]
    productFamily: Annotated[int, Field(title="Product Family")]
    engineClass: Annotated[int, Field(title="Engine Class")]
    engineType: Annotated[str, Field(title="Engine Type")]
    description: Annotated[str, Field(title="Description")]
    roadName: Annotated[str, Field(title="Road Name")]
    roadNumber: Annotated[str, Field(title="Road Number")]
    gauge: Annotated[str, Field(title="Gauge")]
    pmid: Annotated[int, Field(title="Product Management ID")]
    smoke: Annotated[bool, Field(title="Smoke")]
    hasOnBoardSound: Annotated[bool, Field(title="Has onboard sound")]
    appSoundFilesAvailable: Annotated[bool, Field(title="Supports sound files")]
    blE_StreamingSoundsSupported: Annotated[bool, Field(title="Supports Bluetooth streaming sounds")]
    appControlledLight: Annotated[bool, Field(title="Supports controllable lights")]
    frontCoupler: Annotated[bool, Field(title="Has Front Coupler")]
    rearCoupler: Annotated[bool, Field(title="Has Rear Coupler")]
    sound: Annotated[bool, Field(title="Supports Legacy RailSounds")]
    masterVolume: Annotated[bool, Field(title="Has Master Volume Control")]
    customSound: Annotated[bool, Field(title="Supports Sound Customization")]
    undefinedBit: Annotated[bool, Field(title="Undefined Bit")]
    imageUrl: Annotated[str, Field(title="Engine Image URL")]


class ComponentInfo(BaseModel):
    tmcc_id: Annotated[int, Field(title="TMCC ID", description="Assigned TMCC ID", ge=1, le=99)]
    road_name: Annotated[str | None, Field(description="Road Name assigned by user", max_length=32)]
    road_number: Annotated[str | None, Field(description="Road Number assigned by user", max_length=4)]
    scope: Component


class ComponentInfoIr(ComponentInfo):
    road_name: Annotated[str, Field(description="Road Name assigned by user or read from Sensor Track", max_length=32)]
    road_number: Annotated[str, Field(description="Road Name assigned by user or read from Sensor Track", max_length=4)]


class RouteSwitch(BaseModel):
    switch: int
    position: str


class SubRoute(BaseModel):
    route: int


class RouteInfo(ComponentInfo):
    active: bool | None
    switches: list[RouteSwitch] | None
    routes: list[SubRoute] | None


class SwitchInfo(ComponentInfo):
    scope: Component = Component.SWITCH
    state: str | None


class MotiveInfo(BaseModel):
    scope: str | None
    tmcc_id: int | None


class BlockInfo(BaseModel):
    scope: Component = Component.BLOCK
    block_id: int
    name: str | None
    direction: str | None
    sensor_track: int | None
    switch: int | None
    previous_block_id: int | None
    next_block_id: int | None
    is_occupied: bool | None
    occupied_by: MotiveInfo | None


class AccessoryInfo(ComponentInfo):
    # noinspection PyMethodParameters
    @model_validator(mode="before")
    def validate_model(cls, data: Any) -> Any:
        if isinstance(data, dict):
            for field in {"aux", "aux1", "aux2"}:
                if field not in data:
                    data[field] = None
            if "state" in data:
                data["aux"] = data["state"]
                del data["state"]
            if "type" not in data:
                data["type"] = "accessory"
            if "lcs" not in data:
                data["lcs"] = None
        return data

    # noinspection PyMethodParameters
    @field_validator("scope", mode="before")
    def validate_component(cls, v: str) -> str:
        return "accessory" if v in {"acc", "sensor_track", "sensor track", "power_district", "power district"} else v

    scope: Component = Component.ACCESSORY
    type: str | None
    lcs: str | None
    aux: str | None
    aux1: str | None
    aux2: str | None


STRICT_AMC2 = (
    "Enforce AMC2 validation. When true, the TMCC ID must resolve to a defined AMC2; "
    "otherwise the request fails. When false, the command is sent without validation."
)


class Amc2MotorStateCommand(BaseModel):
    model_config = ConfigDict(extra="forbid")
    mode: Literal["state"] = Field("state", description="Set motor on/off")
    motor: int = Field(..., ge=1, le=2, description="Motor (1 - 2)")
    state: OnOffOption = Field(..., description="On or Off")
    strict: bool = Field(True, description=STRICT_AMC2)


class Amc2MotorSpeedCommand(BaseModel):
    model_config = ConfigDict(extra="forbid")
    mode: Literal["speed"] = Field("speed", description="Set motor speed")
    motor: int = Field(..., ge=1, le=2, description="Motor (1 - 2)")
    speed: int = Field(..., ge=0, le=100, description="Speed (0 - 100)")
    strict: bool = Field(True, description=STRICT_AMC2)


Amc2MotorCommand = Annotated[
    Union[Amc2MotorStateCommand, Amc2MotorSpeedCommand],
    Field(discriminator="mode"),
]


class Amc2LampStateCommand(BaseModel):
    model_config = ConfigDict(extra="forbid")
    mode: Literal["state"] = Field("state", description="Set lamp on/off")
    lamp: int = Field(..., ge=1, le=4, description="Lamp (1 - 4)")
    state: OnOffOption = Field(..., description="On or Off")
    strict: bool = Field(True, description=STRICT_AMC2)


class Amc2LampLevelCommand(BaseModel):
    model_config = ConfigDict(extra="forbid")
    mode: Literal["level"] = Field("level", description="Set lamp brightness")
    lamp: int = Field(..., ge=1, le=4, description="Lamp (1 - 4)")
    level: int = Field(..., ge=0, le=100, description="Brightness level (0 - 100)")
    strict: bool = Field(True, description=STRICT_AMC2)


Amc2LampCommand = Annotated[
    Union[Amc2LampStateCommand, Amc2LampLevelCommand],
    Field(discriminator="mode"),
]


class Asc2Command(BaseModel):
    model_config = command_config(
        examples=[
            {"state": "on", "duration": None, "strict": True},
            {"state": "on", "duration": 1.0, "strict": True},
        ]
    )
    state: OnOffOption = Field(..., description="On or Off")
    duration: float | None = Field(default=None, gt=0.0, description="Optional duration (seconds)")
    strict: bool = Field(True, description=STRICT_AMC2.replace("AMC2", "ASC2"))


class Bpc2Command(BaseModel):
    model_config = ConfigDict(extra="forbid")
    state: OnOffOption = Field(..., description="On or Off")
    strict: bool = Field(True, description=STRICT_AMC2.replace("AMC2", "BPC2"))


class RelativeSpeedCommand(BaseModel):
    model_config = command_config(
        examples=[
            {"speed": 5, "duration": None},
            {"speed": -2, "duration": 1.0},
        ]
    )
    speed: Annotated[int, Field(ge=-5, le=5, description="New relative speed (-5 to 5)")]
    duration: float | None = Field(default=None, gt=0.0, description="Optional duration (seconds)")


class EngineInfo(ComponentInfoIr):
    tmcc_id: Annotated[int, Field(title="TMCC ID", description="Assigned TMCC ID", ge=1, le=9999)]
    scope: Component = Component.ENGINE
    bt_id: str | None
    control: str | None
    direction: str | None
    engine_class: str | None
    engine_type: str | None
    fuel_level: int | None
    labor: int | None
    max_speed: int | None
    momentum: int | None
    rpm: int | None
    smoke: str | None
    sound_type: str | None
    speed: int | None
    speed_limit: int | None
    target_speed: int | None
    train_brake: int | None
    water_level: int | None
    year: int | None


class TrainInfo(EngineInfo):
    scope: Component = Component.TRAIN
    flags: int | None
    components: dict[int, str] | None


class HornGrade(BaseModel):
    model_config = command_config(
        examples=[
            {"option": "grade"},
        ]
    )
    option: Literal[HornOption.GRADE]


class HornSound(BaseModel):
    model_config = command_config(
        examples=[
            {"option": "sound"},
            {"option": "sound", "duration": 1.0},
        ]
    )
    option: Literal[HornOption.SOUND]
    duration: float | None = Field(default=None, gt=0.0, description="Duration (seconds)")


class HornQuilling(BaseModel):
    model_config = command_config(
        examples=[
            {"option": "quilling", "intensity": 10, "duration": 1.0},
            {"option": "quilling", "duration": 2.0},
            {"option": "quilling"},
        ]
    )
    option: Literal[HornOption.QUILLING]
    intensity: int = Field(10, ge=0, le=15, description="Quilling horn intensity (Legacy engines only)")
    duration: float | None = Field(default=None, gt=0.0, description="Duration (seconds)")


HornCommand = Annotated[
    Union[HornSound, HornGrade, HornQuilling],
    Field(discriminator="option"),
]


class BellToggle(BaseModel):
    model_config = ConfigDict(extra="forbid")
    option: Literal[BellOption.TOGGLE]


class BellOn(BaseModel):
    model_config = ConfigDict(extra="forbid")
    option: Literal[BellOption.ON]


class BellOff(BaseModel):
    model_config = ConfigDict(extra="forbid")
    option: Literal[BellOption.OFF]


class BellOnce(BaseModel):
    model_config = command_config(
        examples=[
            {"option": "once", "duration": 1.0},
            {"option": "once"},
        ]
    )
    option: Literal[BellOption.ONCE]
    duration: float | None = Field(
        default=None,
        gt=0.0,
        description="Duration (seconds) for one-shot bell",
    )


class BellDing(BaseModel):
    model_config = ConfigDict(extra="forbid")
    option: Literal[BellOption.DING]
    ding: int | None = Field(None, ge=0, le=3, description="Ding number (0-3)", examples=[None])


BellCommand = Annotated[
    Union[BellToggle, BellOn, BellOff, BellOnce, BellDing],
    Field(discriminator="option"),
]


class NumericCommand(BaseModel):
    model_config = command_config(
        examples=[
            {"number": 5},
            {"number": 0, "duration": 3.0},
        ]
    )
    number: int = Field(..., description="Number (0 - 9)", ge=0, le=9)
    duration: float | None = Field(default=None, gt=0.0, description="Optional duration (seconds)")


class ResetCommand(BaseModel):
    hold: bool = Field(False, description="If true, perform refuel (held reset)")
    duration: float | None = Field(None, gt=0.0, description="Optional duration (seconds) for refuel", examples=[None])


SpeedNumber = Annotated[int, Field(ge=0, le=195, strict=True)]
SpeedKeyword = Literal[
    "stop",
    "roll",
    "restricted",
    "slow",
    "medium",
    "limited",
    "normal",
    "highball",
]


class SpeedCommand(BaseModel):
    speed: SpeedNumber | SpeedKeyword = Field(
        ...,
        description="New speed (0 to 195) or one of: stop, roll, restricted, slow, medium, limited, normal, highball",
        examples=[10, "slow"],
    )
    immediate: bool = Field(
        False,
        description="If true, apply speed change immediately (if supported)",
    )
    dialog: bool = Field(
        False,
        description="If true, include dialog sounds (if supported)",
    )


class AuxCommand(BaseModel):
    model_config = command_config(
        examples=[
            {"aux_req": "aux1", "duration": 3.0},
            {"aux_req": "aux3"},
        ]
    )
    aux_req: AuxOption = Field(..., description="Aux 1, Aux2, or Aux 3")
    number: int | None = Field(None, ge=0, le=9, description="Optional number (0 - 9)", examples=[None])
    duration: float | None = Field(default=None, gt=0.0, description="Optional duration (seconds)")
