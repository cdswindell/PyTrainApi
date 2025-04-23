#
#  PyTrainApi: a restful API for controlling Lionel Legacy engines, trains, switches, and accessories
#
#  Copyright (c) 2024-2025 Dave Swindell <pytraininfo.gmail.com>
#
#  SPDX-License-Identifier: LPGL
#
#

import importlib.metadata
import sys
from importlib.metadata import PackageNotFoundError


API_PACKAGE = "pytrain-ogr-api"


def main(args: list[str] | None = None) -> int:
    from .endpoints import API_NAME
    from .pytrain_api import PyTrainApi

    if args is None:
        args = sys.argv[1:]
    try:
        PyTrainApi(args)

        return 0
    except Exception as e:
        # Output anything else nicely formatted on stderr and exit code 1
        return sys.exit(f"{API_NAME}: error: {e}\n")


def is_package() -> bool:
    try:
        # production version
        importlib.metadata.version(API_PACKAGE)
        return True
    except PackageNotFoundError:
        return False


def get_version() -> str:
    #
    # this should be easier, but it is what it is.
    # we handle the two major cases; we're running from
    # the PyTrain pypi package, or we're running from
    # source retrieved from git...
    #
    # we try the package path first...
    version = None
    try:
        # production version
        version = importlib.metadata.version(API_PACKAGE)
    except PackageNotFoundError:
        pass

    # finally, call the method to read it from git
    if version is None:
        from setuptools_scm import get_version as get_git_version

        version = get_git_version(version_scheme="only-version")

    version = version if version.startswith("v") else f"v{version}"
    version = version.replace(".post0", "")
    return version
