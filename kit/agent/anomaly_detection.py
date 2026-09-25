"""Deterministic anomaly detection and calibrated reporting for the minimal agent.

The platform publishes the exact scoring formula, so every observation has a
public baseline: the preview estimate recorded when the exposure was committed.
Each snapshot's `tile_last_finished` carries the realized official score of the
most recently finished observation. Snapshots never carry
`instrument_efficiency`, so the baseline is efficiency-free and the
realized/estimated ratio isolates the hidden instrument side
(efficiency jitter x fault multiplier x tag multiplier):

- instrument fault   ratio ≈ 0.10-0.55 (fault band) x jitter
- reddening tag      ratio ≈ 0.8 x jitter
- nova tag           ratio ≈ 1.5 x jitter
- ordinary weather   ratio ≈ jitter ∈ [0.90, 1.00], plus a few percent of
  slot-to-slot drift

Reports cost the +100/-150 tag odds and the fault misreport ledger, so this
layer is deliberately conservative: bands sit tight around the published
factors, a tag is reported only once a tile's reads (minimum five, spanning at
least two nights) sit in the tag's band at least 80% of the time — a true tag
reads in-band almost every night, while weather edges dip in-band only
occasionally — and an instrument
fault needs two collapsed exposures inside a rolling window before it is
reported. Blind guessing is negative-expected-value (break-even at 60%
confidence); when in doubt, do not report.

All thresholds are env-overridable (SAC_ANOMALY_*) for organizer calibration.
"""

from __future__ import annotations

import math
import os
from datetime import datetime
from typing import Mapping


def _float_env(name: str, default: float) -> float:
    try:
        return float(os.environ.get(name, default))
    except (TypeError, ValueError):
        return default


def _int_env(name: str, default: int) -> int:
    try:
        return int(os.environ.get(name, default))
    except (TypeError, ValueError):
        return default


def _parse_utc(value: object) -> datetime | None:
    if not isinstance(value, str) or not value:
        return None
    try:
        return datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None


def _separation_deg(ra1: float, dec1: float, ra2: float, dec2: float) -> float:
    ra1r, dec1r, ra2r, dec2r = map(math.radians, (ra1, dec1, ra2, dec2))
    cosine = math.sin(dec1r) * math.sin(dec2r) + math.cos(dec1r) * math.cos(dec2r) * math.cos(ra1r - ra2r)
    return math.degrees(math.acos(max(-1.0, min(1.0, cosine))))


class AnomalyDetector:
    """Run-long memory for realized-vs-baseline deviation tracking."""

    def __init__(self, initial_publication: Mapping[str, object]) -> None:
        self.nova_ratio_min = _float_env("SAC_ANOMALY_NOVA_RATIO_MIN", 1.30)
        self.nova_ratio_max = _float_env("SAC_ANOMALY_NOVA_RATIO_MAX", 1.65)
        self.reddening_ratio_min = _float_env("SAC_ANOMALY_REDDENING_RATIO_MIN", 0.70)
        self.reddening_ratio_max = _float_env("SAC_ANOMALY_REDDENING_RATIO_MAX", 0.87)
        self.fault_ratio_max = _float_env("SAC_ANOMALY_FAULT_RATIO_MAX", 0.60)
        self.fault_min_evidence = _int_env("SAC_ANOMALY_FAULT_MIN_EVIDENCE", 2)
        self.fault_evidence_window = _int_env("SAC_ANOMALY_FAULT_EVIDENCE_WINDOW", 6)
        self.tag_min_reads = _int_env("SAC_ANOMALY_TAG_MIN_READS", 5)
        self.tag_min_fraction = _float_env("SAC_ANOMALY_TAG_MIN_FRACTION", 0.8)
        scoring = initial_publication.get("scoring_contract", {})
        self._program_bonus = dict(scoring.get("score_config", {}).get("program_bonus", {})) or {"DARK": 0.25, "BRIGHT": 0.15, "BACKUP": 0.08}
        self._tile_coords = {
            str(tile["tile_id"]): (float(tile["ra_deg"]), float(tile["dec_deg"]))
            for tile in initial_publication.get("tile_catalog", {}).get("tiles", [])
            if "ra_deg" in tile and "dec_deg" in tile
        }
        self.bests: dict[str, float] = {}
        self._pending: dict[str, object] | None = None
        self._last_feedback: tuple[str, float] | None = None
        self._reported_tags: set[tuple[str, str]] = set()
        self._tag_reads: dict[str, list[str | None]] = {}
        self._recent_ratios: list[float] = []
        self._fault_pending = False
        self._fault_scope: dict[str, object] | None = None
        self._fault_repair_until: datetime | None = None
        self._forecasts: list[Mapping[str, object]] = []

    def potential_of(self, preview_row) -> float:
        """The public-baseline score of one exposure under the commit-time snapshot.

        Program bands never include instrument efficiency (scorer and preview
        agree by construction), so the preview bonus counts unconditionally.
        """
        bonus = float(self._program_bonus.get(str(preview_row.quality_band), 0.0))
        return float(preview_row.tile_science_value) * float(preview_row.combined_quality) * (1.0 + bonus)

    def note_observation(self, tile_id: str | None, expected: float | None, under_cold_wave: bool = False) -> None:
        """Remember the estimate of the observation just committed (None for waits)."""
        if tile_id is None or expected is None:
            self._pending = None
            return
        self._pending = {"tile_id": tile_id, "expected": float(expected), "cold_wave": under_cold_wave}
        self.bests[tile_id] = max(self.bests.get(tile_id, 0.0), float(expected))

    def under_cold_wave(self, snapshot: Mapping[str, object]) -> bool:
        """Whether a published forecast currently predicts a cold_wave (the only
        forecastable event that also moves instrument_efficiency). Reads taken
        under one carry a public, legitimate efficiency dip and must not count
        as anomaly evidence."""
        weekly = snapshot.get("weekly")
        if isinstance(weekly, Mapping) and weekly.get("weather_forecast") is not None:
            self._forecasts = list(weekly["weather_forecast"])
        now = _parse_utc((snapshot.get("cursor") or {}).get("timestamp_utc"))
        if now is None:
            return False
        for forecast in self._forecasts:
            if forecast.get("condition") != "cold_wave":
                continue
            start = _parse_utc(forecast.get("predicted_start_utc"))
            end = _parse_utc(forecast.get("predicted_end_utc"))
            if start is not None and end is not None and start <= now < end:
                return True
        return False

    def process_snapshot(self, snapshot: Mapping[str, object]) -> list[dict[str, str]]:
        """Consume feedback/fault publications and return the reports to attach now."""
        reports: list[dict[str, str]] = []
        fault_status = snapshot.get("fault_status")
        if isinstance(fault_status, Mapping):
            if fault_status.get("status") == "fault":
                self._fault_pending = False
                self._recent_ratios.clear()
                self._fault_scope = {
                    "spatial_scope_type": fault_status.get("spatial_scope_type"),
                    "spatial_scope_payload": fault_status.get("spatial_scope_payload") or {},
                }
                self._fault_repair_until = _parse_utc(fault_status.get("repair_complete_utc"))
            elif fault_status.get("status") == "normal":
                # The platform answered a misreport: clear the pending flag and require
                # fresh collapse evidence before reporting again.
                self._fault_pending = False
                self._recent_ratios.clear()
        feedback = snapshot.get("tile_last_finished")
        if isinstance(feedback, Mapping) and feedback.get("tile_id"):
            key = (str(feedback["tile_id"]), float(feedback.get("score", 0.0)))
            if key != self._last_feedback:
                self._last_feedback = key
                pending = self._pending
                self._pending = None
                if pending is not None and pending["tile_id"] == key[0] and float(pending["expected"]) > 0:
                    realized, expected = key[1], float(pending["expected"])
                    self.bests[key[0]] = max(self.bests.get(key[0], 0.0), realized)
                    # zero means the exposure was interrupted: no anomaly signal.
                    # a forecasted cold_wave is a public efficiency dip: not an anomaly either.
                    if realized > 0 and not pending.get("cold_wave"):
                        reports.extend(self._classify(key[0], realized / expected, snapshot))
        return reports

    def _classify(self, tile_id: str, ratio: float, snapshot: Mapping[str, object]) -> list[dict[str, str]]:
        reports: list[dict[str, str]] = []
        band = None
        if self.nova_ratio_min <= ratio <= self.nova_ratio_max:
            band = "NOVA"
        elif self.reddening_ratio_min <= ratio <= self.reddening_ratio_max:
            band = "Reddening"
        # Tag reads accumulate per tile; a tag is permanent, so it must dominate
        # the tile's whole read history, not just appear once (weather edges dip
        # in-band only occasionally), and across more than one night.
        reads = self._tag_reads.setdefault(tile_id, [])
        night = (snapshot.get("cursor") or {}).get("night_id", "")
        reads.append((band, night))
        if band is not None and (tile_id, band) not in self._reported_tags:
            hits = [read_night for read_band, read_night in reads if read_band == band]
            if (
                len(reads) >= self.tag_min_reads
                and len(hits) / len(reads) >= self.tag_min_fraction
                and len(set(hits)) >= 2
            ):
                self._reported_tags.add((tile_id, band))
                reports.append({"kind": band, "tile_id": tile_id})
        # Faults persist for many hours but the agent keeps observing other tiles,
        # so evidence accumulates over a rolling window rather than consecutive reads.
        self._recent_ratios.append(ratio)
        del self._recent_ratios[:-self.fault_evidence_window]
        collapses = sum(value <= self.fault_ratio_max for value in self._recent_ratios)
        now = _parse_utc((snapshot.get("cursor") or {}).get("timestamp_utc"))
        repair_active = self._fault_repair_until is not None and (now is None or now < self._fault_repair_until)
        if collapses >= self.fault_min_evidence and not self._fault_pending and not repair_active:
            self._fault_pending = True
            self._recent_ratios.clear()
            reports.append({"kind": "Instrument_Failure"})
        return reports

    def top_suspect(self, previews) -> object | None:
        """The best-ranked preview whose tile's latest read was in-band and unreported."""
        def is_suspect(tile_id: str) -> bool:
            reads = self._tag_reads.get(tile_id)
            return bool(reads) and reads[-1][0] is not None and (tile_id, reads[-1][0]) not in self._reported_tags
        return next((row for row in previews if is_suspect(row.tile_id)), None)

    def _in_fault_scope(self, candidate: Mapping[str, object]) -> bool:
        if self._fault_scope is None:
            return False
        scope_type = self._fault_scope["spatial_scope_type"]
        payload = self._fault_scope["spatial_scope_payload"]
        if scope_type == "REGION_SET":
            return str(candidate.get("region_id")) in set(payload.get("region_ids", []))
        if scope_type == "SKY_CAP_ICRS":
            coords = self._tile_coords.get(str(candidate.get("tile_id")))
            if coords is None:
                return False
            return _separation_deg(coords[0], coords[1], float(payload["ra_deg"]), float(payload["dec_deg"])) <= float(payload["radius_deg"])
        # Shipped configs scope faults to REGION_SET only, so avoidance is complete;
        # the SKY_CAP_ICRS branch above is kept for generality. HORIZON_SECTOR would
        # need mount-side geometry the agent does not have: keep the candidate.
        return False

    def filter_fault_scope(self, snapshot: Mapping[str, object]) -> Mapping[str, object]:
        """Drop candidates inside a known-active fault's scope while alternatives exist."""
        now = _parse_utc((snapshot.get("cursor") or {}).get("timestamp_utc"))
        if self._fault_scope is None or self._fault_repair_until is None or now is None or now >= self._fault_repair_until:
            return snapshot
        candidates = list(snapshot.get("candidate_tiles", []))
        kept = [candidate for candidate in candidates if not self._in_fault_scope(candidate)]
        if not kept or len(kept) == len(candidates):
            return snapshot
        return {**snapshot, "candidate_tiles": kept}
