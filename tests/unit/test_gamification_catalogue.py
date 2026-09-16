from __future__ import annotations

from app.gamification import service as gamification_service


def test_achievement_catalogue_has_exactly_the_four_planned_entries():
    assert [a.code for a in gamification_service.ACHIEVEMENTS] == [
        "first_outcome", "ten_contacts", "fifty_contacts", "callback_follow_through",
    ]


def test_achievement_catalogue_lookup_covers_every_entry():
    assert set(gamification_service.ACHIEVEMENTS_BY_CODE) == {
        a.code for a in gamification_service.ACHIEVEMENTS
    }


def test_daily_goal_bounds_match_the_plan():
    assert gamification_service.MIN_DAILY_GOAL == 1
    assert gamification_service.MAX_DAILY_GOAL == 500


def test_no_achievement_description_references_call_quality_or_dnc_avoidance():
    # Plan 7.3: no badge for connected-call ratio, DNC avoidance, speed, or a
    # comparison against a colleague - a cheap guard against later drift.
    banned_words = ("connect", "dnc", "do not call", "fast", "quick", "beat", "top", "rank")
    for achievement in gamification_service.ACHIEVEMENTS:
        text = f"{achievement.display_name} {achievement.description}".lower()
        for word in banned_words:
            assert word not in text, f"{achievement.code} description mentions '{word}'"
