import asyncio
import os
import unittest
from unittest.mock import patch

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from starlette.requests import Request
from starlette.responses import Response

from app_config import (
    OriginGuardMiddleware,
    add_cors_middleware,
    add_origin_guard_middleware,
    get_backend_host,
    get_backend_port,
    get_cors_origins,
)


class AppSecurityTest(unittest.TestCase):
    @staticmethod
    def _request(method: str, origin: str | None = None) -> Request:
        headers = [(b"origin", origin.encode("utf-8"))] if origin is not None else []
        return Request(
            {
                "type": "http",
                "method": method,
                "scheme": "http",
                "path": "/api/workspace/path",
                "raw_path": b"/api/workspace/path",
                "query_string": b"",
                "headers": headers,
            }
        )

    def test_backend_defaults_to_loopback_and_standard_port(self):
        with patch.dict(os.environ, {}, clear=True):
            self.assertEqual(get_backend_host(), "127.0.0.1")
            self.assertEqual(get_backend_port(), 8000)

    def test_backend_bind_and_cors_origins_can_be_explicitly_overridden(self):
        with patch.dict(
            os.environ,
            {
                "MWG_BACKEND_HOST": "0.0.0.0",
                "MWG_BACKEND_PORT": "8123",
                "MWG_CORS_ORIGINS": "http://localhost:4000, https://wg.example",
            },
            clear=True,
        ):
            self.assertEqual(get_backend_host(), "0.0.0.0")
            self.assertEqual(get_backend_port(), 8123)
            self.assertEqual(
                get_cors_origins(),
                ["http://localhost:4000", "https://wg.example"],
            )

    def test_cors_is_limited_to_local_frontend_origins_by_default(self):
        with patch.dict(os.environ, {}, clear=True):
            app = FastAPI()
            add_cors_middleware(app)
            cors_middleware = next(
                middleware
                for middleware in app.user_middleware
                if middleware.cls is CORSMiddleware
            )

        self.assertEqual(
            cors_middleware.kwargs["allow_origins"],
            ["http://localhost:3000", "http://127.0.0.1:3000"],
        )
        self.assertFalse(cors_middleware.kwargs["allow_credentials"])

    def test_mutating_request_with_disallowed_origin_is_rejected_before_route(self):
        middleware = OriginGuardMiddleware(FastAPI())
        route_reached = False

        async def call_next(_request):
            nonlocal route_reached
            route_reached = True
            return Response(status_code=204)

        with patch("app_config.get_cors_origins", return_value=["http://localhost:3000"]):
            response = asyncio.run(
                middleware.dispatch(self._request("POST", "https://untrusted.example"), call_next)
            )

        self.assertEqual(response.status_code, 403)
        self.assertFalse(route_reached)

    def test_mutating_request_without_origin_reaches_route(self):
        middleware = OriginGuardMiddleware(FastAPI())
        route_reached = False

        async def call_next(_request):
            nonlocal route_reached
            route_reached = True
            return Response(status_code=204)

        response = asyncio.run(middleware.dispatch(self._request("POST"), call_next))

        self.assertEqual(response.status_code, 204)
        self.assertTrue(route_reached)

    def test_origin_guard_is_registered_as_application_middleware(self):
        app = FastAPI()
        add_origin_guard_middleware(app)

        self.assertTrue(
            any(middleware.cls is OriginGuardMiddleware for middleware in app.user_middleware)
        )


if __name__ == "__main__":
    unittest.main()
