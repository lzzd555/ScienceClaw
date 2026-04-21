import unittest
from types import SimpleNamespace
from unittest.mock import patch

from fastapi import FastAPI
from fastapi.testclient import TestClient


class _FakeStep:
    def __init__(self, payload):
        self._payload = payload

    def model_dump(self):
        return dict(self._payload)


class RpaGenerateRouteTests(unittest.TestCase):
    def test_generate_route_passes_session_ledger_into_generator(self):
        import backend.route.rpa as rpa_route

        ledger = object()
        seen = {}

        async def _fake_get_session(_session_id):
            return SimpleNamespace(
                steps=[_FakeStep({"action": "click", "target": "", "url": "https://example.com"})],
                context_ledger=ledger,
                sandbox_session_id="sandbox-1",
                user_id="user-1",
            )

        def _fake_generate_script(steps, params, is_local=False, test_mode=False, context_ledger=None):
            seen["steps"] = steps
            seen["params"] = params
            seen["is_local"] = is_local
            seen["test_mode"] = test_mode
            seen["context_ledger"] = context_ledger
            return "generated-script"

        app = FastAPI()
        app.include_router(rpa_route.router, prefix="/api/v1/rpa")
        app.dependency_overrides[rpa_route.get_current_user] = lambda: SimpleNamespace(
            id="user-1",
            username="tester",
            role="admin",
        )

        with (
            patch.object(rpa_route.rpa_manager, "get_session", _fake_get_session),
            patch.object(rpa_route.generator, "generate_script", _fake_generate_script),
            patch.object(rpa_route.settings, "storage_backend", "local"),
        ):
            client = TestClient(app)
            response = client.post("/api/v1/rpa/session/session-1/generate", json={"params": {"buyer": "Ada"}})

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json(), {"status": "success", "script": "generated-script"})
        self.assertIs(seen["context_ledger"], ledger)
        self.assertFalse(seen["test_mode"])
        self.assertTrue(seen["is_local"])
        self.assertEqual(seen["params"], {"buyer": "Ada"})

    def test_test_route_passes_session_ledger_into_generator(self):
        import backend.route.rpa as rpa_route

        ledger = object()
        seen = {}

        async def _fake_get_session(_session_id):
            return SimpleNamespace(
                steps=[_FakeStep({"action": "click", "target": "", "url": "https://example.com"})],
                context_ledger=ledger,
                sandbox_session_id="sandbox-1",
                user_id="user-1",
            )

        def _fake_generate_script(steps, params, is_local=False, test_mode=False, context_ledger=None):
            seen["steps"] = steps
            seen["params"] = params
            seen["is_local"] = is_local
            seen["test_mode"] = test_mode
            seen["context_ledger"] = context_ledger
            return "test-script"

        class _FakeConnector:
            run_in_pw_loop = None

            async def get_browser(self, session_id, user_id):
                return SimpleNamespace(session_id=session_id, user_id=user_id)

        async def _fake_inject_credentials(_user_id, params, _store):
            return dict(params)

        async def _fake_execute(*args, **kwargs):
            seen["execute_args"] = args
            seen["execute_kwargs"] = kwargs
            return {"success": True}

        app = FastAPI()
        app.include_router(rpa_route.router, prefix="/api/v1/rpa")
        app.dependency_overrides[rpa_route.get_current_user] = lambda: SimpleNamespace(
            id="user-1",
            username="tester",
            role="admin",
        )

        with (
            patch.object(rpa_route.rpa_manager, "get_session", _fake_get_session),
            patch.object(rpa_route.generator, "generate_script", _fake_generate_script),
            patch.object(rpa_route, "get_cdp_connector", lambda: _FakeConnector()),
            patch.object(rpa_route.executor, "execute", _fake_execute),
            patch.object(rpa_route, "inject_credentials", _fake_inject_credentials),
            patch.object(rpa_route.settings, "storage_backend", "local"),
            patch.object(rpa_route.settings, "workspace_dir", "/tmp"),
        ):
            client = TestClient(app)
            response = client.post("/api/v1/rpa/session/session-1/test", json={"params": {"buyer": "Ada"}})

        self.assertEqual(response.status_code, 200)
        self.assertEqual(seen["context_ledger"], ledger)
        self.assertTrue(seen["test_mode"])
        self.assertTrue(seen["is_local"])


if __name__ == "__main__":
    unittest.main()
