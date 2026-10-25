import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "app"))
from budget_breakdown import summarize, category  # noqa: E402


def test_purpose_totals_preserve_daily_validation_and_monthly_accounting():
    rows = [
        ("2026-10-23", "reply", "completed", .12, 2),
        ("2026-10-23", "reply_official_fallback", "completed", .03, 1),
        ("2026-10-23", "conversation_memory", "estimated", .02, 1),
        ("2026-10-23", "qzone_image_understanding", "completed_with_error", .04, 1),
        ("2026-10-23", "web_fetch", "failed_or_unknown", .005, 1),
        ("2026-10-23", "web_search", "reserved", .005, 1),
        ("2026-10-23", "future_unknown_feature", "interrupted", .01, 1),
        ("2026-10-23", "validation_probe", "completed", .2, 2),
        ("2026-10-23", "acceptance_model", "failed_or_unknown", .1, 1),
        ("2026-10-22", "reply", "completed", .7, 8),
    ]
    result = summarize(rows, "2026-10-23")
    assert result["today"] == .195 and result["validation"] == .2
    assert result["month"] == 1.095
    assert result["unconfirmed"] == .035 and result["validation_calls"] == 3
    assert result["usage_estimate"] == .19 and result["month_usage_estimate"] == 1.09
    assert result["month_unconfirmed"] == .135
    assert result["active_reserve"] == .005
    assert result["validation_usage_estimate"] == .2 and result["validation_unconfirmed"] == .1
    groups = {row["id"]: row for row in result["breakdown"]}
    assert sum(row["amount"] for row in groups.values()) == pytest.approx(result["today"])
    assert groups["chat"]["amount"] == .15 and groups["chat"]["calls"] == 3
    assert groups["qzone"]["unconfirmed"] == 0
    assert groups["other"]["amount"] == 0 and groups["other"]["unconfirmed"] == .01
    assert all("future_unknown_feature" not in str(row) for row in groups.values())


def test_next_day_clears_detail_while_month_preserves_prior_days():
    rows = [("2026-10-23", "reply", "completed", .25, 4),
            ("2026-10-24", "web_search", "completed", .005, 1)]
    result = summarize(rows, "2026-10-24")
    assert result["today"] == .005 and result["month"] == .255
    assert [row["id"] for row in result["breakdown"]] == ["web"]
    assert summarize([], "2026-10-24")["breakdown"] == []


@pytest.mark.parametrize("kind,expected", [
    ("drawing", "drawing"), ("digital_activity_plan", "reading"),
    ("group_interjection", "group"), ("image_understanding_official_fallback", "images"),
    ("self_signature", "profile"), ("public_novelty_official_fallback", "novelty"),
    ("reading_catalog_fetch", "web"),
])
def test_known_cost_centers(kind, expected):
    assert category(kind) == expected
