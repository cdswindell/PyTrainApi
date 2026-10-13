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


REGISTERED_ROUTES = {
    (method, route.path): route
    for route in [*endpoints.app.routes, *endpoints.router.routes]
    if isinstance(route, APIRoute)
    for method in route.methods - {"HEAD", "OPTIONS"}
}


def _all_routes(prefix: str | None = None) -> list[tuple[str, str]]:
    routes: list[tuple[str, str]] = []
    for method, path in sorted(REGISTERED_ROUTES):
        if prefix and not path.startswith(prefix):
            continue
        routes.append((method, path))
    return routes


AUTH_ROUTES = _all_routes("/pytrain/v1/")
ALL_ROUTES = _all_routes()


class TestPyTrainApi:
    def test_route_inventory_is_non_empty(self):
        assert AUTH_ROUTES, "Expected secured API routes to be registered"
        assert ALL_ROUTES, "Expected at least one API route to be registered"
        assert len(AUTH_ROUTES) == sum(len(route.methods) for route in endpoints.router.routes)

    @pytest.mark.parametrize("method,path", AUTH_ROUTES)
    def test_all_secured_endpoints_require_api_key_dependency(self, method: str, path: str):
        route = REGISTERED_ROUTES[method, path]
        dependencies = [dep.call for dep in route.dependant.dependencies]
        assert endpoints.get_api_token in dependencies, f"Missing auth dependency for {method} {path}"

    @pytest.mark.parametrize("method,path", ALL_ROUTES)
    def test_all_routes_have_operation_metadata(self, method: str, path: str):
        route = REGISTERED_ROUTES[method, path]
        assert route.name
        assert callable(route.endpoint)
        assert route.summary is None or isinstance(route.summary, str)
        assert route.operation_id is None or isinstance(route.operation_id, str)

    def test_openapi_contains_all_registered_routes(self):
        schema = endpoints.app.openapi()
        openapi_paths = schema.get("paths", {})

        for method, path in ALL_ROUTES:
            route = REGISTERED_ROUTES[method, path]
            if not route.include_in_schema:
                continue
            normalized_path = re.sub(r":int(?=})", "", path)
            assert normalized_path in openapi_paths
            assert method.lower() in openapi_paths[normalized_path]
