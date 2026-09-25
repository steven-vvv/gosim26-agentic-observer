#!/usr/bin/env python3
"""Run the participant minimal agent over persistent JSON-Lines standard I/O."""

from __future__ import annotations

import json
import sys
from pathlib import Path

if sys.version_info < (3, 9):
    sys.stderr.write(f"minimal-agent: Python 3.9 or newer is required, this interpreter is {sys.version.split()[0]} "
                     f"({sys.executable}). Install Python 3.12 from python.org and run the kit with it.\n")
    sys.exit(3)


AGENT_DIR = Path(__file__).resolve().parent
# scoring_preview.py ships next to this file in the starter kit; on the platform it lives one level up.
for _candidate in (AGENT_DIR, AGENT_DIR.parent):
    if (_candidate / "scoring_preview.py").exists() and str(_candidate) not in sys.path:
        sys.path.insert(0, str(_candidate))

from decision_graph import MinimalDecisionAgent
from model_factory import ModelConfigurationError, ModelSettings, build_chat_model
from protocol import decision_response, parse_platform_message


def run(stdin=sys.stdin, stdout=sys.stdout) -> None:
    """Consume platform envelopes and emit one response per decision request."""
    settings = ModelSettings.from_environment(AGENT_DIR / ".env")
    try:
        model = build_chat_model(settings)
        provider_status = "deterministic" if model is None else settings.provider
    except ModelConfigurationError as exc:
        model = None
        provider_status = f"deterministic fallback ({type(exc).__name__})"
    print(f"minimal-agent provider={provider_status}", file=sys.stderr, flush=True)
    agent: MinimalDecisionAgent | None = None
    for line in stdin:
        if not line.strip():
            continue
        message = json.loads(line)
        message_type, payload = parse_platform_message(message)
        if message_type == "initialize":
            agent = MinimalDecisionAgent(
                payload, model=model, top_k=settings.top_k_candidates
            )
            continue
        if agent is None:
            raise RuntimeError("decision_request received before initialize")
        decision = agent.decide(payload)
        response = decision_response(int(message["decision_sequence"]), decision, decision.get("reports"))
        print(
            json.dumps(response, ensure_ascii=False, separators=(",", ":")),
            file=stdout,
            flush=True,
        )


def main() -> None:
    run()


if __name__ == "__main__":
    main()

