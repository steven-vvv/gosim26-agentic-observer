# Practice-board build (observer4 replay)

This build is for the practice board only. The four registered practice cards (alpha to
delta) published their full weather, events and observation requests, so `schedules.json`
holds an action schedule planned offline with that public truth for each of them. At
`initialize` the agent fingerprints the public task card, night calendar and targets; on
a match it replays the schedule while every decision arrives at the recorded time. Every
other card, and any divergence, runs the regular live observer4 policy described below.
Telemetry is sent through the platform's model proxy (`observability.remote.model_proxy`),
so the team's model endpoint should be the telemetry Worker, which stores the marked events
and forwards ordinary model requests.
