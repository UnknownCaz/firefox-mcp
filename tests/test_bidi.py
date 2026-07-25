"""Unit tests for BiDi request/response correlation — no browser needed.

Drives the client's reader loop with a fake WebSocket so we can assert that
responses are matched to the right pending command by ``id``, that errors
raise BiDiError, and that events land in the ring buffers.
"""

import asyncio
import json
import unittest

from firefox_mcp.bidi import BiDiClient, BiDiError


class FakeWS:
    def __init__(self):
        self.sent: list[str] = []
        self._queue: asyncio.Queue = asyncio.Queue()

    async def send(self, data):
        self.sent.append(data)

    async def close(self):
        pass

    def push(self, msg):
        self._queue.put_nowait(msg)

    def stop(self):
        self._queue.put_nowait(None)

    def __aiter__(self):
        return self

    async def __anext__(self):
        item = await self._queue.get()
        if item is None:
            raise StopAsyncIteration
        return item


class CorrelationTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.client = BiDiClient()
        self.ws = FakeWS()
        self.client._ws = self.ws
        self.client._connected = True
        self.reader = asyncio.create_task(self.client._reader())

    async def asyncTearDown(self):
        self.ws.stop()
        await self.reader

    async def _last_sent_id(self) -> int:
        for _ in range(1000):
            if self.ws.sent:
                return json.loads(self.ws.sent[-1])["id"]
            await asyncio.sleep(0.001)
        raise AssertionError("nothing was sent")

    async def test_success_correlates_by_id(self):
        async def respond():
            mid = await self._last_sent_id()
            self.ws.push(json.dumps({"type": "success", "id": mid, "result": {"ok": 1}}))

        asyncio.create_task(respond())
        result = await self.client._send("session.status", {})
        self.assertEqual(result, {"ok": 1})

    async def test_error_raises(self):
        async def respond():
            mid = await self._last_sent_id()
            self.ws.push(json.dumps({"type": "error", "id": mid,
                                     "error": "unknown command", "message": "nope"}))

        asyncio.create_task(respond())
        with self.assertRaises(BiDiError):
            await self.client._send("bogus.method", {})

    async def test_out_of_order_correlation(self):
        # Two in-flight commands resolved in reverse order still match correctly.
        async def send_and_get(method):
            return await self.client._send(method, {})

        t1 = asyncio.create_task(send_and_get("a.one"))
        t2 = asyncio.create_task(send_and_get("a.two"))
        # Wait for both to be on the wire.
        for _ in range(1000):
            if len(self.ws.sent) >= 2:
                break
            await asyncio.sleep(0.001)
        ids = [json.loads(s)["id"] for s in self.ws.sent[:2]]
        # respond to the second first
        self.ws.push(json.dumps({"type": "success", "id": ids[1], "result": {"which": "two"}}))
        self.ws.push(json.dumps({"type": "success", "id": ids[0], "result": {"which": "one"}}))
        r1, r2 = await asyncio.gather(t1, t2)
        self.assertEqual(r1, {"which": "one"})
        self.assertEqual(r2, {"which": "two"})

    async def test_event_goes_to_ring_buffer(self):
        self.ws.push(json.dumps({"type": "event", "method": "log.entryAdded",
                                 "params": {"level": "error", "text": "boom"}}))
        for _ in range(1000):
            if self.client.console_logs:
                break
            await asyncio.sleep(0.001)
        self.assertEqual(self.client.console_logs[-1]["text"], "boom")


class LoopbackTests(unittest.TestCase):
    def test_rejects_non_loopback(self):
        with self.assertRaises(ValueError):
            BiDiClient("ws://evil.example.com:9222/session")

    def test_accepts_loopback(self):
        BiDiClient("ws://127.0.0.1:9222/session")  # should not raise


if __name__ == "__main__":
    unittest.main()
