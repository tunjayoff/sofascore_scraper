"""Mirrors frontend/src/lib/jobLabel.ts — classify job payload for activity list."""


def job_action_kind(payload: dict | None, current_task: str = "") -> str:
    p = payload or {}
    selections = p.get("selections") or []
    if not isinstance(selections, list):
        selections = []
    match_count = sum(len(s.get("match_ids") or []) for s in selections if isinstance(s, dict))
    season_count = sum(len(s.get("season_ids") or []) for s in selections if isinstance(s, dict))
    league_id = None
    if selections and isinstance(selections[0], dict):
        league_id = selections[0].get("league_id")
    if league_id is None:
        league_id = p.get("league_id")

    if p.get("mode") == "details" or match_count > 0:
        return "details"
    if season_count > 0:
        return "seasons"
    if league_id is not None:
        return "league"
    return "generic"


def test_details_kind():
    assert (
        job_action_kind(
            {"mode": "details", "selections": [{"league_id": 17, "match_ids": [1, 2]}]}
        )
        == "details"
    )


def test_seasons_kind():
    assert (
        job_action_kind(
            {"mode": "full", "selections": [{"league_id": 17, "season_ids": [76986]}]}
        )
        == "seasons"
    )


def test_generic_kind():
    assert job_action_kind({"mode": "full", "selections": []}) == "generic"
