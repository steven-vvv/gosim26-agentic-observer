from __future__ import annotations

import json
import sys
import tempfile
import time
import unittest
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "agents" / "observer"))

from telemetry import TelemetryReporter, runtime_payload


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

    def test_packaged_defaults_use_worker_without_logging_the_token(self):
        with tempfile.NamedTemporaryFile("w", encoding="utf-8") as handle:
            json.dump({"enabled": True, "transport": "openai",
                       "url": "https://worker.example/v1", "model": "telemetry-ack"}, handle)
            handle.flush()
            with patch.dict("os.environ", {"OPENAI_API_KEY": "ingest-secret"}, clear=True):
                reporter = TelemetryReporter.from_env(handle.name)
        self.assertTrue(reporter.enabled)
        self.assertEqual(reporter.url, "https://worker.example/v1/chat/completions")
        status = reporter.status()
        self.assertTrue(status["configured"])
        self.assertTrue(status["token_present"])
        self.assertNotIn("ingest-secret", json.dumps(status))
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
        self.assertEqual(body["messages"][0]["role"], "user")
        self.assertEqual(body["telemetry"]["event"]["event_type"], "runtime")
        self.assertNotIn("secret", request.data.decode())

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
