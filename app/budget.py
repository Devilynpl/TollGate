import time
from collections import deque
from typing import Dict
from app.config import settings
from app.schemas import AppUsage, UsageSummary

# Pricing model (Gemini Flash estimation: $0.10 / 1M in, $0.40 / 1M out)
COST_PER_INPUT_TOKEN = 0.10 / 1_000_000
COST_PER_OUTPUT_TOKEN = 0.40 / 1_000_000


class BudgetTracker:
    """
    In-memory budget and RPM tracker with sliding window and daily token caps.
    Thread-safe basic operations for single-process async uvicorn.
    """

    def __init__(
        self,
        daily_token_cap: int = settings.daily_token_cap,
        daily_usd_cap: float = settings.daily_usd_cap,
        rpm_limit: int = settings.rpm_limit,
    ):
        self.daily_token_cap = daily_token_cap
        self.daily_usd_cap = daily_usd_cap
        self.rpm_limit = rpm_limit

        self.req_timestamps: deque = deque()
        self.by_app_timestamps: Dict[str, deque] = {}

        self.global_usage = AppUsage()
        self.app_usage: Dict[str, AppUsage] = {
            "docground": AppUsage(),
            "briefagent": AppUsage(),
        }

    def check_rpm_limit(self, app: str) -> bool:
        """Returns True if within RPM limit, False if rate limited."""
        now = time.time()
        # Clean timestamps older than 60s
        while self.req_timestamps and now - self.req_timestamps[0] > 60:
            self.req_timestamps.popleft()

        if len(self.req_timestamps) >= self.rpm_limit:
            return False

        self.req_timestamps.append(now)
        return True

    def check_daily_budget(self, est_new_tokens: int = 100) -> bool:
        """Returns True if within daily token and USD caps."""
        if (self.global_usage.total_tokens + est_new_tokens) > self.daily_token_cap:
            return False
        if self.global_usage.est_usd >= self.daily_usd_cap:
            return False
        return True

    def record_usage(self, app: str, input_tokens: int, output_tokens: int) -> float:
        """Records tokens, calculates cost, updates counters, returns est_usd."""
        cost = (input_tokens * COST_PER_INPUT_TOKEN) + (output_tokens * COST_PER_OUTPUT_TOKEN)

        # Update app usage
        if app not in self.app_usage:
            self.app_usage[app] = AppUsage()

        target = self.app_usage[app]
        target.requests += 1
        target.input_tokens += input_tokens
        target.output_tokens += output_tokens
        target.total_tokens += input_tokens + output_tokens
        target.est_usd += cost

        # Update global usage
        self.global_usage.requests += 1
        self.global_usage.input_tokens += input_tokens
        self.global_usage.output_tokens += output_tokens
        self.global_usage.total_tokens += input_tokens + output_tokens
        self.global_usage.est_usd += cost

        return cost

    def get_summary(self) -> UsageSummary:
        now = time.time()
        while self.req_timestamps and now - self.req_timestamps[0] > 60:
            self.req_timestamps.popleft()

        return UsageSummary(
            global_usage=self.global_usage,
            by_app=self.app_usage,
            rpm_current=len(self.req_timestamps),
            daily_token_cap=self.daily_token_cap,
            daily_usd_cap=self.daily_usd_cap,
            token_cap_remaining=max(0, self.daily_token_cap - self.global_usage.total_tokens),
        )


budget_tracker = BudgetTracker()
