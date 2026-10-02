## 1. Complete projects, one submission page

Open [Participate](/compete) to upload a complete project. Submissions accept a public GitHub repository URL or a private ZIP up to 50 MB. Any language is allowed. The platform's Python runner connects to your program; it does not require your project to be Python.

The source revision, launch configuration and any adapter are fixed and tested. You review them before confirming the version. Adaptation proposes interface files; it does not silently replace your algorithm. Model calls are optional.

[Minimal complete project](https://github.com/BH3GEI/observer-project-example)

## 2. Launch configuration

Place `observer.project.json` at the project root. For example:

```json
{
  "schema_version": "observer-project-v1",
  "protocol": "jsonl-v2",
  "image": "python:3.12-slim",
  "working_directory": ".",
  "build": [],
  "run": ["python3", "agent.py"]
}
```

Use a suitable container image and build command for your language. `run` and each `build` command are arrays of arguments. The evaluated container image is pinned to a digest. Do not put credentials in the manifest or project files.

## 3. One decision per request

The project is a persistent process. Standard input and standard output carry one JSON object per line. Send diagnostic logs to standard error; flush each response immediately.

- `initialize`: public configuration and catalogue. Do not reply to this message.
- `decision_request`: current snapshot and `decision_sequence`. Read current weather, available tiles, requests, progress and forecasts released so far.
- `decision_response`: return the same sequence and `protocol_version: participant-agent-protocol-v2`, with a decision to `observe` or `wait`. See the interactive examples below for the full envelope. Reports may accompany the decision.

The server commits the current decision before releasing the next observation. Future sequences are rejected. Retrying the identical current decision is safe; a conflicting replacement is rejected. The server holds future weather and anomaly answers.

A 900-second calendar slot is not necessarily one decision: an exposure can span slots and several short exposures can begin in the same slot. Follow the returned sequence and cursor.

## 4. Confirm and evaluate

The platform records the source revision and launch configuration, tests the interface and shows them for your confirmation. Start an evaluation from the confirmed version. One batch runs all configured scenarios; each run returns decisions to the server round by round. CSV upload is not accepted.

## 5. Optional personal model APIs

Bring your own API and quota if your algorithm needs a model. The platform does not provide model credits. Credentials use HTTPS and stay only in page and request memory. They must not enter databases, project files, run artifacts or logs. Do not commit keys to your repository or ZIP.

For model calls, enter a supported HTTPS endpoint, model and key in Participate and keep that page open, including during automatic adaptation. Closing the page or disconnecting clears the key; reconnecting requires entering it again.

Deterministic algorithms do not need a key. Explanation length does not increase the performance score.

## 6. Random scenarios and scoring

Each team and new evaluation attempt gets new secret seeds. Public catalogues, observation tasks and scoring rules stay fixed. Weather, events and hidden tags vary. Infrastructure retries of the same run retain its scenario.

Three frozen reference algorithms test each generated scenario against frozen calibration criteria. Outliers are not served. The ranking score is:

```
10000 × (raw score − all-wait score) / (reference mean − all-wait score)
```

All-wait scores 0; the reference mean scores 10000; stronger algorithms may exceed 10000. Calibration reduces differences; it cannot guarantee identical difficulty for every strategy.

A batch includes all scenarios and ranks only when they all finish. The batch score is the mean calibrated score. Each team keeps its best complete batch, without mixing the best scenario scores from different attempts. Original score components remain available. Detailed scoring, penalties and tie-breaking are in [Rules](/rules).

## 7. Results and reproduction

The result records decisions, original and calibrated scores, runtime status and logs. The platform's private audit record retains the seed, generator version, calibration profile, source revision and file digests so organizers can regenerate the exact scenario and re-score the decisions. Future data and private credentials are never included in participant downloads.

Keep your own source version, launch settings and reproduction notes. Design review considers code, run records and reproduction separately from the performance board.
