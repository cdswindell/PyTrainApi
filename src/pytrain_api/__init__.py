#
#  PyTrainApi: a restful API for controlling Lionel Legacy engines, trains, switches, and accessories
#
#  Copyright (c) 2024-2025 Dave Swindell <pytraininfo.gmail.com>
#
#  SPDX-License-Identifier: LPGL
#
#

import importlib.metadata
import os
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
    # 0) Explicit override (CI, Docker, manual testing)
    v = os.getenv("PYTRAIN_API_VERSION")
    if v:
        v = v.strip()
    else:
        v = None

    # 1) Installed package version (PyPI / production)
    if not v:
        try:
            v = importlib.metadata.version(API_PACKAGE)
        except PackageNotFoundError:
            v = None

    # 2) Source checkout version via git (development)
    if not v:
        try:
            from setuptools_scm import get_version as get_git_version

            v = get_git_version(version_scheme="only-version")
        except (LookupError, RuntimeError, ImportError):
            # - not a git repo
            # - SCM metadata missing
            # - setuptools_scm not installed
            v = "0.0.0"

    # Normalize
    v = v.replace(".post0", "")
    v = v[1:] if v.startswith("v") else v
    return v
