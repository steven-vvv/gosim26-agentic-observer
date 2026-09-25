import json
from pathlib import Path
import subprocess
import sys
import unittest


class ProtocolTest(unittest.TestCase):
    def test_initialization_emits_no_action_and_sequences_are_preserved(self):
        for version in ("participant-agent-protocol-v1", "participant-agent-protocol-v2"):
            messages = [{"message_type": "initialize", "protocol_version": version}]
            messages += [{"message_type": "decision_request", "protocol_version": version,
                          "decision_sequence": n, "payload": {}} for n in (1, 2)]
            root = Path(__file__).resolve().parents[1]
            result = subprocess.run([sys.executable, "-u", str(root / "src/agent.py")],
                input="".join(json.dumps(m) + "\n" for m in messages), capture_output=True,
                text=True, check=True, timeout=5)
            replies = [json.loads(line) for line in result.stdout.splitlines()]
            self.assertEqual([r["decision_sequence"] for r in replies], [1, 2])
            self.assertTrue(all(r["protocol_version"] == version and r["action"] == "wait" for r in replies))


if __name__ == "__main__":
    unittest.main()
