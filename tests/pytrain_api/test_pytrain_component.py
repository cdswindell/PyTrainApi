#
#  PyTrainApi: a restful API for controlling Lionel Legacy engines, trains, switches, and accessories
#
#  Copyright (c) 2024-2025 Dave Swindell <pytraininfo.gmail.com>
#
#  SPDX-License-Identifier: LPGL
#
#

from src.pytrain_api.pytrain_component import PyTrainEngine
from pytrain import CommandScope


class TestPyTrainEngine:
    def setup_method(self):
        self.engine = PyTrainEngine(scope=CommandScope.ENGINE)

    def test_prefix_with_engine_scope(self):
        assert self.engine.prefix == "engine"

    def test_prefix_with_train_scope(self):
        self.engine = PyTrainEngine(scope=CommandScope.TRAIN)
        assert self.engine.prefix == "train"
