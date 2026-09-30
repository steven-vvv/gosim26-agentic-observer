"""Fault research rules use only prior samples and current public notices."""

from __future__ import annotations

from v4.evaluation.fault_study import Rule, first_trigger


def trace():
    early = [[float(i), 0, 0.8] for i in range(8)]
    late = [[float(i + 10), 1 if i < 2 else 2, 0.3] for i in range(4)]
    return {"clean_samples": early + late,
            "band_checks": [[float(i + 10), 2, "DARK", True, 1.2] for i in range(4)],
            "decisions": [{"now": 13.0, "night": 2, "notices": [], "forecast_nights": []}]}


def test_fault_rule_needs_cross_night_drop_with_matching_dark_band():
    item = trace()
    result = first_trigger(item, 0.65, Rule(), minimum=4)
    assert result is not None and result["drop"] == 0.375
    item["band_checks"][0][3] = False
    item["band_checks"][1][3] = False
    assert first_trigger(item, 0.65, Rule(), minimum=4) is None


def test_fault_rule_obeys_current_notice_and_forecast_guards():
    item = trace()
    item["decisions"][0]["notices"] = [["earthquake", "ALL"]]
    assert first_trigger(item, 0.65, Rule(quake_cooldown=False), minimum=4) is None
    item["decisions"][0]["notices"] = []
    item["decisions"][0]["forecast_nights"] = [2]
    assert first_trigger(item, 0.65, Rule(), minimum=4) is None
    assert first_trigger(item, 0.65, Rule(forecast_guard=False), minimum=4) is not None
