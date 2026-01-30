#
#  PyTrainApi: a restful API for controlling Lionel Legacy engines, trains, switches, and accessories
#
#  Copyright (c) 2024-2026 Dave Swindell <pytraininfo.gmail.com>
#
#  SPDX-License-Identifier: LPGL
#
#

from __future__ import annotations

from pydantic import BaseModel, Field


class SuccessResponse(BaseModel):
    status: str = Field(..., description="Status message")


def ok_response(status: str) -> SuccessResponse:
    return SuccessResponse(status=status)


class ErrorResponse(BaseModel):
    detail: str = Field(..., description="Error message")


def err_response(detail: str) -> ErrorResponse:
    return ErrorResponse(detail=detail)


StatusResponse = SuccessResponse | ErrorResponse


class VersionResponse(BaseModel):
    pytrain: str = Field(..., description="PyTrain version")
    pytrain_api: str = Field(..., description="PyTrain API version")
