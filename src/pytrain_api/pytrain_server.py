#
#  PyTrain: a library for controlling Lionel Legacy engines, trains, switches, and accessories
#
#  Copyright (c) 2024-2025 Dave Swindell <pytraininfo.gmail.com>
#
#  SPDX-License-Identifier: LPGL
#
#
from __future__ import annotations

import signal
from threading import Thread, Event

from pytrain import PROGRAM_NAME
from pytrain.cli.pytrain import PyTrain
from pytrain.db.component_state_store import ComponentStateStore


class PyTrainServer(Thread):
    def __init__(self) -> None:
        super().__init__(daemon=True, name=f"{PROGRAM_NAME} Server")
        self._pytrain = None
        self._is_running = True
        self._ev = Event()
        self.start()
        self._ev.wait()

    def stop(self) -> None:
        self._is_running = False

    def run(self) -> None:
        self._pytrain = PyTrain("-headless -client".split())
        print(f"PyTrain Server started: {self._pytrain}")
        self._ev.set()
        while self._is_running:
            signal.pause()

    @property
    def pytrain(self) -> PyTrain:
        return self._pytrain

    @property
    def store(self) -> ComponentStateStore:
        # noinspection PyProtectedMember
        return self._pytrain._state_store()
