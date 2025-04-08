#
#  PyTrainApi: a restful API for controlling Lionel Legacy engines, trains, switches, and accessories
#
#  Copyright (c) 2024-2025 Dave Swindell <pytraininfo.gmail.com>
#
#  SPDX-License-Identifier: LPGL
#
#

from __future__ import annotations

import logging
import os
import subprocess
import sys
import threading
from datetime import datetime
from time import sleep
from typing import cast

import uvicorn
from dotenv import find_dotenv, load_dotenv
from pytrain import PyTrain, PyTrainExitStatus, is_linux, PROGRAM_NAME
from pytrain.utils.argument_parser import PyTrainArgumentParser

from . import is_package

log = logging.getLogger(__name__)

API_NAME = "PyTrain Api"
API_PACKAGE = "pytrain-ogr-api"
DEFAULT_API_SERVER_PORT: int = 8000


class PyTrainApi:
    """
    This class provides an interface for managing and running a PyTrain system,
    including its API server. It acts as a singleton to ensure only one instance
    is created and can function as either a client or a server depending on the
    specified arguments.

    The class handles various operations such as parsing command-line arguments,
    configuring and starting the server, managing environment settings, updating
    and upgrading the system, and rebooting. The web server associated with the
    API is launched through this class, and it processes signals accordingly for
    different system states.

    """

    _instance: None = None
    _lock = threading.RLock()

    @classmethod
    def get(cls) -> PyTrainApi:
        if cls._instance is None:
            raise RuntimeError("PyTrainApi instance not created")
        return cls._instance

    def __init__(self, cmd_line: list[str] | None = None) -> None:
        from . import get_version
        from .endpoints import app, API_SERVER

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

            # if generate api key, do so and exit
            if args.token is True:
                from .endpoints import create_api_token

                token = create_api_token()
                print(f"Api Token: {token}")
                return
            elif args.secret is True:
                from .endpoints import create_secret

                token = create_secret()
                print(f"Api Secret: {token}")
                return
            elif args.env is True:
                self.write_env()
                return
            # if .env file is empty, create it and restart

            if not find_dotenv():
                log.warning("No .env file found, creating one and restarting...")
                self.write_env()
                self._is_server = True
                self.relaunch(PyTrainExitStatus.RESTART)

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
                self._is_server = True
                pytrain_args += " -base"
                if isinstance(args.base, list) and len(args.base):
                    pytrain_args += f" {args.base[0]}"
            elif args.client is True:
                pytrain_args += " -client"
            elif args.server:
                pytrain_args += f" -server {args.server}"

            if (args.base is not None or args.ser2 is True) and args.server_port:
                pytrain_args += f" -server_port {args.server_port}"
            if args.echo is True:
                pytrain_args += " -echo"
            if args.buttons_file:
                pytrain_args += f" -buttons_file {args.buttons_file}"

            # create a PyTrain process to handle commands
            self._pytrain_server = PyTrain(pytrain_args.split())
            port = args.api_port if args.api_port else DEFAULT_API_SERVER_PORT
            host = args.api_host if args.api_host else "0.0.0.0"
            log.info(f"{API_NAME} {get_version()}")
            if API_SERVER:
                log.info(f"Starting {API_NAME} server; external access via {API_SERVER}...")

            # launch the web server, this starts the API
            uvicorn.run(app, host=host, port=port, reload=False)

            # Web server exited, process signals from PyTrain server, if any
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
        log.info(f"{API_NAME} restarting...")
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
            log.info(f"{'Server' if self.is_server else 'Client'} updating...")
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
            log.info(f"{'Server' if self.is_server else 'Client'} upgrading...")
            os.system("sudo apt update")
            sleep(1)
            os.system("sudo apt upgrade -y")
            sleep(1)
            os.system("sudo apt autoremove -y")
        self.update(do_inform=False)

    def reboot(self, option: PyTrainExitStatus) -> None:
        if option == PyTrainExitStatus.REBOOT:
            msg = "rebooting"
        else:
            msg = "shutting down"
        log.info(f"{'Server' if self.is_server else 'Client'} {msg}...")
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

        secrets = parser.add_argument_group(title="Management")
        secret_opts = secrets.add_mutually_exclusive_group()
        secret_opts.add_argument(
            "-env",
            action="store_true",
            help="Write new .env file with SECRET_KEY, API_TOKEN, and ALGORITHM and exit",
        )
        secret_opts.add_argument(
            "-secret",
            action="store_true",
            help="Generate API Secret and exit",
        )
        secret_opts.add_argument(
            "-token",
            action="store_true",
            help="Generate API Token and exit",
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

        misc = parser.add_argument_group("Miscellaneous options")
        misc.add_argument(
            "-version",
            action="version",
            version=f"{cls.__qualname__} {get_version()}",
            help="Show version and exit",
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

    @staticmethod
    def write_env() -> None:
        from .endpoints import create_secret, create_api_token, DEFAULT_API_SERVER_VALUE

        api_server = DEFAULT_API_SERVER_VALUE
        algorithm = "HS256"
        alexa_exp = 60

        # look for existing file, if present, create a backup
        env_file = find_dotenv()
        if env_file:
            load_dotenv(env_file)
            api_server = os.environ.get("API_SERVER") if os.environ.get("API_SERVER") else api_server
            algorithm = os.environ.get("ALGORITHM") if os.environ.get("ALGORITHM") else algorithm
            alexa_exp = (
                int(os.environ.get("ALEXA_TOKEN_EXP_MIN")) if os.environ.get("ALEXA_TOKEN_EXP_MIN") else alexa_exp
            )

            env_file = os.path.relpath(env_file, os.getcwd())
            log.info(f"Renaming {env_file} to {env_file}.bak... ")
            os.rename(env_file, f"{env_file}.bak")
        else:
            env_file = ".env"  # write file in current directory
        with open(env_file, "w") as f:
            log.info(f"Creating {env_file}...")
            f.write("#\n")
            f.write(f"# {API_NAME} env \n")
            f.write("#\n")
            f.write(f"# Created: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}\n")
            f.write("#\n")
            f.write(f'ALGORITHM="{algorithm}"\n')
            log.info("Creating SECRET_KEY...")
            secret = create_secret()
            f.write(f'SECRET_KEY="{secret}"\n')
            f.write(f"ALEXA_TOKEN_EXP_MIN={alexa_exp}\n")
            log.info("Creating API_TOKEN...")
            f.write(f'API_TOKEN="{create_api_token(secret=secret)}"\n')
            f.write("API_TOKENS=\n")
            f.write(f'API_SERVER="{api_server}"\n')
