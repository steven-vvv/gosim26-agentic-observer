"""Minimal protocol example. Waiting is legal but is not a competitive strategy."""
import json
import sys


def main():
    for line in sys.stdin:
        message = json.loads(line)
        if message["message_type"] == "initialize":
            continue
        if message["message_type"] != "decision_request":
            raise ValueError("Unexpected protocol message")
        print(json.dumps({
            "protocol_version": message["protocol_version"],
            "message_type": "decision_response",
            "decision_sequence": message["decision_sequence"],
            "action": "wait",
        }), flush=True)


if __name__ == "__main__":
    main()
