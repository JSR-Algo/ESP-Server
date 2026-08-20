import asyncio
import unittest

from websockets.exceptions import ConnectionClosedOK

from core.utils.audioRateController import AudioRateController


class AudioRateControllerCleanupTest(unittest.IsolatedAsyncioTestCase):
    async def test_reset_preserves_empty_state_before_and_after_loop_initialization(self):
        controller = AudioRateController()

        controller.add_audio(b"before-init")
        controller.reset()
        self.assertIsNone(controller.queue_empty_event)
        self.assertIsNone(controller.queue_has_data_event)
        await controller.wait_until_empty()
        self.assertTrue(controller.queue_empty_event.is_set())
        self.assertFalse(controller.queue_has_data_event.is_set())

        controller.add_audio(b"after-init")
        self.assertFalse(controller.queue_empty_event.is_set())
        self.assertTrue(controller.queue_has_data_event.is_set())
        controller.reset()
        self.assertTrue(controller.queue_empty_event.is_set())
        self.assertFalse(controller.queue_has_data_event.is_set())

    async def test_stop_sending_and_wait_retrieves_cancelled_send_loop(self):
        controller = AudioRateController()

        async def send_audio(_packet):
            return None

        task = controller.start_sending(send_audio)
        await asyncio.sleep(0)

        await controller.stop_sending_and_wait()

        self.assertTrue(task.done())
        self.assertIsNone(controller.pending_send_task)

    async def test_wait_until_empty_includes_in_flight_send_callback(self):
        controller = AudioRateController(frame_duration=1)
        callback_started = asyncio.Event()
        release_callback = asyncio.Event()

        async def send_audio(_packet):
            callback_started.set()
            await release_callback.wait()

        controller.start_sending(send_audio)
        controller.add_audio(b"final-packet")
        await callback_started.wait()
        self.assertEqual(list(controller.queue), [])

        wait_task = asyncio.create_task(controller.wait_until_empty())
        await asyncio.sleep(0)
        self.assertFalse(wait_task.done())

        release_callback.set()
        await wait_task
        await controller.stop_sending_and_wait()

    async def test_normal_websocket_close_drains_queue_and_stops_send_loop(self):
        controller = AudioRateController(frame_duration=1)

        async def send_audio(_packet):
            raise ConnectionClosedOK(None, None)

        task = controller.start_sending(send_audio)
        controller.add_audio(b"packet-1")
        controller.add_audio(b"packet-2")

        await asyncio.sleep(0.05)

        self.assertTrue(task.done())
        self.assertIsNone(controller.pending_send_task)
        self.assertEqual(list(controller.queue), [])
        self.assertTrue(controller.queue_empty_event.is_set())
        self.assertFalse(controller.queue_has_data_event.is_set())


if __name__ == "__main__":
    unittest.main()
