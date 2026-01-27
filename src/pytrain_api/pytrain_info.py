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

from .pytrain_component import BellOption, Component, HornOption


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
    tmcc_id: Annotated[int, Field(title="TMCC ID", description="Assigned TMCC ID", ge=1, le=9999)]
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


class HornGrade(BaseModel):
    model_config = ConfigDict(extra="forbid")
    option: Literal[HornOption.GRADE]


class HornSound(BaseModel):
    model_config = ConfigDict(extra="forbid")
    option: Literal[HornOption.SOUND]
    duration: float | None = Field(None, gt=0.0, description="Duration (seconds)")


class HornQuilling(BaseModel):
    model_config = ConfigDict(extra="forbid")
    option: Literal[HornOption.QUILLING]
    intensity: int = Field(10, ge=0, le=15, description="Quilling horn intensity (Legacy engines only)")
    duration: float | None = Field(None, gt=0.0, description="Duration (seconds)")


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
    model_config = ConfigDict(extra="forbid")
    option: Literal[BellOption.ONCE]
    duration: float | None = Field(
        None,
        gt=0.0,
        description="Duration (seconds) for one-shot bell",
    )


class BellDing(BaseModel):
    model_config = ConfigDict(extra="forbid")
    option: Literal[BellOption.DING]
    ding: int | None = Field(
        None,
        ge=0,
        le=3,
        description="Ding number (0-3)",
    )


BellCommand = Annotated[
    Union[BellToggle, BellOn, BellOff, BellOnce],
    Field(discriminator="option"),
]


class ResetCommand(BaseModel):
    hold: bool = Field(False, description="If true, perform refuel (held reset)")
    duration: float | None = Field(None, gt=0.0, description="Optional duration (seconds) for refuel")


class SpeedCommand(BaseModel):
    speed: int | str = Field(
        ...,
        description="New speed (0 to 195, roll, restricted, slow, medium, limited, normal, highball)",
    )
    immediate: bool | None = Field(
        None,
        description="If true, apply speed change immediately (if supported)",
    )
    dialog: bool | None = Field(
        None,
        description="If true, include dialog sounds (if supported)",
    )
