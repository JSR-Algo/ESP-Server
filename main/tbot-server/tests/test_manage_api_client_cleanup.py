import asyncio
import importlib
import sys
import unittest


class _DummyAsyncClient:
    def __init__(self):
        self.closed = False

    async def aclose(self):
        self.closed = True


class ManageApiClientCleanupTest(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        module = sys.modules.get("config.manage_api_client")
        if module is not None and not hasattr(module, "ManageApiClient"):
            sys.modules.pop("config.manage_api_client", None)
        self.manage_api_client = importlib.import_module("config.manage_api_client")
        self.ManageApiClient = self.manage_api_client.ManageApiClient
        self.ManageApiClient._async_clients = {}
        self.ManageApiClient._instance = object()

    async def asyncTearDown(self):
        self.ManageApiClient._async_clients = {}
        self.ManageApiClient._instance = None

    async def test_safe_close_closes_legacy_clients_when_called_inside_running_loop(self):
        client = _DummyAsyncClient()
        self.ManageApiClient._async_clients = {"loop-1": client}

        self.ManageApiClient.safe_close()
        await asyncio.sleep(0)

        self.assertTrue(client.closed)
        self.assertEqual(self.ManageApiClient._async_clients, {})
        self.assertIsNone(self.ManageApiClient._instance)


if __name__ == "__main__":
    unittest.main()
