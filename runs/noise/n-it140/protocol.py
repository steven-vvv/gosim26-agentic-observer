"""Participant-side validation for the public JSON-Lines protocol."""

from __future__ import annotations

from typing import Mapping, Sequence


PROTOCOL_VERSION = "participant-agent-protocol-v2"
INITIAL_PUBLICATION_VERSION = "initial-publication-v2"
DECISION_SNAPSHOT_VERSION = "decision-snapshot-v3"

# Practice scenarios still speak the pre-anomaly contract; this agent accepts both.
ACCEPTED_PROTOCOL_VERSIONS = ("participant-agent-protocol-v1", PROTOCOL_VERSION)
ACCEPTED_SNAPSHOT_VERSIONS = ("decision-snapshot-v2", DECISION_SNAPSHOT_VERSION)


class ProtocolError(ValueError):
    """Raised when the platform sends an unsupported or malformed message."""


def parse_platform_message(message: Mapping[str, object]) -> tuple[str, dict]:
    """Validate an input envelope and return its message type and payload."""
    if message.get("protocol_version") not in ACCEPTED_PROTOCOL_VERSIONS:
        raise ProtocolError("unsupported participant protocol_version")
    message_type = str(message.get("message_type", ""))
    payload = message.get("payload")
    if not isinstance(payload, dict):
        raise ProtocolError("platform message payload must be an object")
    if message_type == "initialize":
        if payload.get("schema_version") != INITIAL_PUBLICATION_VERSION:
            raise ProtocolError("unsupported initial publication schema_version")
    elif message_type == "decision_request":
        if payload.get("schema_version") not in ACCEPTED_SNAPSHOT_VERSIONS:
            raise ProtocolError("unsupported decision snapshot schema_version")
        if int(message.get("decision_sequence", -1)) != int(
            payload.get("decision_sequence", -2)
        ):
            raise ProtocolError("decision sequence differs between envelope and payload")
    else:
        raise ProtocolError(f"unsupported platform message_type {message_type!r}")
    return message_type, payload


def decision_response(sequence: int, decision: Mapping[str, object], reports: Sequence[Mapping[str, object]] | None = None) -> dict[str, object]:
    """Wrap one validated local decision in the public response envelope.

    `reports` is an optional list of {"kind": "Instrument_Failure"} or
    {"kind": "NOVA" | "Reddening", "tile_id": ...} entries riding on this
    decision; reports never consume slot time.
    """
    envelope = {
        "protocol_version": PROTOCOL_VERSION,
        "message_type": "decision_response",
        "decision_sequence": int(sequence),
        "action": decision["action"],
        "tile_id": decision.get("tile_id", ""),
        "program": decision.get("program", ""),
        "request_id": decision.get("request_id", ""),
        "reason": decision.get("reason", ""),
        "decision_source": decision.get("decision_source", "deterministic"),
    }
    if reports:
        envelope["reports"] = [dict(entry) for entry in reports]
    return envelope

