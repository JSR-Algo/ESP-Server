import asyncio

import pytest

from core.utils import audioRateController
from core.utils.audioRateController import AudioRateController


class _Logger:
    def __init__(self):
        self.records = []

    def bind(self, **kwargs):
        self.records.append(("bind", kwargs))
        return self

    def debug(self, message):
        self.records.append(("debug", message))

    def info(self, message):
        self.records.append(("info", message))

    def warning(self, message):
        self.records.append(("warning", message))

    def error(self, message):
        self.records.append(("error", message))


def _controller(frame_duration=10):
    controller = AudioRateController(frame_duration=frame_duration)
    controller.logger = _Logger()
    return controller


def test_controller_constructs_without_a_running_loop_and_rebinds_between_loops():
    controller = _controller(frame_duration=1)
    assert controller.queue_empty_event is None
    assert controller.queue_has_data_event is None

    async def run_once(packet):
        sent = []

        async def send_audio(value):
            sent.append(value)

        task = controller.start_sending(send_audio)
        controller.add_audio(packet)
        await controller.wait_until_empty()
        await controller.stop_sending_and_wait()
        return sent, task.get_loop(), controller.queue_empty_event

    first_sent, first_loop, first_empty_event = asyncio.run(run_once(b"first"))
    second_sent, second_loop, second_empty_event = asyncio.run(run_once(b"second"))

    assert first_sent == [b"first"]
    assert second_sent == [b"second"]
    assert first_loop is not second_loop
    assert first_empty_event is not second_empty_event


def test_controller_rejects_rebind_while_sender_is_owned_by_another_loop():
    controller = _controller()
    owner_loop = asyncio.new_event_loop()

    async def send_audio(_packet):
        return None

    try:
        task = owner_loop.run_until_complete(
            owner_loop.create_task(_start_controller(controller, send_audio))
        )
        with pytest.raises(RuntimeError, match="active sender.*different event loop"):
            asyncio.run(controller.wait_until_empty())
    finally:
        task.cancel()
        owner_loop.run_until_complete(asyncio.gather(task, return_exceptions=True))
        owner_loop.close()


async def _start_controller(controller, send_audio):
    task = controller.start_sending(send_audio)
    await asyncio.sleep(0)
    return task


def test_add_audio_and_message_recover_queue_timing_and_reset(monkeypatch):
    controller = _controller(frame_duration=100)
    controller.play_position = 500
    controller._last_queue_empty_time = 0

    with monkeypatch.context() as scoped:
        times = iter([1.0, 1.0, 1.2, 1.4, 1.4])
        scoped.setattr(audioRateController.time, "monotonic", lambda: next(times))
        controller.add_audio(b"packet")
        assert list(controller.queue) == [("audio", b"packet")]
        assert controller.queue_empty_event is None
        assert controller.queue_has_data_event is None
        assert any(level == "debug" and "Queue recovered" in msg for level, msg in controller.logger.records)

        controller._drain_queue()
        controller.add_message(lambda: None)
        assert controller.queue[0][0] == "message"

    assert _controller()._get_elapsed_ms() == 0

    async def never_finishes():
        await asyncio.sleep(10)

    async def make_task():
        task = asyncio.create_task(never_finishes())
        controller.pending_send_task = task
        await asyncio.sleep(0)
        controller.reset()
        await asyncio.sleep(0)
        assert task.cancelled()

    asyncio.run(make_task())
    assert list(controller.queue) == []
    assert controller.play_position == 0
    assert controller.start_timestamp is None


@pytest.mark.asyncio
async def test_check_queue_sends_messages_audio_and_logs_callback_failures(monkeypatch):
    controller = _controller(frame_duration=10)
    controller._ensure_loop_primitives()
    sent = []
    message_calls = []
    monkeypatch.setattr(audioRateController.time, "monotonic", lambda: 10.0)

    async def message_callback():
        message_calls.append("message")

    async def send_audio(packet):
        sent.append(packet)

    controller.add_message(message_callback)
    controller.add_audio(b"a")
    await controller.check_queue(send_audio)
    assert message_calls == ["message"]
    assert sent == [b"a"]
    assert controller.play_position == 10
    assert controller.queue_empty_event.is_set()

    controller.add_message(lambda: (_ for _ in ()).throw(RuntimeError("message failed")))
    with pytest.raises(RuntimeError, match="message failed"):
        await controller.check_queue(send_audio)
    assert any(level == "error" and "Failed to send message" in msg for level, msg in controller.logger.records)

    controller = _controller(frame_duration=10)
    monkeypatch.setattr(audioRateController.time, "monotonic", lambda: 10.0)
    controller.add_audio(b"b")

    async def failing_send(packet):
        raise RuntimeError("audio failed")

    with pytest.raises(RuntimeError, match="audio failed"):
        await controller.check_queue(failing_send)
    assert any(level == "error" and "Failed to send audio" in msg for level, msg in controller.logger.records)


@pytest.mark.asyncio
async def test_check_queue_message_transport_close_exceptions_propagate(monkeypatch):
    class NormalClose(Exception):
        pass

    class TransportClose(Exception):
        pass

    monkeypatch.setattr(audioRateController, "_NORMAL_TRANSPORT_CLOSE_EXCEPTIONS", (NormalClose,))
    monkeypatch.setattr(audioRateController, "_TRANSPORT_CLOSE_EXCEPTIONS", (TransportClose,))

    controller = _controller()

    async def normal_message():
        raise NormalClose("normal")

    controller.add_message(normal_message)
    with pytest.raises(NormalClose):
        await controller.check_queue(lambda packet: None)

    controller = _controller()

    async def transport_message():
        raise TransportClose("closed")

    controller.add_message(transport_message)
    with pytest.raises(TransportClose):
        await controller.check_queue(lambda packet: None)


@pytest.mark.asyncio
async def test_check_queue_wait_can_be_cancelled(monkeypatch):
    controller = _controller(frame_duration=10)
    controller.queue.append(("audio", b"late"))
    controller.start_timestamp = 100.0
    controller.play_position = 1000
    monkeypatch.setattr(audioRateController.time, "monotonic", lambda: 100.0)

    async def cancel_sleep(seconds):
        raise asyncio.CancelledError

    monkeypatch.setattr(audioRateController.asyncio, "sleep", cancel_sleep)
    with pytest.raises(asyncio.CancelledError):
        await controller.check_queue(lambda packet: None)
    assert any(level == "debug" and "task canceled" in msg for level, msg in controller.logger.records)


@pytest.mark.asyncio
async def test_start_loop_handles_normal_transport_closed_transport_closed_and_generic(monkeypatch):
    class NormalClose(Exception):
        pass

    class TransportClose(Exception):
        pass

    monkeypatch.setattr(audioRateController, "_NORMAL_TRANSPORT_CLOSE_EXCEPTIONS", (NormalClose,))
    monkeypatch.setattr(audioRateController, "_TRANSPORT_CLOSE_EXCEPTIONS", (TransportClose,))

    async def run_loop_with(error):
        controller = _controller(frame_duration=1)

        async def send_audio(packet):
            raise error

        task = controller.start_sending(send_audio)
        controller.add_audio(b"packet")
        await asyncio.sleep(0.01)
        assert task.done()
        assert controller.pending_send_task is None
        assert controller.queue_empty_event.is_set()
        return controller.logger.records

    normal_records = await run_loop_with(NormalClose("normal"))
    assert any(level == "info" and "normal_close" in msg for level, msg in normal_records)

    transport_records = await run_loop_with(TransportClose("closed"))
    assert any(level == "warning" and "connection_closed" in msg for level, msg in transport_records)

    generic_records = await run_loop_with(RuntimeError("boom"))
    assert any(level == "error" and "Audio send loop exception" in msg for level, msg in generic_records)


@pytest.mark.asyncio
async def test_stop_sending_variants_clear_pending_task():
    controller = _controller()
    await controller.stop_sending_and_wait()

    async def never_finishes():
        await asyncio.sleep(10)

    controller.pending_send_task = asyncio.create_task(never_finishes())
    await asyncio.sleep(0)
    controller.stop_sending()
    assert any(level == "debug" and "task canceled" in msg for level, msg in controller.logger.records)
    await controller.stop_sending_and_wait()
    assert controller.pending_send_task is None

    done_task = asyncio.create_task(asyncio.sleep(0))
    await done_task
    controller.pending_send_task = done_task
    await controller.stop_sending_and_wait()
    assert controller.pending_send_task is None
