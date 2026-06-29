import importlib
import unittest


class PhysicalSmokeAuditTest(unittest.TestCase):
    def test_audit_passes_for_physical_audio_interrupt_smoke(self):
        audit = importlib.import_module("scripts.physical_smoke_audit")
        log_text = """
260518 20:10:00[core.connection]-INFO-192.168.0.50 conn - Headers: {'device-id': '3c:0f:02:de:c2:e0', 'client-id': 'd16afa54-eb44-4fcb-8cac-cdefdf05f6fc', 'user-agent': 'TBOT/2.2.6'}
260518 20:10:01[GoogleLive]-INFO-Google Live input_audio_diag encoded_bytes=80 decoded_bytes=640 rms=921 source_rate=16000 target_rate=16000 sample_width=2
260518 20:10:01[GoogleLive]-INFO-Google Live transcript source=user chars=42
""" + "\n".join(
            "260518 20:10:{:02d}[GoogleLive]-INFO-Google Live user_interrupted reason=audio_input cancelled_response_id={} next_response_id={}".format(i, i, i + 1)
            for i in range(10)
        )

        result = audit.audit_log(
            log_text,
            device_id="3c:0f:02:de:c2:e0",
            client_id="d16afa54-eb44-4fcb-8cac-cdefdf05f6fc",
            min_interrupts=10,
        )

        self.assertTrue(result["passed"])
        self.assertEqual(result["audio_interrupts"], 10)
        self.assertEqual(result["input_audio_diag"], 1)
        self.assertEqual(result["user_transcripts"], 1)

    def test_audit_rejects_local_python_soak_as_physical_evidence(self):
        audit = importlib.import_module("scripts.physical_smoke_audit")
        log_text = """
260518 20:03:08[core.connection]-INFO-127.0.0.1 conn - Headers: {'device-id': '3c:0f:02:de:c2:e0', 'client-id': 'd16afa54-eb44-4fcb-8cac-cdefdf05f6fc', 'user-agent': 'Python/3.11 websockets/14.2'}
260518 20:03:09[GoogleLive]-INFO-Google Live user_interrupted reason=text_input cancelled_response_id=0 next_response_id=1
"""

        result = audit.audit_log(
            log_text,
            device_id="3c:0f:02:de:c2:e0",
            client_id="d16afa54-eb44-4fcb-8cac-cdefdf05f6fc",
            min_interrupts=10,
        )

        self.assertFalse(result["passed"])
        self.assertIn("physical_ws_connected", result["missing"])
        self.assertIn("user_transcript", result["missing"])
        self.assertIn("audio_interrupts>=10", result["missing"])

    def test_audit_rejects_server_ip_even_with_firmware_like_user_agent(self):
        audit = importlib.import_module("scripts.physical_smoke_audit")
        log_text = """
260518 20:10:00[core.connection]-INFO-192.168.0.114 conn - Headers: {'device-id': '3c:0f:02:de:c2:e0', 'client-id': 'd16afa54-eb44-4fcb-8cac-cdefdf05f6fc', 'user-agent': 'TBOT/2.2.7'}
260518 20:10:01[GoogleLive]-INFO-Google Live input_audio_diag encoded_bytes=80 decoded_bytes=640 rms=921 source_rate=16000 target_rate=16000 sample_width=2
260518 20:10:01[GoogleLive]-INFO-Google Live transcript source=user chars=42
""" + "\n".join(
            "260518 20:10:{:02d}[GoogleLive]-INFO-Google Live user_interrupted reason=audio_input cancelled_response_id={} next_response_id={}".format(i, i, i + 1)
            for i in range(10)
        )

        result = audit.audit_log(
            log_text,
            device_id="3c:0f:02:de:c2:e0",
            client_id="d16afa54-eb44-4fcb-8cac-cdefdf05f6fc",
            min_interrupts=10,
            server_ip="192.168.0.114",
        )

        self.assertFalse(result["passed"])
        self.assertIn("physical_ws_connected", result["missing"])

    def test_audit_does_not_accept_audio_without_user_transcript(self):
        audit = importlib.import_module("scripts.physical_smoke_audit")
        log_text = """
260518 20:10:00[core.connection]-INFO-192.168.0.50 conn - Headers: {'device-id': '3c:0f:02:de:c2:e0', 'client-id': 'd16afa54-eb44-4fcb-8cac-cdefdf05f6fc', 'user-agent': 'TBOT/2.2.7'}
260518 20:10:01[GoogleLive]-INFO-Google Live input_audio_diag encoded_bytes=80 decoded_bytes=640 rms=921 source_rate=16000 target_rate=16000 sample_width=2
""" + "\n".join(
            "260518 20:10:{:02d}[GoogleLive]-INFO-Google Live user_interrupted reason=audio_input cancelled_response_id={} next_response_id={}".format(i, i, i + 1)
            for i in range(10)
        )

        result = audit.audit_log(
            log_text,
            device_id="3c:0f:02:de:c2:e0",
            client_id="d16afa54-eb44-4fcb-8cac-cdefdf05f6fc",
            min_interrupts=10,
        )

        self.assertFalse(result["passed"])
        self.assertIn("user_transcript", result["missing"])


    def test_audit_passes_for_full_physical_lesson_flow(self):
        audit = importlib.import_module("scripts.physical_smoke_audit")
        step_lines = []
        for index, step_type in enumerate(
            [
                "greeting",
                "review",
                "focus",
                "model",
                "listen",
                "repeat",
                "fillBlank",
                "feedback",
                "celebrate",
            ],
            start=1,
        ):
            step_lines.extend(
                [
                    f"260518 20:11:{index:02d}[LessonRuntime]-INFO-emit lesson_step stepId=s{index} stepType={step_type} backgroundScene=1 teachingObject=1 robotOverlay=1 prompt=1",
                    f"260518 20:11:{index:02d}[lesson_handler]-INFO-lesson_step poster fetched+drawn from URL stepId=s{index}",
                    f"260518 20:11:{index:02d}[lesson_handler]-INFO-lesson_step teaching object fetched+drawn from URL stepId=s{index}",
                    f"260518 20:11:{index:02d}[lesson_handler]-INFO-lesson_step robot overlay fetched+drawn from URL stepId=s{index}",
                    f"260518 20:11:{index:02d}[lesson_handler]-INFO-lesson_step rendered stepId=s{index} passive=0 degraded=0",
                    f"260518 20:11:{index:02d}[GoogleLive]-INFO-Google Live lesson_step_prompt queued via tts text='Can you say step {index} with TeeBot?'"
                    if step_type in {"model", "listen", "repeat", "fillBlank"}
                    else f"260518 20:11:{index:02d}[GoogleLive]-INFO-Google Live lesson_step_prompt queued via tts text='step {index}'",
                ]
            )
            if step_type in {"model", "listen", "repeat", "fillBlank"}:
                step_lines.extend(
                    [
                        f"260518 20:11:{index:02d}[LessonRuntime]-INFO-child response window opened stepId=s{index} listening=true",
                        f"260518 20:11:{index:02d}[LessonRuntime]-INFO-interactive child response accepted stepId=s{index} recognizedText=step{index}",
                        f"260518 20:11:{index:02d}[LessonRuntime]-INFO-lesson_progress event=step_completed result=success stepId=s{index}",
                    ]
                )
        log_text = """
260518 20:10:00[core.connection]-INFO-192.168.0.50 conn - Headers: {'device-id': '3c:0f:02:de:c2:e0', 'client-id': 'd16afa54-eb44-4fcb-8cac-cdefdf05f6fc', 'user-agent': 'TBOT/2.2.7'}
260518 20:10:01[GoogleLive]-INFO-Google Live input_audio_diag encoded_bytes=80 decoded_bytes=640 rms=921 source_rate=16000 target_rate=16000 sample_width=2
260518 20:10:02[GoogleLive]-INFO-Google Live transcript source=user chars=14 text='bắt đầu bài học'
260518 20:10:03[GoogleLive]-INFO-Google Live lesson_start_intent tool=start_lesson text_preview='bắt đầu bài học'
260518 20:10:04[LessonRuntime]-INFO-emit lesson_prepare assignmentId=assignment-1 lessonId=w01-d01-barn-say-it-age3-20260617
260518 20:10:05[LessonRuntime]-INFO-emit lesson_start
""" + "\n".join(step_lines) + """
260518 20:11:59[LessonRuntime]-INFO-emit lesson_stop reason=COMPLETED
260518 20:12:00[LessonRuntime]-INFO-lesson_completed stepsCompleted=9
""" + "\n".join(
            "260518 20:10:{:02d}[GoogleLive]-INFO-Google Live user_interrupted reason=audio_input cancelled_response_id={} next_response_id={}".format(i, i, i + 1)
            for i in range(10)
        )

        result = audit.audit_log(
            log_text,
            device_id="3c:0f:02:de:c2:e0",
            client_id="d16afa54-eb44-4fcb-8cac-cdefdf05f6fc",
            min_interrupts=10,
            require_lesson=True,
            expected_lesson_steps=9,
            expected_interactive_steps=4,
        )

        self.assertTrue(result["passed"])
        self.assertEqual(result["lesson_steps"], 9)
        self.assertEqual(result["lesson_prompt_tts"], 9)
        self.assertEqual(result["lesson_prompt_after_render"], 9)
        self.assertEqual(result["lesson_firmware_rendered"], 9)
        self.assertEqual(result["lesson_robot_overlays_drawn"], 9)
        self.assertEqual(result["lesson_step_layers_drawn_by_step"], 9)
        self.assertEqual(result["interactive_child_response_windows"], 4)
        self.assertEqual(result["interactive_child_responses"], 4)
        self.assertEqual(result["interactive_child_responses_observed"], 4)
        self.assertEqual(result["interactive_child_response_ordered"], 4)
        self.assertEqual(result["interactive_child_response_after_prompt"], 4)
        self.assertEqual(result["interactive_child_response_window_after_prompt"], 4)
        self.assertEqual(result["interactive_guided_prompts"], 4)
        self.assertEqual(result["interactive_child_response_before_progress"], 4)

    def test_audit_rejects_lesson_flow_missing_expected_interactive_child_turns(self):
        audit = importlib.import_module("scripts.physical_smoke_audit")
        log_text = """
260518 20:10:00[core.connection]-INFO-192.168.0.50 conn - Headers: {'device-id': '3c:0f:02:de:c2:e0', 'client-id': 'd16afa54-eb44-4fcb-8cac-cdefdf05f6fc', 'user-agent': 'TBOT/2.2.7'}
260518 20:10:01[GoogleLive]-INFO-Google Live input_audio_diag encoded_bytes=80 decoded_bytes=640 rms=921 source_rate=16000 target_rate=16000 sample_width=2
260518 20:10:02[GoogleLive]-INFO-Google Live transcript source=user chars=14 text='bắt đầu bài học'
260518 20:10:03[GoogleLive]-INFO-Google Live lesson_start_intent tool=start_lesson text_preview='bắt đầu bài học'
260518 20:10:04[LessonRuntime]-INFO-emit lesson_prepare assignmentId=assignment-1 lessonId=w01-d01-barn-say-it-age3-20260617
260518 20:10:05[LessonRuntime]-INFO-emit lesson_start
260518 20:11:01[LessonRuntime]-INFO-emit lesson_step stepId=s1 stepType=listen backgroundScene=1 teachingObject=1 robotOverlay=1 prompt=1
260518 20:11:02[lesson_handler]-INFO-lesson_step poster fetched+drawn from URL stepId=s1
260518 20:11:03[lesson_handler]-INFO-lesson_step teaching object fetched+drawn from URL stepId=s1
260518 20:11:04[lesson_handler]-INFO-lesson_step robot overlay fetched+drawn from URL stepId=s1
260518 20:11:05[lesson_handler]-INFO-lesson_step rendered stepId=s1 passive=0 degraded=0
260518 20:11:06[GoogleLive]-INFO-Google Live lesson_step_prompt queued via tts stepId=s1 text='step 1'
260518 20:11:59[LessonRuntime]-INFO-emit lesson_stop reason=COMPLETED
260518 20:12:00[LessonRuntime]-INFO-lesson_completed stepsCompleted=1
""" + "\n".join(
            "260518 20:10:{:02d}[GoogleLive]-INFO-Google Live user_interrupted reason=audio_input cancelled_response_id={} next_response_id={}".format(i, i, i + 1)
            for i in range(10)
        )

        result = audit.audit_log(
            log_text,
            device_id="3c:0f:02:de:c2:e0",
            client_id="d16afa54-eb44-4fcb-8cac-cdefdf05f6fc",
            min_interrupts=10,
            require_lesson=True,
            expected_lesson_steps=1,
            expected_interactive_steps=1,
        )

        self.assertFalse(result["passed"])
        self.assertIn("interactive_child_response_windows>=1", result["missing"])
        self.assertIn("interactive_child_responses>=1", result["missing"])

    def test_audit_rejects_child_response_before_child_response_window(self):
        audit = importlib.import_module("scripts.physical_smoke_audit")
        log_text = """
260518 20:10:00[core.connection]-INFO-192.168.0.50 conn - Headers: {'device-id': '3c:0f:02:de:c2:e0', 'client-id': 'd16afa54-eb44-4fcb-8cac-cdefdf05f6fc', 'user-agent': 'TBOT/2.2.7'}
260518 20:10:01[GoogleLive]-INFO-Google Live input_audio_diag encoded_bytes=80 decoded_bytes=640 rms=921 source_rate=16000 target_rate=16000 sample_width=2
260518 20:10:02[GoogleLive]-INFO-Google Live transcript source=user chars=14 text='bắt đầu bài học'
260518 20:10:03[GoogleLive]-INFO-Google Live lesson_start_intent tool=start_lesson text_preview='bắt đầu bài học'
260518 20:10:04[LessonRuntime]-INFO-emit lesson_prepare assignmentId=assignment-1 lessonId=w01-d01-barn-say-it-age3-20260617
260518 20:10:05[LessonRuntime]-INFO-emit lesson_start
260518 20:11:01[LessonRuntime]-INFO-emit lesson_step stepId=s1 stepType=listen backgroundScene=1 teachingObject=1 robotOverlay=1 prompt=1
260518 20:11:02[lesson_handler]-INFO-lesson_step poster fetched+drawn from URL stepId=s1
260518 20:11:03[lesson_handler]-INFO-lesson_step teaching object fetched+drawn from URL stepId=s1
260518 20:11:04[lesson_handler]-INFO-lesson_step robot overlay fetched+drawn from URL stepId=s1
260518 20:11:05[lesson_handler]-INFO-lesson_step rendered stepId=s1 passive=0 degraded=0
260518 20:11:06[GoogleLive]-INFO-Google Live lesson_step_prompt queued via tts stepId=s1 text='step 1'
260518 20:11:07[LessonRuntime]-INFO-interactive child response accepted stepId=s1
260518 20:11:08[LessonRuntime]-INFO-child response window opened stepId=s1 listening=true
260518 20:11:59[LessonRuntime]-INFO-emit lesson_stop reason=COMPLETED
260518 20:12:00[LessonRuntime]-INFO-lesson_completed stepsCompleted=1
""" + "\n".join(
            "260518 20:10:{:02d}[GoogleLive]-INFO-Google Live user_interrupted reason=audio_input cancelled_response_id={} next_response_id={}".format(i, i, i + 1)
            for i in range(10)
        )

        result = audit.audit_log(
            log_text,
            device_id="3c:0f:02:de:c2:e0",
            client_id="d16afa54-eb44-4fcb-8cac-cdefdf05f6fc",
            min_interrupts=10,
            require_lesson=True,
            expected_lesson_steps=1,
            expected_interactive_steps=1,
        )

        self.assertFalse(result["passed"])
        self.assertEqual(result["interactive_child_response_windows"], 1)
        self.assertEqual(result["interactive_child_responses"], 1)
        self.assertIn("interactive_child_response_ordered>=1", result["missing"])

    def test_audit_rejects_step_completed_before_child_response(self):
        audit = importlib.import_module("scripts.physical_smoke_audit")
        log_text = """
260518 20:10:00[core.connection]-INFO-192.168.0.50 conn - Headers: {'device-id': '3c:0f:02:de:c2:e0', 'client-id': 'd16afa54-eb44-4fcb-8cac-cdefdf05f6fc', 'user-agent': 'TBOT/2.2.7'}
260518 20:10:01[GoogleLive]-INFO-Google Live input_audio_diag encoded_bytes=80 decoded_bytes=640 rms=921 source_rate=16000 target_rate=16000 sample_width=2
260518 20:10:02[GoogleLive]-INFO-Google Live transcript source=user chars=14 text='bắt đầu bài học'
260518 20:10:03[GoogleLive]-INFO-Google Live lesson_start_intent tool=start_lesson text_preview='bắt đầu bài học'
260518 20:10:04[LessonRuntime]-INFO-emit lesson_prepare assignmentId=assignment-1 lessonId=w01-d01-barn-say-it-age3-20260617
260518 20:10:05[LessonRuntime]-INFO-emit lesson_start
260518 20:11:01[LessonRuntime]-INFO-emit lesson_step stepId=s1 stepType=listen backgroundScene=1 teachingObject=1 robotOverlay=1 prompt=1
260518 20:11:02[lesson_handler]-INFO-lesson_step poster fetched+drawn from URL stepId=s1
260518 20:11:03[lesson_handler]-INFO-lesson_step teaching object fetched+drawn from URL stepId=s1
260518 20:11:04[lesson_handler]-INFO-lesson_step robot overlay fetched+drawn from URL stepId=s1
260518 20:11:05[lesson_handler]-INFO-lesson_step rendered stepId=s1 passive=0 degraded=0
260518 20:11:06[GoogleLive]-INFO-Google Live lesson_step_prompt queued via tts stepId=s1 text='step 1'
260518 20:11:07[LessonRuntime]-INFO-child response window opened stepId=s1 listening=true
260518 20:11:08[LessonRuntime]-INFO-lesson_progress event=step_completed result=success stepId=s1
260518 20:11:09[LessonRuntime]-INFO-interactive child response accepted stepId=s1
260518 20:11:59[LessonRuntime]-INFO-emit lesson_stop reason=COMPLETED
260518 20:12:00[LessonRuntime]-INFO-lesson_completed stepsCompleted=1
""" + "\n".join(
            "260518 20:10:{:02d}[GoogleLive]-INFO-Google Live user_interrupted reason=audio_input cancelled_response_id={} next_response_id={}".format(i, i, i + 1)
            for i in range(10)
        )

        result = audit.audit_log(
            log_text,
            device_id="3c:0f:02:de:c2:e0",
            client_id="d16afa54-eb44-4fcb-8cac-cdefdf05f6fc",
            min_interrupts=10,
            require_lesson=True,
            expected_lesson_steps=1,
            expected_interactive_steps=1,
        )

        self.assertFalse(result["passed"])
        self.assertEqual(result["interactive_child_response_windows"], 1)
        self.assertEqual(result["interactive_child_responses"], 1)
        self.assertIn("interactive_child_response_before_progress>=1", result["missing"])

    def test_audit_rejects_child_response_before_step_prompt(self):
        audit = importlib.import_module("scripts.physical_smoke_audit")
        log_text = """
260518 20:10:00[core.connection]-INFO-192.168.0.50 conn - Headers: {'device-id': '3c:0f:02:de:c2:e0', 'client-id': 'd16afa54-eb44-4fcb-8cac-cdefdf05f6fc', 'user-agent': 'TBOT/2.2.7'}
260518 20:10:01[GoogleLive]-INFO-Google Live input_audio_diag encoded_bytes=80 decoded_bytes=640 rms=921 source_rate=16000 target_rate=16000 sample_width=2
260518 20:10:02[GoogleLive]-INFO-Google Live transcript source=user chars=14 text='bắt đầu bài học'
260518 20:10:03[GoogleLive]-INFO-Google Live lesson_start_intent tool=start_lesson text_preview='bắt đầu bài học'
260518 20:10:04[LessonRuntime]-INFO-emit lesson_prepare assignmentId=assignment-1 lessonId=w01-d01-barn-say-it-age3-20260617
260518 20:10:05[LessonRuntime]-INFO-emit lesson_start
260518 20:11:01[LessonRuntime]-INFO-emit lesson_step stepId=s1 stepType=listen backgroundScene=1 teachingObject=1 robotOverlay=1 prompt=1
260518 20:11:02[lesson_handler]-INFO-lesson_step poster fetched+drawn from URL stepId=s1
260518 20:11:03[lesson_handler]-INFO-lesson_step teaching object fetched+drawn from URL stepId=s1
260518 20:11:04[lesson_handler]-INFO-lesson_step robot overlay fetched+drawn from URL stepId=s1
260518 20:11:05[lesson_handler]-INFO-lesson_step rendered stepId=s1 passive=0 degraded=0
260518 20:11:06[LessonRuntime]-INFO-child response window opened stepId=s1 listening=true
260518 20:11:07[LessonRuntime]-INFO-interactive child response accepted stepId=s1
260518 20:11:08[GoogleLive]-INFO-Google Live lesson_step_prompt queued via tts stepId=s1 text='step 1'
260518 20:11:09[LessonRuntime]-INFO-lesson_progress event=step_completed result=success stepId=s1
260518 20:11:59[LessonRuntime]-INFO-emit lesson_stop reason=COMPLETED
260518 20:12:00[LessonRuntime]-INFO-lesson_completed stepsCompleted=1
""" + "\n".join(
            "260518 20:10:{:02d}[GoogleLive]-INFO-Google Live user_interrupted reason=audio_input cancelled_response_id={} next_response_id={}".format(i, i, i + 1)
            for i in range(10)
        )

        result = audit.audit_log(
            log_text,
            device_id="3c:0f:02:de:c2:e0",
            client_id="d16afa54-eb44-4fcb-8cac-cdefdf05f6fc",
            min_interrupts=10,
            require_lesson=True,
            expected_lesson_steps=1,
            expected_interactive_steps=1,
        )

        self.assertFalse(result["passed"])
        self.assertEqual(result["interactive_child_response_windows"], 1)
        self.assertEqual(result["interactive_child_responses"], 1)
        self.assertIn("interactive_child_response_after_prompt>=1", result["missing"])

    def test_audit_rejects_child_response_window_before_step_prompt(self):
        audit = importlib.import_module("scripts.physical_smoke_audit")
        log_text = """
260518 20:10:00[core.connection]-INFO-192.168.0.50 conn - Headers: {'device-id': '3c:0f:02:de:c2:e0', 'client-id': 'd16afa54-eb44-4fcb-8cac-cdefdf05f6fc', 'user-agent': 'TBOT/2.2.7'}
260518 20:10:01[GoogleLive]-INFO-Google Live input_audio_diag encoded_bytes=80 decoded_bytes=640 rms=921 source_rate=16000 target_rate=16000 sample_width=2
260518 20:10:02[GoogleLive]-INFO-Google Live transcript source=user chars=14 text='bắt đầu bài học'
260518 20:10:03[GoogleLive]-INFO-Google Live lesson_start_intent tool=start_lesson text_preview='bắt đầu bài học'
260518 20:10:04[LessonRuntime]-INFO-emit lesson_prepare assignmentId=assignment-1 lessonId=w01-d01-barn-say-it-age3-20260617
260518 20:10:05[LessonRuntime]-INFO-emit lesson_start
260518 20:11:01[LessonRuntime]-INFO-emit lesson_step stepId=s1 stepType=listen backgroundScene=1 teachingObject=1 robotOverlay=1 prompt=1
260518 20:11:02[lesson_handler]-INFO-lesson_step poster fetched+drawn from URL stepId=s1
260518 20:11:03[lesson_handler]-INFO-lesson_step teaching object fetched+drawn from URL stepId=s1
260518 20:11:04[lesson_handler]-INFO-lesson_step robot overlay fetched+drawn from URL stepId=s1
260518 20:11:05[lesson_handler]-INFO-lesson_step rendered stepId=s1 passive=0 degraded=0
260518 20:11:06[LessonRuntime]-INFO-child response window opened stepId=s1 listening=true
260518 20:11:07[GoogleLive]-INFO-Google Live lesson_step_prompt queued via tts stepId=s1 text='Can you say barn with TeeBot?'
260518 20:11:08[LessonRuntime]-INFO-interactive child response accepted stepId=s1 recognizedText=barn
260518 20:11:09[LessonRuntime]-INFO-lesson_progress event=step_completed result=success stepId=s1
260518 20:11:59[LessonRuntime]-INFO-emit lesson_stop reason=COMPLETED
260518 20:12:00[LessonRuntime]-INFO-lesson_completed stepsCompleted=1
""" + "\n".join(
            "260518 20:10:{:02d}[GoogleLive]-INFO-Google Live user_interrupted reason=audio_input cancelled_response_id={} next_response_id={}".format(i, i, i + 1)
            for i in range(10)
        )

        result = audit.audit_log(
            log_text,
            device_id="3c:0f:02:de:c2:e0",
            client_id="d16afa54-eb44-4fcb-8cac-cdefdf05f6fc",
            min_interrupts=10,
            require_lesson=True,
            expected_lesson_steps=1,
            expected_interactive_steps=1,
        )

        self.assertFalse(result["passed"])
        self.assertEqual(result["interactive_child_response_windows"], 1)
        self.assertEqual(result["interactive_child_responses"], 1)
        self.assertEqual(result["interactive_child_response_window_after_prompt"], 0)
        self.assertIn("interactive_child_response_window_after_prompt>=1", result["missing"])

    def test_audit_accepts_google_live_child_response_audio_window_log(self):
        audit = importlib.import_module("scripts.physical_smoke_audit")
        log_text = """
260518 20:10:00[core.connection]-INFO-192.168.0.50 conn - Headers: {'device-id': '3c:0f:02:de:c2:e0', 'client-id': 'd16afa54-eb44-4fcb-8cac-cdefdf05f6fc', 'user-agent': 'TBOT/2.2.7'}
260518 20:10:01[GoogleLive]-INFO-Google Live input_audio_diag encoded_bytes=80 decoded_bytes=640 rms=921 source_rate=16000 target_rate=16000 sample_width=2
260518 20:10:02[GoogleLive]-INFO-Google Live transcript source=user chars=14 text='bắt đầu bài học'
260518 20:10:03[GoogleLive]-INFO-Google Live lesson_start_intent tool=start_lesson text_preview='bắt đầu bài học'
260518 20:10:04[LessonRuntime]-INFO-emit lesson_prepare assignmentId=assignment-1 lessonId=w01-d01-barn-say-it-age3-20260617
260518 20:10:05[LessonRuntime]-INFO-emit lesson_start
260518 20:11:01[LessonRuntime]-INFO-emit lesson_step stepId=s1 stepType=listen backgroundScene=1 teachingObject=1 robotOverlay=1 prompt=1
260518 20:11:02[lesson_handler]-INFO-lesson_step poster fetched+drawn from URL stepId=s1
260518 20:11:03[lesson_handler]-INFO-lesson_step teaching object fetched+drawn from URL stepId=s1
260518 20:11:04[lesson_handler]-INFO-lesson_step robot overlay fetched+drawn from URL stepId=s1
260518 20:11:05[lesson_handler]-INFO-lesson_step rendered stepId=s1 passive=0 degraded=0
260518 20:11:06[GoogleLive]-INFO-Google Live lesson_step_prompt queued via tts stepId=s1 text='Can you say barn with TeeBot?'
260518 20:11:07[GoogleLive]-INFO-Google Live user_audio_window_open reason=lesson_child_response window_ms=25000
260518 20:11:08[LessonRuntime]-INFO-interactive child response accepted stepId=s1 recognizedText=barn
260518 20:11:09[LessonRuntime]-INFO-lesson_progress event=step_completed result=success stepId=s1
260518 20:11:59[LessonRuntime]-INFO-emit lesson_stop reason=COMPLETED
260518 20:12:00[LessonRuntime]-INFO-lesson_completed stepsCompleted=1
""" + "\n".join(
            "260518 20:10:{:02d}[GoogleLive]-INFO-Google Live user_interrupted reason=audio_input cancelled_response_id={} next_response_id={}".format(i, i, i + 1)
            for i in range(10)
        )

        result = audit.audit_log(
            log_text,
            device_id="3c:0f:02:de:c2:e0",
            client_id="d16afa54-eb44-4fcb-8cac-cdefdf05f6fc",
            min_interrupts=10,
            require_lesson=True,
            expected_lesson_steps=1,
            expected_interactive_steps=1,
        )

        self.assertTrue(result["passed"])
        self.assertEqual(result["interactive_child_response_windows"], 1)
        self.assertEqual(result["interactive_child_response_ordered"], 1)
        self.assertEqual(result["interactive_child_response_window_after_prompt"], 1)

    def test_audit_accepts_google_live_step_prompt_queued_via_live_text(self):
        audit = importlib.import_module("scripts.physical_smoke_audit")
        log_text = """
260518 20:10:00[core.connection]-INFO-192.168.0.50 conn - Headers: {'device-id': '3c:0f:02:de:c2:e0', 'client-id': 'd16afa54-eb44-4fcb-8cac-cdefdf05f6fc', 'user-agent': 'TBOT/2.2.7'}
260518 20:10:01[GoogleLive]-INFO-Google Live input_audio_diag encoded_bytes=80 decoded_bytes=640 rms=921 source_rate=16000 target_rate=16000 sample_width=2
260518 20:10:02[GoogleLive]-INFO-Google Live transcript source=user chars=14 text='bắt đầu bài học'
260518 20:10:03[GoogleLive]-INFO-Google Live lesson_start_intent tool=start_lesson text_preview='bắt đầu bài học'
260518 20:10:04[LessonRuntime]-INFO-emit lesson_prepare assignmentId=assignment-1 lessonId=w01-d01-barn-say-it-age3-20260617
260518 20:10:05[LessonRuntime]-INFO-emit lesson_start
260518 20:11:01[LessonRuntime]-INFO-emit lesson_step stepId=s1 stepType=listen backgroundScene=1 teachingObject=1 robotOverlay=1 prompt=1
260518 20:11:02[lesson_handler]-INFO-lesson_step poster fetched+drawn from URL stepId=s1
260518 20:11:03[lesson_handler]-INFO-lesson_step teaching object fetched+drawn from URL stepId=s1
260518 20:11:04[lesson_handler]-INFO-lesson_step robot overlay fetched+drawn from URL stepId=s1
260518 20:11:05[lesson_handler]-INFO-lesson_step rendered stepId=s1 passive=0 degraded=0
260518 20:11:06[GoogleLive]-INFO-Google Live lesson_step_prompt queued via live text='Can you say barn with TeeBot?'
260518 20:11:07[GoogleLive]-INFO-Google Live user_audio_window_open reason=lesson_child_response window_ms=25000
260518 20:11:08[LessonRuntime]-INFO-interactive child response accepted stepId=s1 recognizedText=barn
260518 20:11:09[LessonRuntime]-INFO-lesson_progress event=step_completed result=success stepId=s1
260518 20:11:59[LessonRuntime]-INFO-emit lesson_stop reason=COMPLETED
260518 20:12:00[LessonRuntime]-INFO-lesson_completed stepsCompleted=1
""" + "\n".join(
            "260518 20:10:{:02d}[GoogleLive]-INFO-Google Live user_interrupted reason=audio_input cancelled_response_id={} next_response_id={}".format(i, i, i + 1)
            for i in range(10)
        )

        result = audit.audit_log(
            log_text,
            device_id="3c:0f:02:de:c2:e0",
            client_id="d16afa54-eb44-4fcb-8cac-cdefdf05f6fc",
            min_interrupts=10,
            require_lesson=True,
            expected_lesson_steps=1,
            expected_interactive_steps=1,
        )

        self.assertTrue(result["passed"])
        self.assertEqual(result["lesson_prompt_tts"], 1)
        self.assertEqual(result["lesson_prompt_after_render"], 1)
        self.assertEqual(result["interactive_guided_prompts"], 1)
        self.assertEqual(result["interactive_child_response_window_after_prompt"], 1)

    def test_audit_rejects_command_only_interactive_prompt(self):
        audit = importlib.import_module("scripts.physical_smoke_audit")
        log_text = """
260518 20:10:00[core.connection]-INFO-192.168.0.50 conn - Headers: {'device-id': '3c:0f:02:de:c2:e0', 'client-id': 'd16afa54-eb44-4fcb-8cac-cdefdf05f6fc', 'user-agent': 'TBOT/2.2.7'}
260518 20:10:01[GoogleLive]-INFO-Google Live input_audio_diag encoded_bytes=80 decoded_bytes=640 rms=921 source_rate=16000 target_rate=16000 sample_width=2
260518 20:10:02[GoogleLive]-INFO-Google Live transcript source=user chars=14 text='bắt đầu bài học'
260518 20:10:03[GoogleLive]-INFO-Google Live lesson_start_intent tool=start_lesson text_preview='bắt đầu bài học'
260518 20:10:04[LessonRuntime]-INFO-emit lesson_prepare assignmentId=assignment-1 lessonId=w01-d01-barn-say-it-age3-20260617
260518 20:10:05[LessonRuntime]-INFO-emit lesson_start
260518 20:11:01[LessonRuntime]-INFO-emit lesson_step stepId=s1 stepType=listen backgroundScene=1 teachingObject=1 robotOverlay=1 prompt=1
260518 20:11:02[lesson_handler]-INFO-lesson_step poster fetched+drawn from URL stepId=s1
260518 20:11:03[lesson_handler]-INFO-lesson_step teaching object fetched+drawn from URL stepId=s1
260518 20:11:04[lesson_handler]-INFO-lesson_step robot overlay fetched+drawn from URL stepId=s1
260518 20:11:05[lesson_handler]-INFO-lesson_step rendered stepId=s1 passive=0 degraded=0
260518 20:11:06[GoogleLive]-INFO-Google Live lesson_step_prompt queued via tts stepId=s1 text='Say barn with TeeBot.'
260518 20:11:07[LessonRuntime]-INFO-child response window opened stepId=s1 listening=true
260518 20:11:08[LessonRuntime]-INFO-interactive child response accepted stepId=s1
260518 20:11:09[LessonRuntime]-INFO-lesson_progress event=step_completed result=success stepId=s1
260518 20:11:59[LessonRuntime]-INFO-emit lesson_stop reason=COMPLETED
260518 20:12:00[LessonRuntime]-INFO-lesson_completed stepsCompleted=1
""" + "\n".join(
            "260518 20:10:{:02d}[GoogleLive]-INFO-Google Live user_interrupted reason=audio_input cancelled_response_id={} next_response_id={}".format(i, i, i + 1)
            for i in range(10)
        )

        result = audit.audit_log(
            log_text,
            device_id="3c:0f:02:de:c2:e0",
            client_id="d16afa54-eb44-4fcb-8cac-cdefdf05f6fc",
            min_interrupts=10,
            require_lesson=True,
            expected_lesson_steps=1,
            expected_interactive_steps=1,
        )

        self.assertFalse(result["passed"])
        self.assertEqual(result["interactive_guided_prompts"], 0)
        self.assertIn("interactive_guided_prompts>=1", result["missing"])

    def test_audit_rejects_immediate_pronunciation_correction(self):
        audit = importlib.import_module("scripts.physical_smoke_audit")
        log_text = """
260518 20:10:00[core.connection]-INFO-192.168.0.50 conn - Headers: {'device-id': '3c:0f:02:de:c2:e0', 'client-id': 'd16afa54-eb44-4fcb-8cac-cdefdf05f6fc', 'user-agent': 'TBOT/2.2.7'}
260518 20:10:01[GoogleLive]-INFO-Google Live input_audio_diag encoded_bytes=80 decoded_bytes=640 rms=921 source_rate=16000 target_rate=16000 sample_width=2
260518 20:10:02[GoogleLive]-INFO-Google Live transcript source=user chars=14 text='bắt đầu bài học'
260518 20:10:03[GoogleLive]-INFO-Google Live lesson_start_intent tool=start_lesson text_preview='bắt đầu bài học'
260518 20:10:04[LessonRuntime]-INFO-emit lesson_prepare assignmentId=assignment-1 lessonId=w01-d01-barn-say-it-age3-20260617
260518 20:10:05[LessonRuntime]-INFO-emit lesson_start
260518 20:11:01[LessonRuntime]-INFO-emit lesson_step stepId=s1 stepType=listen backgroundScene=1 teachingObject=1 robotOverlay=1 prompt=1
260518 20:11:02[lesson_handler]-INFO-lesson_step poster fetched+drawn from URL stepId=s1
260518 20:11:03[lesson_handler]-INFO-lesson_step teaching object fetched+drawn from URL stepId=s1
260518 20:11:04[lesson_handler]-INFO-lesson_step robot overlay fetched+drawn from URL stepId=s1
260518 20:11:05[lesson_handler]-INFO-lesson_step rendered stepId=s1 passive=0 degraded=0
260518 20:11:06[GoogleLive]-INFO-Google Live lesson_step_prompt queued via tts stepId=s1 text='Can you say barn with TeeBot?'
260518 20:11:07[LessonRuntime]-INFO-child response window opened stepId=s1 listening=true
260518 20:11:08[LessonRuntime]-INFO-interactive child response accepted stepId=s1 recognizedText=barn
260518 20:11:09[LessonRuntime]-INFO-lesson_step_prompt queued via tts stepId=s1 text='Con phát âm chưa chuẩn, mình nói lại nhé.'
260518 20:11:10[LessonRuntime]-INFO-lesson_progress event=step_completed result=success stepId=s1
260518 20:11:59[LessonRuntime]-INFO-emit lesson_stop reason=COMPLETED
260518 20:12:00[LessonRuntime]-INFO-lesson_completed stepsCompleted=1
""" + "\n".join(
            "260518 20:10:{:02d}[GoogleLive]-INFO-Google Live user_interrupted reason=audio_input cancelled_response_id={} next_response_id={}".format(i, i, i + 1)
            for i in range(10)
        )

        result = audit.audit_log(
            log_text,
            device_id="3c:0f:02:de:c2:e0",
            client_id="d16afa54-eb44-4fcb-8cac-cdefdf05f6fc",
            min_interrupts=10,
            require_lesson=True,
            expected_lesson_steps=1,
            expected_interactive_steps=1,
        )

        self.assertFalse(result["passed"])
        self.assertEqual(result["immediate_pronunciation_scoring"], 1)
        self.assertIn("no_immediate_pronunciation_scoring", result["missing"])

    def test_audit_rejects_lesson_image_decode_or_fetch_failures_even_with_draw_lines(self):
        audit = importlib.import_module("scripts.physical_smoke_audit")
        log_text = """
260518 20:10:00[core.connection]-INFO-192.168.0.50 conn - Headers: {'device-id': '3c:0f:02:de:c2:e0', 'client-id': 'd16afa54-eb44-4fcb-8cac-cdefdf05f6fc', 'user-agent': 'TBOT/2.2.7'}
260518 20:10:01[GoogleLive]-INFO-Google Live input_audio_diag encoded_bytes=80 decoded_bytes=640 rms=921 source_rate=16000 target_rate=16000 sample_width=2
260518 20:10:02[GoogleLive]-INFO-Google Live transcript source=user chars=14 text='bắt đầu bài học'
260518 20:10:03[GoogleLive]-INFO-Google Live lesson_start_intent tool=start_lesson text_preview='bắt đầu bài học'
260518 20:10:04[LessonRuntime]-INFO-emit lesson_prepare assignmentId=assignment-1 lessonId=w01-d01-barn-say-it-age3-20260617
260518 20:10:05[LessonRuntime]-INFO-emit lesson_start
260518 20:11:01[LessonRuntime]-INFO-emit lesson_step stepId=s1 stepType=listen backgroundScene=1 teachingObject=1 robotOverlay=1 prompt=1
260518 20:11:02[Lesson]-WARN-backgroundScene.poster: JPEG decode failed: 257
260518 20:11:03[Lesson]-WARN-lesson_step poster fetch failed; caption-only fallback
260518 20:11:04[lesson_handler]-INFO-lesson_step poster fetched+drawn from URL stepId=s1
260518 20:11:05[lesson_handler]-INFO-lesson_step teaching object fetched+drawn from URL stepId=s1
260518 20:11:06[lesson_handler]-INFO-lesson_step robot overlay fetched+drawn from URL stepId=s1
260518 20:11:07[lesson_handler]-INFO-lesson_step rendered stepId=s1 passive=0 degraded=0
260518 20:11:08[GoogleLive]-INFO-Google Live lesson_step_prompt queued via tts stepId=s1 text='Can you say barn with TeeBot?'
260518 20:11:09[LessonRuntime]-INFO-child response window opened stepId=s1 listening=true
260518 20:11:10[LessonRuntime]-INFO-interactive child response accepted stepId=s1 recognizedText=barn
260518 20:11:11[LessonRuntime]-INFO-lesson_progress event=step_completed result=success stepId=s1
260518 20:11:59[LessonRuntime]-INFO-emit lesson_stop reason=COMPLETED
260518 20:12:00[LessonRuntime]-INFO-lesson_completed stepsCompleted=1
""" + "\n".join(
            "260518 20:10:{:02d}[GoogleLive]-INFO-Google Live user_interrupted reason=audio_input cancelled_response_id={} next_response_id={}".format(i, i, i + 1)
            for i in range(10)
        )

        result = audit.audit_log(
            log_text,
            device_id="3c:0f:02:de:c2:e0",
            client_id="d16afa54-eb44-4fcb-8cac-cdefdf05f6fc",
            min_interrupts=10,
            require_lesson=True,
            expected_lesson_steps=1,
            expected_interactive_steps=1,
        )

        self.assertFalse(result["passed"])
        self.assertIn("no_fatal_patterns", result["missing"])

    def test_audit_rejects_child_response_without_observable_input(self):
        audit = importlib.import_module("scripts.physical_smoke_audit")
        log_text = """
260518 20:10:00[core.connection]-INFO-192.168.0.50 conn - Headers: {'device-id': '3c:0f:02:de:c2:e0', 'client-id': 'd16afa54-eb44-4fcb-8cac-cdefdf05f6fc', 'user-agent': 'TBOT/2.2.7'}
260518 20:10:01[GoogleLive]-INFO-Google Live input_audio_diag encoded_bytes=80 decoded_bytes=640 rms=921 source_rate=16000 target_rate=16000 sample_width=2
260518 20:10:02[GoogleLive]-INFO-Google Live transcript source=user chars=14 text='bắt đầu bài học'
260518 20:10:03[GoogleLive]-INFO-Google Live lesson_start_intent tool=start_lesson text_preview='bắt đầu bài học'
260518 20:10:04[LessonRuntime]-INFO-emit lesson_prepare assignmentId=assignment-1 lessonId=w01-d01-barn-say-it-age3-20260617
260518 20:10:05[LessonRuntime]-INFO-emit lesson_start
260518 20:11:01[LessonRuntime]-INFO-emit lesson_step stepId=s1 stepType=listen backgroundScene=1 teachingObject=1 robotOverlay=1 prompt=1
260518 20:11:02[lesson_handler]-INFO-lesson_step poster fetched+drawn from URL stepId=s1
260518 20:11:03[lesson_handler]-INFO-lesson_step teaching object fetched+drawn from URL stepId=s1
260518 20:11:04[lesson_handler]-INFO-lesson_step robot overlay fetched+drawn from URL stepId=s1
260518 20:11:05[lesson_handler]-INFO-lesson_step rendered stepId=s1 passive=0 degraded=0
260518 20:11:06[GoogleLive]-INFO-Google Live lesson_step_prompt queued via tts stepId=s1 text='Can you say barn with TeeBot?'
260518 20:11:07[LessonRuntime]-INFO-child response window opened stepId=s1 listening=true
260518 20:11:08[LessonRuntime]-INFO-interactive child response accepted stepId=s1
260518 20:11:09[LessonRuntime]-INFO-lesson_progress event=step_completed result=success stepId=s1
260518 20:11:59[LessonRuntime]-INFO-emit lesson_stop reason=COMPLETED
260518 20:12:00[LessonRuntime]-INFO-lesson_completed stepsCompleted=1
""" + "\n".join(
            "260518 20:10:{:02d}[GoogleLive]-INFO-Google Live user_interrupted reason=audio_input cancelled_response_id={} next_response_id={}".format(i, i, i + 1)
            for i in range(10)
        )

        result = audit.audit_log(
            log_text,
            device_id="3c:0f:02:de:c2:e0",
            client_id="d16afa54-eb44-4fcb-8cac-cdefdf05f6fc",
            min_interrupts=10,
            require_lesson=True,
            expected_lesson_steps=1,
            expected_interactive_steps=1,
        )

        self.assertFalse(result["passed"])
        self.assertEqual(result["interactive_child_responses"], 1)
        self.assertEqual(result["interactive_child_responses_observed"], 0)
        self.assertIn("interactive_child_responses_observed>=1", result["missing"])

    def test_audit_rejects_contradictory_child_response_evidence(self):
        audit = importlib.import_module("scripts.physical_smoke_audit")
        log_text = """
260518 20:10:00[core.connection]-INFO-192.168.0.50 conn - Headers: {'device-id': '3c:0f:02:de:c2:e0', 'client-id': 'd16afa54-eb44-4fcb-8cac-cdefdf05f6fc', 'user-agent': 'TBOT/2.2.7'}
260518 20:10:01[GoogleLive]-INFO-Google Live input_audio_diag encoded_bytes=80 decoded_bytes=640 rms=921 source_rate=16000 target_rate=16000 sample_width=2
260518 20:10:02[GoogleLive]-INFO-Google Live transcript source=user chars=14 text='bắt đầu bài học'
260518 20:10:03[GoogleLive]-INFO-Google Live lesson_start_intent tool=start_lesson text_preview='bắt đầu bài học'
260518 20:10:04[LessonRuntime]-INFO-emit lesson_prepare assignmentId=assignment-1 lessonId=w01-d01-barn-say-it-age3-20260617
260518 20:10:05[LessonRuntime]-INFO-emit lesson_start
260518 20:11:01[LessonRuntime]-INFO-emit lesson_step stepId=s1 stepType=listen backgroundScene=1 teachingObject=1 robotOverlay=1 prompt=1
260518 20:11:02[lesson_handler]-INFO-lesson_step poster fetched+drawn from URL stepId=s1
260518 20:11:03[lesson_handler]-INFO-lesson_step teaching object fetched+drawn from URL stepId=s1
260518 20:11:04[lesson_handler]-INFO-lesson_step robot overlay fetched+drawn from URL stepId=s1
260518 20:11:05[lesson_handler]-INFO-lesson_step rendered stepId=s1 passive=0 degraded=0
260518 20:11:06[GoogleLive]-INFO-Google Live lesson_step_prompt queued via tts stepId=s1 text='Can you say barn with TeeBot?'
260518 20:11:07[LessonRuntime]-INFO-child response window opened stepId=s1 listening=true
260518 20:11:08[LessonRuntime]-INFO-interactive child response accepted stepId=s1 recognizedText=barn accepted=false confidence=0
260518 20:11:09[LessonRuntime]-INFO-lesson_progress event=step_completed result=success stepId=s1
260518 20:11:59[LessonRuntime]-INFO-emit lesson_stop reason=COMPLETED
260518 20:12:00[LessonRuntime]-INFO-lesson_completed stepsCompleted=1
""" + "\n".join(
            "260518 20:10:{:02d}[GoogleLive]-INFO-Google Live user_interrupted reason=audio_input cancelled_response_id={} next_response_id={}".format(i, i, i + 1)
            for i in range(10)
        )

        result = audit.audit_log(
            log_text,
            device_id="3c:0f:02:de:c2:e0",
            client_id="d16afa54-eb44-4fcb-8cac-cdefdf05f6fc",
            min_interrupts=10,
            require_lesson=True,
            expected_lesson_steps=1,
            expected_interactive_steps=1,
        )

        self.assertFalse(result["passed"])
        self.assertEqual(result["interactive_child_responses"], 1)
        self.assertEqual(result["interactive_child_responses_observed"], 0)
        self.assertIn("interactive_child_responses_observed>=1", result["missing"])

    def test_audit_rejects_child_response_on_passive_step(self):
        audit = importlib.import_module("scripts.physical_smoke_audit")
        log_text = """
260518 20:10:00[core.connection]-INFO-192.168.0.50 conn - Headers: {'device-id': '3c:0f:02:de:c2:e0', 'client-id': 'd16afa54-eb44-4fcb-8cac-cdefdf05f6fc', 'user-agent': 'TBOT/2.2.7'}
260518 20:10:01[GoogleLive]-INFO-Google Live input_audio_diag encoded_bytes=80 decoded_bytes=640 rms=921 source_rate=16000 target_rate=16000 sample_width=2
260518 20:10:02[GoogleLive]-INFO-Google Live transcript source=user chars=14 text='bắt đầu bài học'
260518 20:10:03[GoogleLive]-INFO-Google Live lesson_start_intent tool=start_lesson text_preview='bắt đầu bài học'
260518 20:10:04[LessonRuntime]-INFO-emit lesson_prepare assignmentId=assignment-1 lessonId=w01-d01-barn-say-it-age3-20260617
260518 20:10:05[LessonRuntime]-INFO-emit lesson_start
260518 20:11:01[LessonRuntime]-INFO-emit lesson_step stepId=s1 stepType=greeting backgroundScene=1 teachingObject=1 robotOverlay=1 prompt=1
260518 20:11:02[lesson_handler]-INFO-lesson_step poster fetched+drawn from URL stepId=s1
260518 20:11:03[lesson_handler]-INFO-lesson_step teaching object fetched+drawn from URL stepId=s1
260518 20:11:04[lesson_handler]-INFO-lesson_step robot overlay fetched+drawn from URL stepId=s1
260518 20:11:05[lesson_handler]-INFO-lesson_step rendered stepId=s1 passive=1 degraded=0
260518 20:11:06[GoogleLive]-INFO-Google Live lesson_step_prompt queued via tts stepId=s1 text='hello'
260518 20:11:07[LessonRuntime]-INFO-child response window opened stepId=s1 listening=true
260518 20:11:08[LessonRuntime]-INFO-interactive child response accepted stepId=s1 recognizedText=hello
260518 20:11:09[LessonRuntime]-INFO-lesson_progress event=step_completed result=success stepId=s1
260518 20:12:01[LessonRuntime]-INFO-emit lesson_step stepId=s2 stepType=listen backgroundScene=1 teachingObject=1 robotOverlay=1 prompt=1
260518 20:12:02[lesson_handler]-INFO-lesson_step poster fetched+drawn from URL stepId=s2
260518 20:12:03[lesson_handler]-INFO-lesson_step teaching object fetched+drawn from URL stepId=s2
260518 20:12:04[lesson_handler]-INFO-lesson_step robot overlay fetched+drawn from URL stepId=s2
260518 20:12:05[lesson_handler]-INFO-lesson_step rendered stepId=s2 passive=0 degraded=0
260518 20:12:06[GoogleLive]-INFO-Google Live lesson_step_prompt queued via tts stepId=s2 text='Can you say barn with TeeBot?'
260518 20:12:07[LessonRuntime]-INFO-child response window opened stepId=s2 listening=true
260518 20:12:08[LessonRuntime]-INFO-interactive child response accepted stepId=s2 recognizedText=barn
260518 20:12:09[LessonRuntime]-INFO-lesson_progress event=step_completed result=success stepId=s2
260518 20:12:59[LessonRuntime]-INFO-emit lesson_stop reason=COMPLETED
260518 20:13:00[LessonRuntime]-INFO-lesson_completed stepsCompleted=2
""" + "\n".join(
            "260518 20:10:{:02d}[GoogleLive]-INFO-Google Live user_interrupted reason=audio_input cancelled_response_id={} next_response_id={}".format(i, i, i + 1)
            for i in range(10)
        )

        result = audit.audit_log(
            log_text,
            device_id="3c:0f:02:de:c2:e0",
            client_id="d16afa54-eb44-4fcb-8cac-cdefdf05f6fc",
            min_interrupts=10,
            require_lesson=True,
            expected_lesson_steps=2,
            expected_interactive_steps=1,
        )

        self.assertFalse(result["passed"])
        self.assertEqual(result["passive_child_response_activity"], 2)
        self.assertIn("no_passive_child_response_activity", result["missing"])

    def test_audit_rejects_layer_draws_not_tied_to_each_step_id(self):
        audit = importlib.import_module("scripts.physical_smoke_audit")
        log_text = """
260518 20:10:00[core.connection]-INFO-192.168.0.50 conn - Headers: {'device-id': '3c:0f:02:de:c2:e0', 'client-id': 'd16afa54-eb44-4fcb-8cac-cdefdf05f6fc', 'user-agent': 'TBOT/2.2.7'}
260518 20:10:01[GoogleLive]-INFO-Google Live input_audio_diag encoded_bytes=80 decoded_bytes=640 rms=921 source_rate=16000 target_rate=16000 sample_width=2
260518 20:10:02[GoogleLive]-INFO-Google Live transcript source=user chars=14 text='bắt đầu bài học'
260518 20:10:03[GoogleLive]-INFO-Google Live lesson_start_intent tool=start_lesson text_preview='bắt đầu bài học'
260518 20:10:04[LessonRuntime]-INFO-emit lesson_prepare assignmentId=assignment-1 lessonId=w01-d01-barn-say-it-age3-20260617
260518 20:10:05[LessonRuntime]-INFO-emit lesson_start
260518 20:11:01[LessonRuntime]-INFO-emit lesson_step stepId=s1 stepType=greeting backgroundScene=1 teachingObject=1 robotOverlay=1 prompt=1
260518 20:11:02[lesson_handler]-INFO-lesson_step poster fetched+drawn from URL stepId=s1
260518 20:11:03[lesson_handler]-INFO-lesson_step teaching object fetched+drawn from URL stepId=s1
260518 20:11:04[lesson_handler]-INFO-lesson_step robot overlay fetched+drawn from URL stepId=s1
260518 20:11:05[lesson_handler]-INFO-lesson_step rendered stepId=s1 passive=1 degraded=0
260518 20:11:06[GoogleLive]-INFO-Google Live lesson_step_prompt queued via tts stepId=s1 text='hello'
260518 20:11:09[LessonRuntime]-INFO-lesson_progress event=step_completed result=success stepId=s1
260518 20:12:01[LessonRuntime]-INFO-emit lesson_step stepId=s2 stepType=listen backgroundScene=1 teachingObject=1 robotOverlay=1 prompt=1
260518 20:12:02[lesson_handler]-INFO-lesson_step poster fetched+drawn from URL stepId=s1
260518 20:12:03[lesson_handler]-INFO-lesson_step teaching object fetched+drawn from URL stepId=s1
260518 20:12:04[lesson_handler]-INFO-lesson_step robot overlay fetched+drawn from URL stepId=s1
260518 20:12:05[lesson_handler]-INFO-lesson_step rendered stepId=s2 passive=0 degraded=0
260518 20:12:06[GoogleLive]-INFO-Google Live lesson_step_prompt queued via tts stepId=s2 text='Can you say barn with TeeBot?'
260518 20:12:07[LessonRuntime]-INFO-child response window opened stepId=s2 listening=true
260518 20:12:08[LessonRuntime]-INFO-interactive child response accepted stepId=s2 recognizedText=barn
260518 20:12:09[LessonRuntime]-INFO-lesson_progress event=step_completed result=success stepId=s2
260518 20:12:59[LessonRuntime]-INFO-emit lesson_stop reason=COMPLETED
260518 20:13:00[LessonRuntime]-INFO-lesson_completed stepsCompleted=2
""" + "\n".join(
            "260518 20:10:{:02d}[GoogleLive]-INFO-Google Live user_interrupted reason=audio_input cancelled_response_id={} next_response_id={}".format(i, i, i + 1)
            for i in range(10)
        )

        result = audit.audit_log(
            log_text,
            device_id="3c:0f:02:de:c2:e0",
            client_id="d16afa54-eb44-4fcb-8cac-cdefdf05f6fc",
            min_interrupts=10,
            require_lesson=True,
            expected_lesson_steps=2,
            expected_interactive_steps=1,
        )

        self.assertFalse(result["passed"])
        self.assertEqual(result["lesson_step_layers_drawn_by_step"], 1)
        self.assertIn("lesson_step_layers_drawn_by_step", result["missing"])

    def test_audit_rejects_lesson_prompt_before_rendered_ack(self):
        audit = importlib.import_module("scripts.physical_smoke_audit")
        log_text = """
260518 20:10:00[core.connection]-INFO-192.168.0.50 conn - Headers: {'device-id': '3c:0f:02:de:c2:e0', 'client-id': 'd16afa54-eb44-4fcb-8cac-cdefdf05f6fc', 'user-agent': 'TBOT/2.2.7'}
260518 20:10:01[GoogleLive]-INFO-Google Live input_audio_diag encoded_bytes=80 decoded_bytes=640 rms=921 source_rate=16000 target_rate=16000 sample_width=2
260518 20:10:02[GoogleLive]-INFO-Google Live transcript source=user chars=14 text='bắt đầu bài học'
260518 20:10:03[GoogleLive]-INFO-Google Live lesson_start_intent tool=start_lesson text_preview='bắt đầu bài học'
260518 20:10:04[LessonRuntime]-INFO-emit lesson_prepare assignmentId=assignment-1 lessonId=w01-d01-barn-say-it-age3-20260617
260518 20:10:05[LessonRuntime]-INFO-emit lesson_start
260518 20:11:01[LessonRuntime]-INFO-emit lesson_step stepId=s1 stepType=greeting backgroundScene=1 teachingObject=1 robotOverlay=1 prompt=1
260518 20:11:02[GoogleLive]-INFO-Google Live lesson_step_prompt queued via tts stepId=s1 text='step 1'
260518 20:11:03[lesson_handler]-INFO-lesson_step poster fetched+drawn from URL stepId=s1
260518 20:11:04[lesson_handler]-INFO-lesson_step teaching object fetched+drawn from URL stepId=s1
260518 20:11:05[lesson_handler]-INFO-lesson_step rendered stepId=s1 passive=1 degraded=0
260518 20:11:59[LessonRuntime]-INFO-emit lesson_stop reason=COMPLETED
260518 20:12:00[LessonRuntime]-INFO-lesson_completed stepsCompleted=1
""" + "\n".join(
            "260518 20:10:{:02d}[GoogleLive]-INFO-Google Live user_interrupted reason=audio_input cancelled_response_id={} next_response_id={}".format(i, i, i + 1)
            for i in range(10)
        )

        result = audit.audit_log(
            log_text,
            device_id="3c:0f:02:de:c2:e0",
            client_id="d16afa54-eb44-4fcb-8cac-cdefdf05f6fc",
            min_interrupts=10,
            require_lesson=True,
            expected_lesson_steps=1,
        )

        self.assertFalse(result["passed"])
        self.assertIn("lesson_prompt_after_render", result["missing"])

    def test_audit_rejects_lesson_flow_missing_robot_overlay_draw(self):
        audit = importlib.import_module("scripts.physical_smoke_audit")
        log_text = """
260518 20:10:00[core.connection]-INFO-192.168.0.50 conn - Headers: {'device-id': '3c:0f:02:de:c2:e0', 'client-id': 'd16afa54-eb44-4fcb-8cac-cdefdf05f6fc', 'user-agent': 'TBOT/2.2.7'}
260518 20:10:01[GoogleLive]-INFO-Google Live input_audio_diag encoded_bytes=80 decoded_bytes=640 rms=921 source_rate=16000 target_rate=16000 sample_width=2
260518 20:10:02[GoogleLive]-INFO-Google Live transcript source=user chars=14 text='bắt đầu bài học'
260518 20:10:03[GoogleLive]-INFO-Google Live lesson_start_intent tool=start_lesson text_preview='bắt đầu bài học'
260518 20:10:04[LessonRuntime]-INFO-emit lesson_prepare assignmentId=assignment-1 lessonId=w01-d01-barn-say-it-age3-20260617
260518 20:10:05[LessonRuntime]-INFO-emit lesson_start
260518 20:11:01[LessonRuntime]-INFO-emit lesson_step stepId=s1 stepType=greeting backgroundScene=1 teachingObject=1 robotOverlay=1 prompt=1
260518 20:11:02[lesson_handler]-INFO-lesson_step poster fetched+drawn from URL stepId=s1
260518 20:11:03[lesson_handler]-INFO-lesson_step teaching object fetched+drawn from URL stepId=s1
260518 20:11:04[lesson_handler]-INFO-lesson_step rendered stepId=s1 passive=1 degraded=0
260518 20:11:05[GoogleLive]-INFO-Google Live lesson_step_prompt queued via tts stepId=s1 text='step 1'
260518 20:11:59[LessonRuntime]-INFO-emit lesson_stop reason=COMPLETED
260518 20:12:00[LessonRuntime]-INFO-lesson_completed stepsCompleted=1
""" + "\n".join(
            "260518 20:10:{:02d}[GoogleLive]-INFO-Google Live user_interrupted reason=audio_input cancelled_response_id={} next_response_id={}".format(i, i, i + 1)
            for i in range(10)
        )

        result = audit.audit_log(
            log_text,
            device_id="3c:0f:02:de:c2:e0",
            client_id="d16afa54-eb44-4fcb-8cac-cdefdf05f6fc",
            min_interrupts=10,
            require_lesson=True,
            expected_lesson_steps=1,
        )

        self.assertFalse(result["passed"])
        self.assertIn("lesson_robot_overlays_drawn>=1", result["missing"])

    def test_audit_rejects_lesson_flow_with_only_start_ack_tts_no_step_prompt(self):
        audit = importlib.import_module("scripts.physical_smoke_audit")
        log_text = """
260518 20:10:00[core.connection]-INFO-192.168.0.50 conn - Headers: {'device-id': '3c:0f:02:de:c2:e0', 'client-id': 'd16afa54-eb44-4fcb-8cac-cdefdf05f6fc', 'user-agent': 'TBOT/2.2.7'}
260518 20:10:01[GoogleLive]-INFO-Google Live input_audio_diag encoded_bytes=80 decoded_bytes=640 rms=921 source_rate=16000 target_rate=16000 sample_width=2
260518 20:10:02[GoogleLive]-INFO-Google Live transcript source=user chars=14 text='bắt đầu bài học'
260518 20:10:03[GoogleLive]-INFO-Google Live lesson_start_intent tool=start_lesson text_preview='bắt đầu bài học'
260518 20:10:04[LessonRuntime]-INFO-emit lesson_prepare assignmentId=assignment-1 lessonId=w01-d01-barn-say-it-age3-20260617
260518 20:10:05[LessonRuntime]-INFO-emit lesson_start
260518 20:10:06[GoogleLive]-INFO-Google Live lesson_start_ack queued via tts text='Bắt đầu bài học nhé.'
260518 20:11:01[LessonRuntime]-INFO-emit lesson_step stepId=s1 stepType=greeting backgroundScene=1 teachingObject=1 robotOverlay=1 prompt=1
260518 20:11:02[lesson_handler]-INFO-lesson_step poster fetched+drawn from URL stepId=s1
260518 20:11:03[lesson_handler]-INFO-lesson_step teaching object fetched+drawn from URL stepId=s1
260518 20:11:04[lesson_handler]-INFO-lesson_step robot overlay fetched+drawn from URL stepId=s1
260518 20:11:05[lesson_handler]-INFO-lesson_step rendered stepId=s1 passive=1 degraded=0
260518 20:11:59[LessonRuntime]-INFO-emit lesson_stop reason=COMPLETED
260518 20:12:00[LessonRuntime]-INFO-lesson_completed stepsCompleted=1
""" + "\n".join(
            "260518 20:10:{:02d}[GoogleLive]-INFO-Google Live user_interrupted reason=audio_input cancelled_response_id={} next_response_id={}".format(i, i, i + 1)
            for i in range(10)
        )

        result = audit.audit_log(
            log_text,
            device_id="3c:0f:02:de:c2:e0",
            client_id="d16afa54-eb44-4fcb-8cac-cdefdf05f6fc",
            min_interrupts=10,
            require_lesson=True,
            expected_lesson_steps=1,
        )

        self.assertFalse(result["passed"])
        self.assertEqual(result["lesson_prompt_tts"], 0)
        self.assertIn("lesson_prompt_tts>=1", result["missing"])

    def test_audit_rejects_lesson_flow_missing_layers_or_steps(self):
        audit = importlib.import_module("scripts.physical_smoke_audit")
        log_text = """
260518 20:10:00[core.connection]-INFO-192.168.0.50 conn - Headers: {'device-id': '3c:0f:02:de:c2:e0', 'client-id': 'd16afa54-eb44-4fcb-8cac-cdefdf05f6fc', 'user-agent': 'TBOT/2.2.7'}
260518 20:10:01[GoogleLive]-INFO-Google Live input_audio_diag encoded_bytes=80 decoded_bytes=640 rms=921 source_rate=16000 target_rate=16000 sample_width=2
260518 20:10:02[GoogleLive]-INFO-Google Live transcript source=user chars=14 text='bắt đầu bài học'
260518 20:10:03[GoogleLive]-INFO-Google Live lesson_start_intent tool=start_lesson text_preview='bắt đầu bài học'
260518 20:10:04[LessonRuntime]-INFO-emit lesson_prepare assignmentId=assignment-1 lessonId=w01-d01-barn-say-it-age3-20260617
260518 20:10:05[LessonRuntime]-INFO-emit lesson_start
260518 20:11:01[LessonRuntime]-INFO-emit lesson_step stepId=s1 stepType=greeting backgroundScene=1 teachingObject=0 robotOverlay=1 prompt=1
260518 20:11:01[GoogleLive]-INFO-Google Live lesson_step_prompt queued via tts text='step 1'
260518 20:11:01[lesson_handler]-INFO-lesson_step rendered stepId=s1 passive=1 degraded=1
260518 20:11:59[LessonRuntime]-INFO-emit lesson_stop reason=COMPLETED
""" + "\n".join(
            "260518 20:10:{:02d}[GoogleLive]-INFO-Google Live user_interrupted reason=audio_input cancelled_response_id={} next_response_id={}".format(i, i, i + 1)
            for i in range(10)
        )

        result = audit.audit_log(
            log_text,
            device_id="3c:0f:02:de:c2:e0",
            client_id="d16afa54-eb44-4fcb-8cac-cdefdf05f6fc",
            min_interrupts=10,
            require_lesson=True,
            expected_lesson_steps=9,
        )

        self.assertFalse(result["passed"])
        self.assertIn("lesson_steps>=9", result["missing"])
        self.assertIn("lesson_step_layers_complete", result["missing"])
        self.assertIn("lesson_firmware_rendered>=9", result["missing"])

if __name__ == "__main__":
    unittest.main()
