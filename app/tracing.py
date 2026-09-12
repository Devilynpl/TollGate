import json
import logging
from pathlib import Path
from typing import Any, Dict, List, Optional
from app.config import settings

logger = logging.getLogger("tollgate.tracing")


class TraceRecorder:
    """
    JSONL-based trace logging for requests, decisions, latencies, tokens and errors.
    Writes to logs/traces.jsonl and allows retrieval by trace_id.
    """

    def __init__(self, log_path: Path = settings.traces_log_path):
        self.log_path = log_path
        self.log_path.parent.mkdir(parents=True, exist_ok=True)

    def record_trace(self, trace_entry: Dict[str, Any]):
        """Appends a trace record in JSONL format."""
        try:
            line = json.dumps(trace_entry, ensure_ascii=False)
            with open(self.log_path, "a", encoding="utf-8") as f:
                f.write(line + "\n")
        except Exception as e:
            logger.error(f"Failed to write trace record: {e}")

    def get_trace(self, trace_id: str) -> Optional[Dict[str, Any]]:
        """Scans the traces log for matching trace_id."""
        if not self.log_path.exists():
            return None
        try:
            with open(self.log_path, "r", encoding="utf-8") as f:
                for line in f:
                    if not line.strip():
                        continue
                    record = json.loads(line)
                    if record.get("trace_id") == trace_id:
                        return record
        except Exception as e:
            logger.error(f"Failed to read trace {trace_id}: {e}")
        return None

    def update_trace_with_judge(self, trace_id: str, judge_score: Dict[str, Any]):
        """Asynchronously amends an existing trace record with LLM Judge evaluations (Faza 8)."""
        if not self.log_path.exists():
            return
        try:
            records = []
            updated = False
            with open(self.log_path, "r", encoding="utf-8") as f:
                for line in f:
                    if not line.strip():
                        continue
                    rec = json.loads(line)
                    if rec.get("trace_id") == trace_id:
                        rec["judge_evaluation"] = judge_score
                        updated = True
                    records.append(rec)

            if updated:
                with open(self.log_path, "w", encoding="utf-8") as f:
                    for r in records:
                        f.write(json.dumps(r, ensure_ascii=False) + "\n")
        except Exception as e:
            logger.error(f"Failed to update trace with judge score: {e}")

    def get_recent_traces(self, limit: int = 20) -> List[Dict[str, Any]]:
        if not self.log_path.exists():
            return []
        traces = []
        try:
            with open(self.log_path, "r", encoding="utf-8") as f:
                for line in f:
                    if line.strip():
                        traces.append(json.loads(line))
        except Exception as e:
            logger.error(f"Failed to read recent traces: {e}")
        return traces[-limit:]


trace_recorder = TraceRecorder()
