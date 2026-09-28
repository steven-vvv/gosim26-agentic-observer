from __future__ import annotations

import json
import sys
import tempfile
import threading
import time
import unittest
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "agents" / "observer"))

from telemetry import RunSummary, TelemetryReporter, TerminalStep, runtime_payload


class TelemetryClientTests(unittest.TestCase):
    def test_disabled_client_does_not_start_a_thread_or_send(self):
        reporter = TelemetryReporter(None, None)
        self.assertFalse(reporter.enabled)
        self.assertFalse(reporter.emit("runtime", {"python": "test"}))
        self.assertIsNone(reporter._thread)
        reporter.close()

    def test_endpoint_normalization_and_sanitization(self):
        reporter = TelemetryReporter("https://example.test/v1", "token", queue_size=2)
        with patch("telemetry.urllib.request.urlopen") as urlopen:
            class Response:
                status = 202
                def __enter__(self): return self
                def __exit__(self, *_): return False
                def read(self, _): return b"{}"
            urlopen.return_value = Response()
            self.assertTrue(reporter.emit("runtime", {"api_key": "secret", "ok": 1}))
            deadline = time.time() + 1.0
            while reporter.stats()["sent"] < 1 and time.time() < deadline:
                time.sleep(0.01)
        reporter.close()
        self.assertEqual(reporter.url, "https://example.test/v1/telemetry")
        self.assertEqual(reporter.stats()["sent"], 1)
        request = urlopen.call_args.args[0]
        self.assertNotIn("secret", request.data.decode())

    def test_close_drains_queued_events_before_final_report(self):
        class Response:
            status = 200

            def __enter__(self): return self
            def __exit__(self, *_): return False

        with patch("telemetry.urllib.request.urlopen", return_value=Response()):
            reporter = TelemetryReporter("https://example.test/v1", "token", transport="openai")
            for sequence in range(4):
                self.assertTrue(reporter.emit("decision_trace", {"sequence": sequence}, sequence=sequence))
            reporter.close(timeout=1.0)
        self.assertFalse(reporter.status()["thread_alive"])
        self.assertEqual(reporter.stats()["queued"], 4)
        self.assertEqual(reporter.stats()["sent"], 4)

    def test_openai_environment_is_a_telemetry_transport(self):
        with patch.dict(
            "os.environ",
            {
                "OPENAI_BASE_URL": "https://telemetry.example/v1",
                "OPENAI_API_KEY": "ingest-token",
                "MODEL_NAME": "telemetry-ack",
            },
            clear=True,
        ):
            reporter = TelemetryReporter.from_env()
        self.assertTrue(reporter.enabled)
        self.assertEqual(reporter.transport, "openai")
        self.assertEqual(reporter.url, "https://telemetry.example/v1/chat/completions")
        self.assertEqual(reporter.model, "telemetry-ack")
        reporter.close()

    def test_model_key_alone_does_not_authorize_packaged_worker(self):
        with tempfile.NamedTemporaryFile("w", encoding="utf-8") as handle:
            json.dump({"enabled": True, "transport": "openai",
                       "url": "https://worker.example/v1", "model": "telemetry-ack"}, handle)
            handle.flush()
            with patch.dict("os.environ", {"OPENAI_API_KEY": "model-secret"}, clear=True):
                reporter = TelemetryReporter.from_env(handle.name)
        self.assertFalse(reporter.enabled)
        reporter.close()

    def test_packaged_defaults_do_not_send_traces_to_model_proxy(self):
        with tempfile.NamedTemporaryFile("w", encoding="utf-8") as handle:
            json.dump({"enabled": True, "transport": "openai",
                       "url": "https://worker.example/v1", "model": "telemetry-ack"}, handle)
            handle.flush()
            with patch.dict("os.environ", {"OPENAI_BASE_URL": "https://platform-model.example/v1",
                                           "OPENAI_API_KEY": "model-proxy-token"}, clear=True):
                reporter = TelemetryReporter.from_env(handle.name)
        self.assertFalse(reporter.enabled)
        reporter.close()

    def test_dedicated_token_uses_packaged_default_url(self):
        with tempfile.NamedTemporaryFile("w", encoding="utf-8") as handle:
            json.dump({"enabled": True, "transport": "openai",
                       "url": "https://worker.example/v1", "model": "telemetry-ack"}, handle)
            handle.flush()
            with patch.dict("os.environ", {"TELEMETRY_TOKEN": "telemetry-secret"}, clear=True):
                reporter = TelemetryReporter.from_env(handle.name)
        self.assertTrue(reporter.enabled)
        self.assertEqual(reporter.url, "https://worker.example/v1/chat/completions")
        self.assertEqual(reporter.token, "telemetry-secret")
        reporter.close()

    def test_dedicated_token_takes_precedence_over_model_environment(self):
        with tempfile.NamedTemporaryFile("w", encoding="utf-8") as handle:
            json.dump({"enabled": True, "transport": "openai",
                       "url": "https://worker.example/v1", "model": "telemetry-ack"}, handle)
            handle.flush()
            with patch.dict("os.environ", {"TELEMETRY_TOKEN": "telemetry-secret",
                                           "OPENAI_BASE_URL": "https://worker.example/v1",
                                           "OPENAI_API_KEY": "model-secret"}, clear=True):
                reporter = TelemetryReporter.from_env(handle.name)
        self.assertTrue(reporter.enabled)
        self.assertEqual(reporter.url, "https://worker.example/v1/chat/completions")
        self.assertEqual(reporter.token, "telemetry-secret")
        reporter.close()

    def test_status_redacts_endpoint_query_and_reports_queue_stats(self):
        reporter = TelemetryReporter("https://example.test/v1?token=secret", "token", retries=0)
        status = reporter.status()
        self.assertEqual(status["endpoint"], "https://example.test/v1/telemetry")
        self.assertNotIn("secret", json.dumps(status))
        self.assertEqual(status["stats"], {"queued": 0, "sent": 0, "failed": 0, "dropped": 0})
        reporter.close()

    def test_openai_transport_wraps_event_in_messages(self):
        reporter = TelemetryReporter(
            "https://example.test/v1",
            "token",
            transport="openai",
            model="telemetry-ack",
            retries=0,
        )
        with patch("telemetry.urllib.request.urlopen") as urlopen:
            class Response:
                status = 200
                def __enter__(self): return self
                def __exit__(self, *_): return False
                def read(self, _=-1): return b'{}'
            urlopen.return_value = Response()
            self.assertTrue(reporter.emit("runtime", {"api_key": "secret", "ok": 1}))
            deadline = time.time() + 1.0
            while reporter.stats()["sent"] < 1 and time.time() < deadline:
                time.sleep(0.01)
        reporter.close()
        request = urlopen.call_args.args[0]
        body = json.loads(request.data.decode())
        self.assertEqual(request.full_url, "https://example.test/v1/chat/completions")
        self.assertEqual(body["model"], "telemetry-ack")
        self.assertEqual(body["messages"][0]["role"], "tool")
        self.assertEqual(body["messages"][0]["tool_call_id"], "gosim26.telemetry")
        self.assertEqual(body["messages"][0]["name"], "gosim26.telemetry")
        self.assertEqual(set(body), {"model", "messages", "stream"})
        envelope = json.loads(body["messages"][0]["content"])
        self.assertEqual(envelope["schema"], "gosim26.telemetry.v1")
        self.assertEqual(envelope["event"]["event_type"], "runtime")
        self.assertNotIn("secret", request.data.decode())

    def test_openai_batches_multiple_traces_in_one_request(self):
        reporter = TelemetryReporter(None, None, transport="openai", batch_size=16)
        reporter.url = "https://example.test/v1/chat/completions"
        reporter.token = "token"
        for sequence in range(1, 4):
            self.assertTrue(reporter.emit("decision_trace", {"action": "wait"}, sequence=sequence))
        with patch("telemetry.urllib.request.urlopen") as urlopen:
            class Response:
                status = 200
                def __enter__(self): return self
                def __exit__(self, *_): return False
            urlopen.return_value = Response()
            reporter._thread = None
            reporter._stop.set()
            reporter._run()
        self.assertEqual(urlopen.call_count, 1)
        self.assertEqual(reporter.stats()["sent"], 3)
        envelope = json.loads(json.loads(urlopen.call_args.args[0].data)["messages"][0]["content"])
        self.assertEqual([event["sequence"] for event in envelope["events"]], [1, 2, 3])

    def test_final_report_bypasses_full_queue_and_fails_boundedly(self):
        reporter = TelemetryReporter(None, None, transport="openai", queue_size=1, timeout=0.1)
        reporter.url = "https://example.test/v1/chat/completions"
        reporter.token = "token"
        reporter._queue.put_nowait({"sentinel": True})
        with patch("telemetry.urllib.request.urlopen", side_effect=OSError("offline")):
            started = time.monotonic()
            self.assertFalse(reporter.send_final({"decisions": 10}, wait_seconds=0.3))
            self.assertLess(time.monotonic() - started, 0.3)
        self.assertEqual(reporter.stats()["dropped"], 1)

    def test_final_wait_has_a_hard_limit_when_network_hangs(self):
        reporter = TelemetryReporter(None, None, transport="openai", timeout=0.1)
        reporter.url = "https://example.test/v1/chat/completions"
        reporter.token = "token"
        release = threading.Event()
        with patch("telemetry.urllib.request.urlopen", side_effect=lambda *_args, **_kwargs: release.wait(1.0)):
            started = time.monotonic()
            self.assertFalse(reporter.send_final({"decisions": 10}, wait_seconds=0.05))
            self.assertLess(time.monotonic() - started, 0.2)
        release.set()

    def test_terminal_step_and_summary(self):
        terminal = TerminalStep()
        terminal.initialize({"calendar": {"last_night": "2026-10-07", "slot_duration_seconds": 900}})
        snap = {
            "cursor": {"night_id": "N20261007", "timestamp_utc": "2026-10-08T10:45:00Z", "slot_offset_seconds": 0},
            "night_start": {"night": {"observing_end_utc": "2026-10-08T11:00:00Z"}},
            "candidate_tiles": [{"tile_id": "T1", "nominal_exptime_seconds": 900}],
        }
        self.assertTrue(terminal.is_final(snap, {"action": "observe", "tile_id": "T1"}))
        self.assertFalse(terminal.is_final(snap, {"action": "invalid"}))
        summary = RunSummary()
        summary.add({"kind": "decision", "sequence": 1, "action": "observe", "policy_ms": 10.0})
        summary.add({"kind": "decision", "sequence": 2, "action": "wait", "policy_ms": 30.0})
        self.assertEqual(summary.report()["actions"], {"observe": 1, "wait": 1})
        self.assertEqual(summary.report()["metrics"]["policy_ms"]["p50"], 20.0)

    def test_queue_overflow_is_dropped_without_blocking(self):
        reporter = TelemetryReporter(None, None, queue_size=1)
        reporter.url = "https://example.test/v1/telemetry"
        reporter.token = "token"
        # Fill the queue directly so the worker's scheduling cannot make this
        # assertion race with a successful upload.
        reporter._queue.put_nowait({"sentinel": True})
        started = time.monotonic()
        self.assertFalse(reporter.emit("overflow", {"x": 2}))
        self.assertLess(time.monotonic() - started, 0.05)
        self.assertEqual(reporter.stats()["dropped"], 1)
        reporter.close()

    def test_invalid_timeout_uses_a_bounded_default(self):
        reporter = TelemetryReporter(None, None, timeout="invalid")
        self.assertEqual(reporter.timeout, 0.8)
        reporter.close()

    def test_upload_failure_is_bounded_and_dropped(self):
        reporter = TelemetryReporter("https://example.test", "token", retries=0, timeout=0.1)
        with patch("telemetry.urllib.request.urlopen", side_effect=OSError("offline")):
            self.assertTrue(reporter.emit("offline", {"ok": True}))
            deadline = time.time() + 1.0
            while reporter.stats()["dropped"] < 1 and time.time() < deadline:
                time.sleep(0.01)
        reporter.close(timeout=0.1)
        self.assertEqual(reporter.stats()["failed"], 1)
        self.assertEqual(reporter.stats()["dropped"], 1)

    def test_runtime_payload_has_no_environment_values(self):
        payload = runtime_payload()
        self.assertIn("python", payload)
        self.assertIn("machine", payload)
        self.assertIn("architecture", payload)
        self.assertIn("pointer_bits", payload)
        self.assertIn("cpu_affinity_count", payload)
        self.assertIn("resource_limits", payload)
        self.assertNotIn("TELEMETRY_TOKEN", str(payload))


if __name__ == "__main__":
    unittest.main()
