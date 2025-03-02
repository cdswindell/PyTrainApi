#
#  PyTrainApi: a restful API for controlling Lionel Legacy engines, trains, switches, and accessories
#
#  Copyright (c) 2024-2025 Dave Swindell <pytraininfo.gmail.com>
#
#  SPDX-License-Identifier: LPGL
#
#

from __future__ import annotations

import os
import subprocess
import sys
import threading
from time import sleep
from typing import cast

import uvicorn
from pytrain import PyTrain, PyTrainExitStatus, is_linux, PROGRAM_NAME
from pytrain.utils.argument_parser import PyTrainArgumentParser

from . import is_package

API_NAME = "PyTrain Api"
API_PACKAGE = "pytrain-ogr-api"
DEFAULT_API_SERVER_PORT: int = 8000


class PyTrainApi:
    _instance: None = None
    _lock = threading.RLock()

    @classmethod
    def get(cls) -> PyTrainApi:
        if cls._instance is None:
            raise RuntimeError("PyTrainApi instance not created")
        return cls._instance

    def __init__(self, cmd_line: list[str] | None = None) -> None:
        from . import get_version
        from .endpoints import app

        if self._initialized:
            return
        else:
            self._initialized = True
        try:
            # parse command line args
            if cmd_line:
                args = self.command_line_parser().parse_args(cmd_line)
            else:
                args = self.command_line_parser().parse_args()

            pytrain_args = "-api"
            self._is_server = False
            if args.ser2 is True:
                pytrain_args += " -ser2"
                self._is_server = True
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
                self._is_server = True
                pytrain_args += f" -server {args.server}"

            if (args.base is not None or args.ser2 is True) and args.server_port:
                pytrain_args += f" -server_port {args.server_port}"

            if args.echo is True:
                pytrain_args += " -echo"
            if args.buttons_file:
                pytrain_args += f" -buttons_file {args.buttons_file}"

            # create a PyTrain process to handle commands
            print(f"{API_NAME} {get_version()}")
            self._pytrain_server = PyTrain(pytrain_args.split())
            port = args.api_port if args.api_port else DEFAULT_API_SERVER_PORT
            host = args.api_host if args.api_host else "0.0.0.0"
            uvicorn.run(app, host=host, port=port, reload=False)
            if self.pytrain.exit_status:
                if self.pytrain.exit_status == PyTrainExitStatus.UPGRADE:
                    self.upgrade()
                elif self.pytrain.exit_status == PyTrainExitStatus.UPDATE:
                    self.update()
                elif self.pytrain.exit_status in [PyTrainExitStatus.REBOOT, PyTrainExitStatus.SHUTDOWN]:
                    self.reboot(self.pytrain.exit_status)
                if self.pytrain.exit_status != PyTrainExitStatus.QUIT:
                    self.relaunch(self.pytrain.exit_status)
        except Exception as e:
            # Output anything else nicely formatted on stderr and exit code 1
            sys.exit(f"{__file__}: error: {e}\n")

    def __new__(cls, *args, **kwargs):
        """
        Provides singleton functionality. We only want one instance
        of this class in a process
        """
        with cls._lock:
            if PyTrainApi._instance is None:
                # noinspection PyTypeChecker
                PyTrainApi._instance = super(PyTrainApi, cls).__new__(cls)
                PyTrainApi._instance._initialized = False
            return PyTrainApi._instance

    @property
    def pytrain(self) -> PyTrain:
        return self._pytrain_server

    # noinspection PyUnusedLocal
    def relaunch(self, exit_status: PyTrainExitStatus) -> None:
        # if we're a client, we need to give the server time to respond, otherwise, we
        # will connect to it as it is shutting down
        print(f"{API_NAME} restarting...")
        if self._is_server is False:
            sleep(10)
        # are we a service or run from the commandline?
        if self.is_service is True:
            # restart service
            os.system("sudo systemctl restart pytrain_api.service")
        else:
            os.execv(sys.argv[0], sys.argv)

    def update(self, do_inform: bool = True) -> None:
        if do_inform:
            print(f"{'Server' if self.is_server else 'Client'} updating...")
        # always update pip
        os.system(f"cd {os.getcwd()}; pip install -U pip")
        if is_package():
            # upgrade from Pypi
            os.system(f"cd {os.getcwd()}; pip install -U {API_PACKAGE}")
        else:
            # upgrade from github
            os.system(f"cd {os.getcwd()}; git pull")
            os.system(f"cd {os.getcwd()}; pip install -r requirements.txt")
        self.relaunch(PyTrainExitStatus.UPDATE)

    def upgrade(self) -> None:
        if sys.platform == "linux":
            print(f"{'Server' if self.is_server else 'Client'} upgrading...")
            os.system("sudo apt update")
            sleep(1)
            os.system("sudo apt upgrade -y")
        self.update(do_inform=False)

    def reboot(self, option: PyTrainExitStatus) -> None:
        if option == PyTrainExitStatus.REBOOT:
            msg = "rebooting"
        else:
            msg = "shutting down"
        print(f"{'Server' if self.is_server else 'Client'} {msg}...")
        # are we running in API mode? if so, send signal
        if option == PyTrainExitStatus.REBOOT:
            opt = " -r"
        else:
            opt = ""
        os.system(f"sudo shutdown{opt} now")

    @property
    def is_server(self) -> bool:
        return self._is_server

    @property
    def is_service(self) -> bool:
        if is_linux() is False:
            return False
        stat = subprocess.call("systemctl is-active --quiet  pytrain_api.service".split())
        return stat == 0

    @classmethod
    def command_line_parser(cls) -> PyTrainArgumentParser:
        from . import get_version

        prog = "pytrain_api" if is_package() else "endpoints.py"
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
