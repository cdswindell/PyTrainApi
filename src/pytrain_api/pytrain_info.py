#
#  PyTrainApi: a restful API for controlling Lionel Legacy engines, trains, switches, and accessories
#
#  Copyright (c) 2024-2025 Dave Swindell <pytraininfo.gmail.com>
#
#  SPDX-License-Identifier: LPGL
#
#

from __future__ import annotations

from typing import Annotated, Any

from pydantic import BaseModel, Field, model_validator, field_validator

from .pytrain_component import Component


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


class RouteInfo(ComponentInfo):
    active: bool | None
    switches: list[RouteSwitch] | None


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
