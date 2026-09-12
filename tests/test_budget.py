"""
Unit tests for Tollgate Budget Tracker and In-Memory RPM Limiter.
"""

import time
import pytest
from app.budget import BudgetTracker


def test_rpm_limit():
    # Setup tracker with RPM = 5
    tracker = BudgetTracker(rpm_limit=5)

    # 5 requests should pass
    for _ in range(5):
        assert tracker.check_rpm_limit("docground") is True

    # 6th request should fail due to RPM cap
    assert tracker.check_rpm_limit("docground") is False


def test_daily_token_and_cost_cap():
    # Setup tracker with 1000 tokens limit and $0.05 limit
    tracker = BudgetTracker(daily_token_cap=1000, daily_usd_cap=0.05, rpm_limit=100)

    # Initial budget is available
    assert tracker.check_daily_budget(est_new_tokens=500) is True

    # Record 600 tokens
    tracker.record_usage("docground", input_tokens=400, output_tokens=200)

    # Summary check
    summary = tracker.get_summary()
    assert summary.global_usage.total_tokens == 600
    assert summary.by_app["docground"].total_tokens == 600
    assert summary.token_cap_remaining == 400

    # Next check with 500 tokens should exceed remaining 400
    assert tracker.check_daily_budget(est_new_tokens=500) is False

    # Check with 300 tokens passes
    assert tracker.check_daily_budget(est_new_tokens=300) is True

    # Exceeding budget completely
    tracker.record_usage("briefagent", input_tokens=300, output_tokens=200)
    assert tracker.check_daily_budget(est_new_tokens=10) is False
