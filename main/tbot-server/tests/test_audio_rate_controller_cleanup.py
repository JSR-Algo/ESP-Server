import asyncio
import unittest

from core.utils.audioRateController import AudioRateController


class AudioRateControllerCleanupTest(unittest.IsolatedAsyncioTestCase):
    async def test_stop_sending_and_wait_retrieves_cancelled_send_loop(self):
        controller = AudioRateController()

        async def send_audio(_packet):
            return None

        task = controller.start_sending(send_audio)
        await asyncio.sleep(0)

        await controller.stop_sending_and_wait()

        self.assertTrue(task.done())
        self.assertIsNone(controller.pending_send_task)


if __name__ == "__main__":
    unittest.main()
