import time
import asyncio
from collections import deque
from config.logger import setup_logging

try:
    from websockets.exceptions import ConnectionClosed, ConnectionClosedOK
except Exception:  # pragma: no cover - websockets is a runtime dependency
    ConnectionClosed = None
    ConnectionClosedOK = None

_NORMAL_TRANSPORT_CLOSE_EXCEPTIONS = (
    (ConnectionClosedOK,) if ConnectionClosedOK is not None else ()
)
_TRANSPORT_CLOSE_EXCEPTIONS = (
    (ConnectionClosed,) if ConnectionClosed is not None else ()
)

TAG = __name__
logger = setup_logging()


class AudioRateController:
    """
    Audio rate controller - precisely controls audio sending by 60ms frame duration
    Fixes time accumulation error under high concurrency
    """

    def __init__(self, frame_duration=60):
        """
        Args:
            frame_duration: single audio frame duration (ms), default 60ms
        """
        self.frame_duration = frame_duration
        self.queue = deque()
        self.play_position = 0  # Virtual playback position (ms)
        self.start_timestamp = None  # StartTimestamp(read-only, noModify)
        self.pending_send_task = None
        self.logger = logger
        self.queue_empty_event = None
        self.queue_has_data_event = None
        self._primitives_loop = None
        self._active_empty_waiters = 0
        self._last_queue_empty_time = 0  # Last queue clear time (seconds)

    def _ensure_loop_primitives(self):
        """Bind asyncio primitives to the active loop when they are needed."""
        loop = asyncio.get_running_loop()
        active_task = self.pending_send_task
        if active_task is not None and not active_task.done():
            owner_loop = active_task.get_loop()
            if owner_loop is not loop:
                raise RuntimeError(
                    "AudioRateController active sender belongs to a different event loop"
                )

        if self._active_empty_waiters and self._primitives_loop is not loop:
            raise RuntimeError(
                "AudioRateController active waiter belongs to a different event loop"
            )

        if self._primitives_loop is loop:
            return

        self.queue_empty_event = asyncio.Event()
        self.queue_has_data_event = asyncio.Event()
        if self.queue:
            self.queue_has_data_event.set()
        else:
            self.queue_empty_event.set()
        self._primitives_loop = loop

    def _sync_queue_events(self):
        """Mirror queue state without creating loop-bound primitives."""
        if self.queue_empty_event is None or self.queue_has_data_event is None:
            return
        if self.queue:
            self.queue_empty_event.clear()
            self.queue_has_data_event.set()
        else:
            self.queue_empty_event.set()
            self.queue_has_data_event.clear()

    async def wait_until_empty(self):
        self._ensure_loop_primitives()
        if not self.queue and self.queue_empty_event.is_set():
            return
        self._active_empty_waiters += 1
        try:
            await self.queue_empty_event.wait()
        finally:
            self._active_empty_waiters -= 1

    def reset(self):
        """Reset controller state"""
        if self.pending_send_task and not self.pending_send_task.done():
            self.pending_send_task.cancel()
            # After task cancellation, task will nextEventClean up during loop, no blocking wait

        self.queue.clear()
        self.play_position = 0
        self.start_timestamp = None  # Set by first audio packet
        self._last_queue_empty_time = 0  # Reset Time
        # RelatedEventProcess
        self._sync_queue_events()

    def _drain_queue(self):
        self.queue.clear()
        self._sync_queue_events()
        self._last_queue_empty_time = time.monotonic()

    def add_audio(self, opus_packet):
        """Add audio packet to queue"""
        # If queue was empty before, need adjustTimestampKeep playback time continuous
        # This way, during tool call wait, newly added audio will not play early
        # If interval short (<1frames), means normal streaming transmission, no reset needed
        if len(self.queue) == 0 and self.play_position > 0:
            elapsed_since_empty = (time.monotonic() - self._last_queue_empty_time) * 1000
            # Only if interval exceeds1frame duration, then considered real"Pause/resume"
            if elapsed_since_empty >= self.frame_duration:
                self.start_timestamp = time.monotonic() - (self.play_position / 1000)
                self.logger.bind(tag=TAG).debug(
                    f"Queue recovered from empty, reset timestamp, current play position: {self.play_position}ms, interval: {elapsed_since_empty:.0f}ms"
                )

        self.queue.append(("audio", opus_packet))
        # RelatedEventProcess
        self._sync_queue_events()

    def add_message(self, message_callback):
        """
        Add message to queue (send immediately, does not occupy playback time)

        Args:
            message_callback: message send callback function async def()
        """
        if len(self.queue) == 0 and self.play_position > 0:
            elapsed_since_empty = (time.monotonic() - self._last_queue_empty_time) * 1000
            if elapsed_since_empty >= self.frame_duration:
                self.start_timestamp = time.monotonic() - (self.play_position / 1000)
                self.logger.bind(tag=TAG).debug(
                    f"Queue recovered from empty, reset timestamp, current play position: {self.play_position}ms, interval: {elapsed_since_empty:.0f}ms"
                )

        self.queue.append(("message", message_callback))
        # RelatedEventProcess
        self._sync_queue_events()

    def _get_elapsed_ms(self):
        """Get elapsed time (ms)"""
        if self.start_timestamp is None:
            return 0
        return (time.monotonic() - self.start_timestamp) * 1000

    async def check_queue(self, send_audio_callback):
        """
        Check queue and send audio/messages on time

        Args:
            send_audio_callback: callback function for sending audio async def(opus_packet)
        """
        while self.queue:
            item = self.queue[0]
            item_type = item[0]

            if item_type == "message":
                # MessageType: send immediately, does not occupy playback time
                _, message_callback = item
                self.queue.popleft()
                try:
                    await message_callback()
                except _NORMAL_TRANSPORT_CLOSE_EXCEPTIONS:
                    raise
                except _TRANSPORT_CLOSE_EXCEPTIONS:
                    raise
                except Exception as e:
                    self.logger.bind(tag=TAG).error(f"Failed to send message: {e}")
                    raise

            elif item_type == "audio":
                if self.start_timestamp is None:
                    self.start_timestamp = time.monotonic()

                _, opus_packet = item

                # Loop wait until time reached
                while True:
                    # Calculate time difference
                    elapsed_ms = self._get_elapsed_ms()
                    output_ms = self.play_position

                    if elapsed_ms < output_ms:
                        # Not send time yet, calculate wait duration
                        wait_ms = output_ms - elapsed_ms

                        # After wait continue check (can be interrupted)
                        try:
                            await asyncio.sleep(wait_ms / 1000)
                        except asyncio.CancelledError:
                            self.logger.bind(tag=TAG).debug("Audio sending task canceled")
                            raise
                        # After wait ends recheck time (loop back to while True)
                    else:
                        # Time reached, break wait loop
                        break

                # Time reached, remove from queue and send
                self.queue.popleft()
                self.play_position += self.frame_duration
                try:
                    await send_audio_callback(opus_packet)
                except _NORMAL_TRANSPORT_CLOSE_EXCEPTIONS:
                    raise
                except _TRANSPORT_CLOSE_EXCEPTIONS:
                    raise
                except Exception as e:
                    self.logger.bind(tag=TAG).error(f"Failed to send audio: {e}")
                    raise

        # Clear after queue processedEvent
        self._drain_queue()

    def start_sending(self, send_audio_callback):
        """
        Start async send task

        Args:
            send_audio_callback: callback function for sending audio

        Returns:
            asyncio.Task: send task
        """

        self._ensure_loop_primitives()

        async def _send_loop():
            current_task = asyncio.current_task()
            try:
                while True:
                    # Wait queue dataEvent, no polling wait occupyingCPU
                    await self.queue_has_data_event.wait()

                    await self.check_queue(send_audio_callback)
            except asyncio.CancelledError:
                self._drain_queue()
                self.logger.bind(tag=TAG).debug("Audio sending loop stopped")
            except _NORMAL_TRANSPORT_CLOSE_EXCEPTIONS as e:
                self._drain_queue()
                self.logger.bind(tag=TAG).info(
                    f"audio_output_transport_closed reason=normal_close detail={e}"
                )
            except _TRANSPORT_CLOSE_EXCEPTIONS as e:
                self._drain_queue()
                self.logger.bind(tag=TAG).warning(
                    f"audio_output_transport_closed reason=connection_closed detail={e}"
                )
            except Exception as e:
                self._drain_queue()
                self.logger.bind(tag=TAG).error(f"Audio send loop exception: {e}")
            finally:
                if self.pending_send_task is current_task:
                    self.pending_send_task = None

        self.pending_send_task = asyncio.create_task(_send_loop())
        return self.pending_send_task

    def stop_sending(self):
        """Stop sending task"""
        if self.pending_send_task and not self.pending_send_task.done():
            self.pending_send_task.cancel()
            self.logger.bind(tag=TAG).debug("Audio sending task canceled")

    async def stop_sending_and_wait(self):
        """Stop sending task and retrieve cancellation before loop shutdown."""
        task = self.pending_send_task
        if task is None:
            return
        if not task.done():
            task.cancel()
            self.logger.bind(tag=TAG).debug("Audio sending task canceled")
        try:
            await task
        except asyncio.CancelledError:
            pass
        finally:
            if self.pending_send_task is task:
                self.pending_send_task = None
