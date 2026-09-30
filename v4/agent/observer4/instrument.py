"""Exact exposure levels, instrument efficiency tracking and instrument-fault reporting.

Every unsaturated hit of one exposure yields the same public level
z = score / weight * f0 * t0 / (flux * t * k) = efficiency * sky * program multiplier,
where k is the target's lunar factor over airmass^0.6 / q0 in the engine's own sampling.
The program band ignores the instrument, so splitting the level into efficiency and sky
keeps program choices right through earthquakes and faults. Site weather drifts slowly
within a night and announced events start at slot boundaries; an unannounced persistent
step is the fault signature. The fault generator draws its single start slot uniformly
within 200 slots of the survey midpoint, which bounds how long a fault can stay hidden.
"""

from __future__ import annotations

import itertools
import math

import numpy as np

from .geometry import airmass, altaz, horizontal_vectors, lunar_factors, parse_utc, sidereal

SATURATION_TOLERANCE = 2e-5
PIECE_SECONDS = 120.0


def engine_revision(publication: dict) -> int:
    """1 for the pinned engine; 2 once reports return results and airmass is time resolved."""
    reporting = (publication.get("scoring") or {}).get("reporting") or {}
    limits = publication.get("limits") or {}
    return 2 if "false_report_free_allowance" in reporting or "max_consecutive_reports" in limits else 1


def exposure_k(task, indices, start: float, duration: float, time_resolved: bool) -> np.ndarray:
    """Lunar factor over airmass for each target, averaged as the scorer samples it."""
    indices = np.asarray(indices, dtype=np.int64)
    if not len(indices):
        return np.zeros(0)
    ra, dec = task.ra[indices], task.dec[indices]
    exponent = float(task.scoring["airmass_exponent"])
    model = task.scoring.get("lunar_model")
    count = max(1, math.ceil(duration / PIECE_SECONDS))
    edges = np.minimum(start + PIECE_SECONDS * np.arange(count + 1), start + duration)
    mids = (edges[:-1] + edges[1:]) / 2
    weights = np.diff(edges)
    total = np.zeros(len(indices))
    for moment, width in zip(mids, weights):
        lunar = lunar_factors(ra, dec, float(moment), task.latitude, task.longitude, model)
        if time_resolved:
            altitude, _ = altaz(horizontal_vectors(ra, dec, sidereal(float(moment), task.longitude), task.latitude))
            lunar = lunar / airmass(altitude) ** exponent
        total += width * lunar
    k = total / max(1e-9, float(np.sum(weights)))
    if not time_resolved:
        middle = start + duration / 2
        altitude, _ = altaz(horizontal_vectors(ra, dec, sidereal(middle, task.longitude), task.latitude))
        k = k / airmass(altitude) ** exponent
    return k / float(task.scoring["q0"])


def exposure_level(scores, weights, fluxes, ks, duration, program, multipliers, mismatch, zero_point,
                   predicted_match=None, reference=None, spread=0.06):
    """Return (log efficiency*sky, unsaturated samples, all-zero flag, matched flag or None)."""
    scores = np.asarray(scores, dtype=float)
    if not len(scores):
        return None, 0, False, None
    ratio = scores / np.asarray(weights, dtype=float)
    positive = scores > 0
    if not np.any(positive):
        return None, 0, True, None
    declared = float(multipliers[program])
    matched = positive & (np.abs(ratio - declared) < SATURATION_TOLERANCE)
    unmatched = positive & (np.abs(ratio - mismatch) < SATURATION_TOLERANCE)
    other = positive & ~matched & ~unmatched
    if not np.any(other):
        flag = True if np.any(matched) and not np.any(unmatched) else False if np.any(unmatched) and not np.any(matched) else None
        return None, 0, False, flag
    z = ratio[other] * zero_point / (np.asarray(fluxes)[other] * duration * np.asarray(ks)[other])
    z = z[np.isfinite(z) & (z > 0)]
    if not len(z):
        return None, 0, False, None
    low, high = float(np.min(z)), float(np.max(z))
    if high / low > 1.0 + 0.5 * (declared / mismatch - 1.0):
        # Targets straddle the band threshold: the low group is unmatched.
        if abs(math.log(high / low) - math.log(declared / mismatch)) < 0.03:
            return math.log(low / mismatch), len(z), False, None
        return None, len(z), False, None
    if np.any(matched) and not np.any(unmatched):
        multiplier, flag = declared, True
    elif np.any(unmatched) and not np.any(matched):
        multiplier, flag = mismatch, False
    else:
        # No saturated target fixes the multiplier: weigh the band prior against continuity.
        prior = 0.5 if predicted_match is None else float(np.clip(predicted_match, 0.02, 0.98))
        level = math.log(float(np.median(z)))
        candidates = []
        for multiplier, flag, weight in ((declared, True, prior), (mismatch, False, 1.0 - prior)):
            value = level - math.log(multiplier)
            penalty = 0.0 if reference is None else (value - reference) ** 2 / (2 * spread * spread)
            candidates.append((math.log(weight) - penalty, value, flag))
        _, value, flag = max(candidates)
        return value, len(z), False, None
    return math.log(float(np.median(z)) / multiplier), len(z), False, flag


def build_record(task, pending: dict, hits: dict, signature: frozenset, time_resolved: bool,
                 reference=None, mixed: bool = False) -> dict:
    """Public evidence of one finished exposure for the instrument monitor."""
    rows = pending["targets"]
    indices = np.array([row["index"] for row in rows], dtype=np.int64)
    clean = np.array([row.get("local_clean", row["clean"]) for row in rows], dtype=bool)
    hit = np.array([task.ids[i] in hits for i in indices], dtype=bool)
    scores = np.array([hits.get(task.ids[i], 0.0) for i in indices])
    use = hit & clean
    level, samples, zero, matched = None, 0, bool(np.any(hit) and not np.any(scores > 0)), None
    z_all = None
    if np.any(hit):
        k_all = exposure_k(task, indices[hit], pending["start"], pending["duration"], time_resolved)
        zero_point = float(task.scoring["flux_zero_point"]) * float(task.scoring["exposure_zero_point_seconds"])
        z_all = np.zeros(len(indices))
        z_all[hit] = scores[hit] / task.weight[indices[hit]] * zero_point / (
            task.flux[indices[hit]] * pending["duration"] * k_all)
    if np.any(use):
        k = exposure_k(task, indices[use], pending["start"], pending["duration"], time_resolved)
        program = task.scoring["program"]
        prior = float(np.mean([row.get("matched_probability", 0.5) for row, used in zip(rows, use) if used]))
        level, samples, _, matched = exposure_level(
            scores[use], task.weight[indices[use]], task.flux[indices[use]], k, pending["duration"],
            pending["program"], program["multipliers"], float(program["mismatch_multiplier"]),
            float(task.scoring["flux_zero_point"]) * float(task.scoring["exposure_zero_point_seconds"]),
            prior, reference)
    status = np.full(len(indices), np.nan)
    band_k = np.full(len(indices), np.nan)
    if z_all is not None and level is not None:
        program = task.scoring["program"]
        declared = float(program["multipliers"][pending["program"]])
        mismatch = float(program["mismatch_multiplier"])
        ratio = scores / task.weight[indices]
        positive = hit & use & (scores > 0)
        status[positive & (np.abs(ratio - declared) < SATURATION_TOLERANCE)] = 1.0
        status[positive & (np.abs(ratio - mismatch) < SATURATION_TOLERANCE)] = 0.0
        unknown = positive & np.isnan(status)
        implied = z_all[unknown] / math.exp(level)
        spread = float(np.max(implied) / np.min(implied)) if np.any(unknown) else 1.0
        # Unsaturated statuses are only exact when saturation or two groups fix the multiplier.
        if matched is not None or abs(math.log(spread) - math.log(declared / mismatch)) < 0.03:
            status[np.flatnonzero(unknown)[np.abs(implied - declared) < 0.03 * declared]] = 1.0
            status[np.flatnonzero(unknown)[np.abs(implied - mismatch) < 0.03 * mismatch]] = 0.0
        band_k[hit] = k_all
    return {"start": pending["start"], "end": pending["start"] + pending["duration"], "night": pending["night"],
            "level": level, "zero": zero, "signature": signature, "samples": samples, "matched": matched,
            "mixed": mixed, "indices": indices, "hit": hit, "scores": scores, "z": z_all, "clean": clean,
            "program": pending["program"], "status": status, "band_k": band_k}


class EfficiencyModel:
    """Relative efficiency from announced earthquakes and the fault state."""

    def __init__(self, settings: dict):
        self.settings = settings
        self.quakes: list[dict] = []
        self.fault_log = 0.0
        self.quake_active = False

    def log_efficiency(self, night: int, now: float) -> float:
        tau = float(self.settings.get("quake_decay_nights", 4.0))
        value = 0.0
        for quake in self.quakes:
            if now < quake["time"]:
                continue
            degradation = quake["d0"] * math.exp(-max(0, night - quake["night"]) / tau)
            value += math.log(max(0.02, 1.0 - degradation))
        return value + self.fault_log

    def quake_onset(self, now: float, night: int) -> dict:
        quake = {"time": now, "night": night, "d0": float(self.settings.get("quake_prior_degradation", 0.5)),
                 "measured": False}
        self.quakes.append(quake)
        return quake


class InstrumentMonitor:
    """Levels per exposure, efficiency tracking, fault detection and report decisions."""

    def __init__(self, task, settings: dict, revision: int):
        self.task = task
        self.settings = settings
        self.revision = revision
        self.efficiency = EfficiencyModel(settings)
        reporting = task.scoring.get("reporting", {})
        self.free_allowance = int(reporting.get("false_report_free_allowance", 0)) if revision >= 2 else 0
        self.false_since_correct = 0
        self.records: list[dict] = []
        self.segment_start = 0
        self.pending = None
        self.repaired = False
        self.reports: list[dict] = []
        self.quake_notice = False
        self.last_signature = None
        self.vetoes = 0
        self.window_trusted = True
        self.repairs = 0
        self.slot_offsets = np.cumsum([0] + [round((n.end - n.start) / task.slot_seconds) for n in task.nights])
        total = int(self.slot_offsets[-1])
        spread = float(settings.get("window_slots", 200))
        self.window = (total // 2 - spread, total // 2 + spread)
        fractions = settings.get("probe_fractions", [0.67])
        self.probes = [self.window[0] + float(f) * (self.window[1] - self.window[0]) for f in fractions]

    # -- clock --------------------------------------------------------------------
    def slot_index(self, now: float, night: int | None) -> float:
        if night is None:
            upcoming = next((i for i, n in enumerate(self.task.nights) if n.start > now), len(self.task.nights))
            return float(self.slot_offsets[upcoming])
        start = self.task.nights[night].start
        return float(self.slot_offsets[night] + (now - start) / self.task.slot_seconds)

    # -- notices ------------------------------------------------------------------
    def notices(self, bulletins: list[dict], now: float) -> None:
        """Track the earthquake notice; its first appearance marks a new shock."""
        for bulletin in bulletins:
            kinds = {n.get("event_kind") for n in bulletin.get("notices", []) if n.get("direction") == "ALL"}
            active = "earthquake" in kinds
            if active and not self.quake_notice:
                try:
                    issued = parse_utc(bulletin["issued_at_utc"]).timestamp()
                except (KeyError, TypeError, ValueError):
                    issued = now
                quake_night = self.task.current_night(issued)
                self.efficiency.quake_onset(issued, quake_night if quake_night is not None else 0)
            self.quake_notice = active

    def log_eff(self, record: dict) -> float:
        return self.efficiency.log_efficiency(record["night"], record["start"])

    # -- evidence -----------------------------------------------------------------
    def add(self, record: dict) -> None:
        records = self.records
        if records:
            last = records[-1]
            gap = record["start"] - last["end"]
            if (record["night"] != last["night"] or record["signature"] != last["signature"] or record["zero"]
                    or record.get("mixed") or gap > float(self.settings.get("segment_gap_seconds", 3600.0))):
                self.segment_start = len(records) + (1 if record["zero"] or record.get("mixed") else 0)
        elif record["zero"] or record.get("mixed"):
            self.segment_start = 1
        records.append(record)
        self._measure_quakes(record)
        if self.pending is not None and record["level"] is not None and self.revision < 2:
            self._verify(record)

    def _measure_quakes(self, record: dict) -> None:
        """Measure a new shock from levels just before and after its announcement."""
        for quake in self.efficiency.quakes:
            if quake["measured"] or record["level"] is None or record["start"] < quake["time"]:
                continue
            reduced = record["signature"] - {"earthquake"}
            before = [r for r in self.records[:-1] if r["level"] is not None and r["end"] <= quake["time"] + 1.0
                      and (r["signature"] - {"earthquake"}) == reduced]
            if before:
                last = before[-1]
                quake["d0"] = 0.0
                older = self.log_eff(last)
                drop = record["level"] - last["level"] - (self.log_eff(record) - older)
                quake["d0"] = float(np.clip(1.0 - math.exp(min(0.0, drop)), 0.02, 0.97))
                quake["measured_same_night"] = last["night"] == record["night"]
            quake["measured"] = True

    def reference_level(self, night: int, signature: frozenset, start: float) -> float | None:
        """Latest level of the current segment, for resolving an unknown program multiplier."""
        levels = self.segment()
        if levels and levels[-1]["night"] == night and levels[-1]["signature"] == signature \
                and start - levels[-1]["end"] <= 3600.0:
            return levels[-1]["level"]
        return None

    def segment(self) -> list[dict]:
        return [r for r in self.records[self.segment_start:] if r["level"] is not None]

    def sky_levels(self, limit: int = 600) -> np.ndarray:
        """Recent log(efficiency * sky) levels with the instrument at its nominal state."""
        prior = math.log(float(self.settings.get("instrument_prior", 0.95)))
        values = [r["level"] - self.log_eff(r) + prior for r in self.records[-limit:] if r["level"] is not None]
        return np.array(values)

    def sky_level(self) -> float | None:
        """log(site weather) of the latest level: level minus modelled efficiency."""
        levels = [r for r in self.records[-64:] if r["level"] is not None]
        if not levels:
            return None
        return levels[-1]["level"] - self.log_eff(levels[-1])

    def predict(self, now: float, night: int, signature: frozenset) -> tuple[float, float] | None:
        """Predicted log(efficiency * sky) for an exposure starting now, with its spread."""
        log_eff = self.efficiency.log_efficiency(night, now)
        levels = self.segment()
        if (levels and levels[-1]["night"] == night and levels[-1]["signature"] == signature
                and now - levels[-1]["end"] <= float(self.settings.get("fresh_seconds", 3600.0))):
            sky = float(np.median([r["level"] - self.log_eff(r) for r in levels[-3:]]))
            return sky + log_eff, float(self.settings.get("fresh_sigma", 0.05))
        reduced = signature - {"earthquake"}
        for record in reversed(self.records[-400:]):
            if record["level"] is None or (record["signature"] - {"earthquake"}) != reduced:
                continue
            sky = record["level"] - self.log_eff(record)
            same = record["night"] == night
            return sky + log_eff, float(self.settings.get("same_night_sigma" if same else "stale_sigma",
                                                         0.08 if same else 0.15))
        return None

    def band_llr(self, records: list[dict], weather_shift: float) -> tuple[float, int]:
        """log P(band flags | instrument cut) - log P(band flags | weather drop of weather_shift).

        Program bands ignore the instrument: after a fault every target keeps its band,
        after a weather drop of the same size bands move down.
        """
        bands = self.task.scoring["program"]["bands"]
        dark, bright = math.log(float(bands["DARK"])), math.log(float(bands["BRIGHT"]))
        sigma = float(self.settings.get("band_sigma", 0.06))
        total, count = 0.0, 0
        for record in records:
            status, k = record.get("status"), record.get("band_k")
            if status is None or record["level"] is None:
                continue
            known = np.isfinite(status) & np.isfinite(k)
            if not np.any(known):
                continue
            # Before the change: level = log(eff * W); the fault hypothesis keeps the old W.
            sky_new = record["level"] - math.log(float(self.settings.get("instrument_prior", 0.95))) - self.log_eff(record)
            for hypothesis, sky in (("fault", sky_new - weather_shift), ("weather", sky_new)):
                q = sky + np.log(k[known])
                upper = 1 / (1 + np.exp(-(q - dark) / sigma))
                lower = 1 / (1 + np.exp(-(q - bright) / sigma))
                if record["program"] == "DARK":
                    p = upper
                elif record["program"] == "BRIGHT":
                    p = lower - upper
                else:
                    p = 1 - lower
                p = np.clip(p, 0.02, 0.98)
                likelihood = np.where(status[known] > 0.5, np.log(p), np.log(1 - p))
                total += float(np.sum(likelihood)) * (1 if hypothesis == "fault" else -1)
            count += int(np.count_nonzero(known))
        return total, count

    # -- detection ----------------------------------------------------------------
    def _persistent(self, levels: list[dict], reference: float, threshold: float) -> list[dict] | None:
        """Levels after a change that stay below reference - threshold and that background
        dome closures cannot explain; one outlier is tolerated in longer runs."""
        if len(levels) < int(self.settings.get("confirm_exposures", 2)):
            return None
        values = np.array([r["level"] for r in levels])
        median = float(np.median(values))
        if median >= reference - threshold:
            return None
        close = np.abs(values - median) < float(self.settings.get("confirm_spread", 0.12))
        needed = len(values) if len(values) <= 3 else math.ceil(0.75 * len(values))
        if np.count_nonzero(close) < needed:
            return None
        if self._closures_explain(levels, reference):
            return None
        return levels

    def _closures_explain(self, levels: list[dict], reference: float) -> bool:
        """True when closing up to four weather slots reproduces every drop of the run.

        A closed slot removes its overlap from each exposure, so an exposure's level falls
        by log(open fraction); a fault lowers every later exposure by the same amount.
        """
        slot = float(self.task.slot_seconds)
        cells = {}
        spans = []
        for record in levels:
            night = self.task.nights[record["night"]] if 0 <= record["night"] < len(self.task.nights) else None
            origin = night.start if night is not None else 0.0
            first = math.floor((record["start"] - origin) / slot)
            last = math.floor((record["end"] - 1e-6 - origin) / slot)
            touched = []
            for cell in range(first, last + 1):
                low = origin + cell * slot
                overlap = max(0.0, min(low + slot, record["end"]) - max(low, record["start"]))
                key = (record["night"], cell)
                cells.setdefault(key, len(cells))
                touched.append((cells[key], overlap))
            spans.append((max(1.0, record["end"] - record["start"]), touched))
        tolerance = float(self.settings.get("dilution_tolerance", 0.15))
        drops = np.array([r["level"] - reference for r in levels])
        count = len(cells)
        if count > 16:
            return False
        for size in range(1, min(4, count) + 1):
            for closed in itertools.combinations(range(count), size):
                shut = set(closed)
                predicted = []
                for duration, touched in spans:
                    open_fraction = 1.0 - sum(overlap for cell, overlap in touched if cell in shut) / duration
                    predicted.append(math.log(max(1e-3, open_fraction)))
                if np.all(np.abs(drops - np.array(predicted)) <= tolerance):
                    return True
        return False

    def _step(self, scale: float = 1.0) -> dict | None:
        levels = self.segment()
        reference_count = int(self.settings.get("reference_exposures", 2))
        threshold = scale * float(self.settings.get("step_threshold", 0.28))
        window = int(self.settings.get("step_window", 6))
        spread = float(self.settings.get("reference_spread", 0.2))
        wide = float(self.settings.get("reference_spread_max", 0.4))
        for split in range(max(reference_count, len(levels) - window), len(levels) - 1):
            # One exposure may straddle the change; it belongs to neither side.
            for gap in (0, 1):
                if split - gap < reference_count:
                    continue
                reference = [r["level"] for r in levels[:split - gap]][-int(self.settings.get("reference_window", 8)):]
                width = max(reference) - min(reference)
                if width >= wide:
                    continue
                # A noisy reference needs a proportionally larger drop.
                needed = threshold if width < spread else threshold + width
                ref = float(np.median(reference))
                if levels[split]["level"] >= ref - needed:
                    continue
                run = self._persistent(levels[split:], ref, needed)
                if run is not None:
                    level = float(np.median([r["level"] for r in run]))
                    return {"kind": "step", "reference": ref, "level": level, "drop": level - ref, "run": run}
        return None

    def _cross_segment(self, scale: float = 1.0) -> dict | None:
        """Levels of a new segment against the latest comparable earlier levels."""
        levels = self.segment()
        if not levels or len(levels) > int(self.settings.get("cross_segment_exposures", 6)):
            return None
        first = levels[0]
        # Earthquakes are modelled as efficiency; a cold snap moves the sky by at most ~11 %.
        mild = {"earthquake", "cold_snap"}
        reduced = first["signature"] - mild
        earlier = [r for r in self.records[:self.segment_start] if r["level"] is not None
                   and (r["signature"] - mild) == reduced and r["end"] <= first["start"]]
        if not earlier or first["start"] - earlier[-1]["end"] > 3 * 86400.0:
            return None
        previous = [r for r in earlier if r["night"] == earlier[-1]["night"]][-3:]
        sky_before = float(np.median([r["level"] - self.log_eff(r) for r in previous]))
        same_night = previous[-1]["night"] == first["night"]
        threshold = scale * float(self.settings.get("cross_segment_threshold" if same_night else "cross_night_threshold",
                                                    0.30 if same_night else 0.45))
        if ("cold_snap" in first["signature"]) != ("cold_snap" in previous[-1]["signature"]):
            threshold += float(self.settings.get("cold_snap_allowance", 0.12))
        # Compare efficiency-corrected levels; the first record may straddle the change.
        shifted = [dict(r, level=r["level"] - self.log_eff(r)) for r in levels[1:]] if len(levels) > 2 else \
            [dict(r, level=r["level"] - self.log_eff(r)) for r in levels]
        run = self._persistent(shifted, sky_before, threshold)
        if run is None:
            return None
        drop = float(np.median([r["level"] for r in run])) - sky_before
        originals = [r for r in levels if any(r["start"] == x["start"] for x in run)]
        return {"kind": "cross_segment" if same_night else "cross_night", "reference": None,
                "level": float(np.median([r["level"] + self.log_eff(r) for r in run])), "drop": drop,
                "run": originals}

    def _detect(self, scale: float) -> dict | None:
        evidence = self._step(scale) or self._cross_segment(scale)
        if evidence is None:
            return None
        run = evidence.pop("run", None)
        if run:
            llr, count = self.band_llr(run, evidence["drop"])
            evidence["band_llr"], evidence["band_targets"] = round(llr, 3), count
            if llr < -float(self.settings.get("band_veto", 3.0)):
                self.vetoes += 1
                return None
        evidence["scale"] = scale
        return evidence

    def decide(self, now: float, night: int) -> dict | None:
        """Return report evidence when a report is warranted now.

        The generator starts one fault uniformly within window_slots of the survey
        midpoint, so a report before the window cannot be right and one after it cannot be
        wrong. Free false reports (engine revision 2) buy weaker evidence and two probes.
        """
        if self.pending is not None or not self.settings.get("enabled", True):
            return None
        if self.repaired and not (self.revision >= 2 and self.free_left()):
            return None
        if len(self.reports) >= int(self.settings.get("maximum_reports", 6)):
            return None
        slot = self.slot_index(now, night)
        trusted = self.window_trusted and not self.repaired
        free = self.free_left()
        if trusted and slot < self.window[0] - float(self.settings.get("window_margin_slots", 2)):
            # A card with more faults than the generator's default starts one earlier; only
            # a clear instrument-like cut (program bands kept) is reported before the window.
            evidence = self._detect(1.0) if self.settings.get("early_reports", True) else None
            if evidence is None:
                return None
            quiet = all(now - quake["time"] >= float(self.settings.get("early_quake_quiet_hours", 48.0)) * 3600.0
                        for quake in self.efficiency.quakes)
            strong = (quiet and evidence["drop"] < -float(self.settings.get("early_drop", 0.5))
                      and evidence.get("band_llr", 0.0) >= float(self.settings.get("early_band_llr", 3.0))
                      and evidence.get("band_targets", 0) >= 3)
            certain = strong and evidence.get("band_llr", 0.0) >= float(self.settings.get("early_paid_llr", 6.0))
            if not (strong and (free or certain)) or evidence["drop"] < -float(self.settings.get("maximum_fault_drop", 1.8)):
                return None
            evidence["kind"] = "early_" + evidence["kind"]
            return evidence
        evidence = self._detect(1.0)
        if evidence is None and free:
            evidence = self._detect(float(self.settings.get("probe_threshold_scale", 0.6)))
        if evidence is not None and evidence["drop"] < -float(self.settings.get("maximum_fault_drop", 1.8)):
            # No fault cuts efficiency this far, an unannounced aftershock can.
            self._hidden_quake(evidence, now, night)
            evidence = None
        if evidence is not None and evidence["scale"] < 1.0 and not free:
            evidence = None
        if evidence is None and trusted:
            fallback_due = slot >= self.window[1] + float(self.settings.get("fallback_margin_slots", 4))
            if fallback_due and self.settings.get("window_fallback", True) and \
                    not any(r.get("kind") == "fallback" for r in self.reports):
                evidence = {"kind": "fallback", "reference": None, "level": self._last_level(), "drop": 0.0}
            elif free and self.settings.get("free_probes", True):
                due = [p for p in self.probes if slot >= p and not any(r.get("probe") == p for r in self.reports)]
                if due:
                    evidence = {"kind": "probe", "probe": due[0], "reference": None, "level": self._last_level(),
                                "drop": 0.0}
        return evidence

    def _hidden_quake(self, evidence: dict, now: float, night: int) -> None:
        quake = self.efficiency.quake_onset(now, night)
        quake["d0"] = float(np.clip(1.0 - math.exp(evidence["drop"]), 0.02, 0.97))
        quake["measured"] = True
        quake["hidden"] = True
        self.segment_start = len(self.records)

    def free_left(self) -> bool:
        return self.revision >= 2 and self.false_since_correct < self.free_allowance

    def _last_level(self):
        levels = [r for r in self.records if r["level"] is not None]
        return levels[-1]["level"] if levels else None

    # -- reporting ----------------------------------------------------------------
    def reported(self, evidence: dict, now: float, night: int) -> None:
        levels = [r for r in self.records if r["level"] is not None]
        entry = dict(evidence, time=now, night=night, resolved=None, before=levels[-1] if levels else None)
        self.reports.append(entry)
        self.pending = entry

    def report_result(self, result: dict) -> None:
        """Engine revision 2 returns the outcome directly."""
        if self.pending is None:
            return
        entry, self.pending = self.pending, None
        entry["resolved"] = "correct" if result.get("correct") else "false"
        if result.get("correct"):
            self._confirm(entry)
        else:
            self.false_since_correct += 1
            if entry["kind"] == "fallback":
                self.window_trusted = False  # the single-fault window does not hold on this card
            self._reject(entry)

    def _verify(self, record: dict) -> None:
        """Engine revision 1: a repair restores the level at once.

        A fault cuts efficiency by at least 30 %, while weather hardly moves between two
        exposures, so a clear rise right after the report marks a repair. The drop that
        triggered a report can include a program-multiplier error; the rise is checked
        against both the drop and the level before it.
        """
        entry = self.pending
        if entry.get("before") is None:
            self.pending = None
            entry["resolved"] = "unknown"
            return
        before = entry["before"]
        gain = record["level"] - before["level"] - (self.log_eff(record) - self.log_eff(before))
        minimum = float(self.settings.get("recovery_minimum", 0.22))
        if record["night"] != before["night"]:
            minimum = max(minimum, float(self.settings.get("recovery_cross_night", 0.3)))
        required = max(minimum, float(self.settings.get("recovery_fraction", 0.45)) * max(0.0, -entry["drop"]))
        restored = (entry.get("reference") is not None and gain >= 0.15
                    and record["level"] >= entry["reference"] - float(self.settings.get("recovery_tolerance", 0.2)))
        self.pending = None
        if gain >= required or restored:
            entry["resolved"] = "correct"
            entry["gain"] = gain
            self._confirm(entry)
        else:
            entry["resolved"] = "false"
            entry["gain"] = gain
            self._reject(entry)

    def _confirm(self, entry: dict) -> None:
        self.repaired = True
        self.repairs += 1
        self.false_since_correct = 0
        self.efficiency.fault_log = 0.0
        self.segment_start = len(self.records)

    def _reject(self, entry: dict) -> None:
        if entry["kind"].removeprefix("early_") in ("step", "cross_segment", "cross_night") and entry["drop"] < 0 \
                and entry.get("band_llr", 0.0) > float(self.settings.get("quake_band_llr", 1.0)):
            # An efficiency cut that a report did not repair behaves like a hidden aftershock;
            # program bands that kept their levels show the cut was not in the sky.
            quake = self.efficiency.quake_onset(entry["time"], entry["night"])
            quake["d0"] = float(np.clip(1.0 - math.exp(entry["drop"]), 0.02, 0.97))
            quake["measured"] = True
            quake["hidden"] = True
        self.segment_start = len(self.records)

    def status(self) -> dict:
        return {"repaired": self.repaired, "reports": len(self.reports),
                "report_kinds": [r["kind"] for r in self.reports],
                "resolved": [r.get("resolved") for r in self.reports],
                "drops": [round(r.get("drop", 0.0), 3) for r in self.reports],
                "gains": [round(r["gain"], 3) if r.get("gain") is not None else None for r in self.reports],
                "quakes": [{"night": q["night"], "d0": round(q["d0"], 3)} for q in self.efficiency.quakes],
                "window": [round(x, 1) for x in self.window], "revision": self.revision,
                "free_allowance": self.free_allowance, "vetoes": self.vetoes,
                "window_trusted": self.window_trusted, "repairs": self.repairs}
