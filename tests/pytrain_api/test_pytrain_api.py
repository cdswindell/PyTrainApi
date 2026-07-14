#
#  PyTrainApi: a restful API for controlling Lionel Legacy engines, trains, switches, and accessories
#
#  Copyright (c) 2024-2025 Dave Swindell <pytraininfo.gmail.com>
#
#  SPDX-License-Identifier: LPGL
#
#


from __future__ import annotations

import re

import pytest
from fastapi.routing import APIRoute

from src.pytrain_api import endpoints


def _all_routes(prefix: str | None = None) -> list[tuple[str, str]]:
    routes: list[tuple[str, str]] = []
    for route in endpoints.app.routes:
        if not isinstance(route, APIRoute):
            continue
        if prefix and not route.path.startswith(prefix):
            continue
        for method in sorted(route.methods - {"HEAD", "OPTIONS"}):
            routes.append((method, route.path))
    return routes


AUTH_ROUTES = _all_routes("/pytrain/v1")
ALL_ROUTES = _all_routes()


class TestPyTrainApi:
    def test_route_inventory_is_non_empty(self):
        assert AUTH_ROUTES, "Expected secured API routes to be registered"
        assert ALL_ROUTES, "Expected at least one API route to be registered"

    @pytest.mark.parametrize("method,path", AUTH_ROUTES)
    def test_all_secured_endpoints_require_api_key_dependency(self, method: str, path: str):
        route = next(
            r
            for r in endpoints.app.routes
            if isinstance(r, APIRoute) and r.path == path and method in (r.methods - {"HEAD", "OPTIONS"})
        )
        if path == "/pytrain/v1":
            pytest.skip("Router landing route is intentionally public")
        dependencies = [dep.call for dep in route.dependant.dependencies]
        assert endpoints.get_api_token in dependencies, f"Missing auth dependency for {method} {path}"

    @pytest.mark.parametrize("method,path", ALL_ROUTES)
    def test_all_routes_have_operation_metadata(self, method: str, path: str):
        route = next(
            r
            for r in endpoints.app.routes
            if isinstance(r, APIRoute) and r.path == path and method in (r.methods - {"HEAD", "OPTIONS"})
        )
        assert route.name
        assert callable(route.endpoint)
        assert route.summary is None or isinstance(route.summary, str)
        assert route.operation_id is None or isinstance(route.operation_id, str)

    def test_openapi_contains_all_registered_routes(self):
        schema = endpoints.app.openapi()
        openapi_paths = schema.get("paths", {})

        for method, path in ALL_ROUTES:
            route = next(
                r
                for r in endpoints.app.routes
                if isinstance(r, APIRoute) and r.path == path and method in (r.methods - {"HEAD", "OPTIONS"})
            )
            if not route.include_in_schema:
                continue
            normalized_path = re.sub(r":int(?=})", "", path)
            assert normalized_path in openapi_paths
