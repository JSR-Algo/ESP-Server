import unittest
from types import SimpleNamespace

from core.voice.google_live.interaction_controller import (
    GoogleLiveInteractionController,
    InteractionState,
)


class GoogleLiveInteractionControllerTest(unittest.TestCase):
    def test_state_machine_exposes_production_states(self):
        self.assertEqual(
            {state.value for state in InteractionState},
            {
                "IDLE",
                "LISTENING",
                "USER_STREAMING",
                "WAITING_MODEL",
                "MODEL_SPEAKING",
                "MUSIC_PLAYING",
                "INTERRUPTING",
                "RECONNECTING",
                "FALLBACK",
                "MUTED",
            },
        )

    def test_identity_snapshot_carries_session_turn_response_and_audio_seq(self):
        conn = SimpleNamespace(
            device_id="device-1",
            client_id="client-1",
            session_id="server-session-1",
        )
        controller = GoogleLiveInteractionController(conn, live_connection_id="live-1")

        first = controller.next_audio_identity()
        controller.begin_turn(reason="wake")
        second = controller.next_audio_identity(state=InteractionState.LISTENING)

        self.assertEqual(first["device_id"], "device-1")
        self.assertEqual(first["client_id"], "client-1")
        self.assertEqual(first["server_session_id"], "server-session-1")
        self.assertEqual(first["live_connection_id"], "live-1")
        self.assertEqual(first["audio_seq"], 1)
        self.assertEqual(second["audio_seq"], 2)
        self.assertEqual(second["turn_id"], 1)
        self.assertEqual(second["response_id"], 1)
        self.assertEqual(second["state"], "LISTENING")

    def test_stale_model_events_are_rejected_after_interrupt(self):
        controller = GoogleLiveInteractionController(SimpleNamespace())
        response_id = controller.response_id

        controller.begin_interrupt(reason="wake")

        self.assertTrue(controller.is_stale_response(response_id))
        self.assertFalse(controller.is_stale_response(controller.response_id))


if __name__ == "__main__":
    unittest.main()
