from v4.evaluation import board_watch


def _row(team, score, rank, **extra):
    return {"team_name": team, "total_score": score, "rank": rank, "scored_at": "2026-10-07T00:00:00+00:00", **extra}


def _boards(scores):
    ranked = sorted(scores.items(), key=lambda item: -item[1])
    rows = [_row(team, score, rank) for rank, (team, score) in enumerate(ranked, 1)]
    cards = [{"slug": "v4-a"}]
    return {"overall": {"rows": rows, "cards": cards, "extra_cards": [{"slug": "v4-a1-v5"}]}, "v4-a": {"rows": rows}}


def test_diff_reports_changes_new_teams_and_rank_moves():
    old = _boards({"CCB": 10.0, "X": 20.0})
    new = _boards({"CCB": 30.0, "X": 20.0, "Y": 5.0})
    text = "\n".join(board_watch.diff(old, new, "CCB"))
    assert "CCB: 10.0 -> 30.0 (+20.0)" in text and "升1" in text
    assert "X: 20.0 -> 20.0" in text and "降1" in text
    assert "新增 Y" in text
    assert "CCB A–D: (10.0, 2) -> (30.0, 1)" in text


def test_team_table_aggregates_card_components():
    boards = _boards({"CCB": 10.0})
    boards["v4-a"]["rows"][0].update(
        components={"sum_best_scores": 8.0}, targets_observed=4, required_missing=1, submission_count=3
    )
    entry = board_watch.team_table(boards)["CCB"]
    assert entry["science_per_target"] == 2.0 and entry["ad_mean"] == 10.0
    assert entry["cards"]["v4-a"]["submissions"] == 3
