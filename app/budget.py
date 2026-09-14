import datetime
import sqlite3
import time
from collections import deque
from pathlib import Path
from typing import Dict, Optional, Union
from app.config import settings
from app.schemas import AppUsage, UsageSummary

# Pricing model (Gemini Flash estimation: $0.10 / 1M in, $0.40 / 1M out)
COST_PER_INPUT_TOKEN = 0.10 / 1_000_000
COST_PER_OUTPUT_TOKEN = 0.40 / 1_000_000


class BudgetTracker:
    """
    Budget and RPM tracker with SQLite persistence (MEDIUM-02 fix) and sliding window RPM.
    Guarantees daily token and USD caps persist across process restarts, crashes, or deploys.
    """

    def __init__(
        self,
        daily_token_cap: int = settings.daily_token_cap,
        daily_usd_cap: float = settings.daily_usd_cap,
        rpm_limit: int = settings.rpm_limit,
        db_path: Optional[Union[str, Path]] = None,
    ):
        self.daily_token_cap = daily_token_cap
        self.daily_usd_cap = daily_usd_cap
        self.rpm_limit = rpm_limit
        if db_path is None:
            db_path = settings.cache_db_path.parent / "budget.db"
        self.db_path = Path(db_path) if str(db_path) != ":memory:" else ":memory:"
        self._is_memory = str(db_path) == ":memory:"

        if not self._is_memory:
            self.db_path.parent.mkdir(parents=True, exist_ok=True)
            self._mem_conn = None
        else:
            self._mem_conn = sqlite3.connect(":memory:")

        self.req_timestamps: deque = deque()
        self.by_app_timestamps: Dict[str, deque] = {}

        self.global_usage = AppUsage()
        self.app_usage: Dict[str, AppUsage] = {
            "docground": AppUsage(),
            "briefagent": AppUsage(),
        }

        self._init_db()
        self._load_today_usage()

    def _get_connection(self):
        if self._is_memory and self._mem_conn:
            return self._mem_conn
        return sqlite3.connect(self.db_path)

    def _init_db(self):
        conn = self._get_connection()
        conn.execute("""
            CREATE TABLE IF NOT EXISTS daily_budget_usage (
                usage_date TEXT NOT NULL,
                app TEXT NOT NULL,
                requests INTEGER DEFAULT 0,
                input_tokens INTEGER DEFAULT 0,
                output_tokens INTEGER DEFAULT 0,
                total_tokens INTEGER DEFAULT 0,
                est_usd REAL DEFAULT 0.0,
                PRIMARY KEY (usage_date, app)
            )
        """)
        conn.commit()
        if not self._is_memory:
            conn.close()

    def _get_today_str(self) -> str:
        return datetime.datetime.now(datetime.timezone.utc).strftime("%Y-%m-%d")

    def _load_today_usage(self):
        """Loads persisted usage from SQLite for the current UTC day upon startup."""
        today = self._get_today_str()
        conn = self._get_connection()
        cursor = conn.cursor()
        cursor.execute("SELECT app, requests, input_tokens, output_tokens, total_tokens, est_usd FROM daily_budget_usage WHERE usage_date = ?", (today,))
        rows = cursor.fetchall()
        if not self._is_memory:
            conn.close()

        # Reset memory state
        self.global_usage = AppUsage()
        self.app_usage = {
            "docground": AppUsage(),
            "briefagent": AppUsage(),
        }

        for app, reqs, in_tok, out_tok, tot_tok, usd in rows:
            usage = AppUsage(
                requests=reqs,
                input_tokens=in_tok,
                output_tokens=out_tok,
                total_tokens=tot_tok,
                est_usd=usd
            )
            self.app_usage[app] = usage
            self.global_usage.requests += reqs
            self.global_usage.input_tokens += in_tok
            self.global_usage.output_tokens += out_tok
            self.global_usage.total_tokens += tot_tok
            self.global_usage.est_usd += usd

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
        """Records tokens, calculates cost, updates counters, persits to SQLite, returns est_usd."""
        cost = (input_tokens * COST_PER_INPUT_TOKEN) + (output_tokens * COST_PER_OUTPUT_TOKEN)

        # Update app usage in RAM
        if app not in self.app_usage:
            self.app_usage[app] = AppUsage()

        target = self.app_usage[app]
        target.requests += 1
        target.input_tokens += input_tokens
        target.output_tokens += output_tokens
        target.total_tokens += input_tokens + output_tokens
        target.est_usd += cost

        # Update global usage in RAM
        self.global_usage.requests += 1
        self.global_usage.input_tokens += input_tokens
        self.global_usage.output_tokens += output_tokens
        self.global_usage.total_tokens += input_tokens + output_tokens
        self.global_usage.est_usd += cost

        # Persist to SQLite
        today = self._get_today_str()
        try:
            conn = self._get_connection()
            conn.execute("""
                INSERT INTO daily_budget_usage (usage_date, app, requests, input_tokens, output_tokens, total_tokens, est_usd)
                VALUES (?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(usage_date, app) DO UPDATE SET
                    requests = requests + excluded.requests,
                    input_tokens = input_tokens + excluded.input_tokens,
                    output_tokens = output_tokens + excluded.output_tokens,
                    total_tokens = total_tokens + excluded.total_tokens,
                    est_usd = est_usd + excluded.est_usd
            """, (today, app, 1, input_tokens, output_tokens, input_tokens + output_tokens, cost))
            conn.commit()
            if not self._is_memory:
                conn.close()
        except Exception as e:
            # Non-blocking log
            print(f"[BudgetTracker] Błąd zapisu do SQLite: {e}")

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
