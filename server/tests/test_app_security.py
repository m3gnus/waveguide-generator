import os
import unittest
from unittest.mock import patch

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app_config import (
    add_cors_middleware,
    get_backend_host,
    get_backend_port,
    get_cors_origins,
)


class AppSecurityTest(unittest.TestCase):
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


if __name__ == "__main__":
    unittest.main()
