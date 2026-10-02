# Agentic Observer complete-project example

A minimal, public example for testing repository submission and the JSONL
participant protocol. It always waits, so it is not a competitive strategy.
No model or API key is required. Never put credentials in a submitted repository.

Submit this repository URL on the Agent projects page. The platform snapshots the
source, resolves the container image, runs a public preview and asks you to review
the interface before formal evaluation. The Python runner can also connect
projects written in other languages through the same JSONL interface.

The manifest specifies the runtime and launch command. Replace the action policy
in `src/agent.py` with your strategy. Initialization produces no reply; each
decision request produces exactly one response with the matching sequence and
protocol version.

Run the protocol test with Python 3.12 or newer:

```sh
python -m unittest discover -s tests -v
```

This repository is public. To keep your own complete project private, submit a ZIP
on the platform instead.
