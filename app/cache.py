import math
import sqlite3
import json
from pathlib import Path
from typing import List, Optional, Tuple
from app.config import settings


class SemanticCache:
    """
    Local Semantic Cache using SQLite.
    Stores query embeddings, response text, and corpus_version.
    Only enabled for app='docground'.
    Calculates cosine similarity to find cache hits.
    """

    def __init__(self, db_path: Path = settings.cache_db_path, threshold: float = settings.cache_similarity_threshold):
        self.db_path = db_path
        self.threshold = threshold
        self._is_memory = str(db_path) == ":memory:"
        if not self._is_memory:
            self.db_path.parent.mkdir(parents=True, exist_ok=True)
            self._mem_conn = None
        else:
            self._mem_conn = sqlite3.connect(":memory:")

        self._init_db()

    def _get_connection(self):
        if self._is_memory and self._mem_conn:
            return self._mem_conn
        return sqlite3.connect(self.db_path)

    def _init_db(self):
        conn = self._get_connection()
        conn.execute("""
            CREATE TABLE IF NOT EXISTS semantic_cache (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                app TEXT NOT NULL DEFAULT 'docground',
                tenant_id TEXT NOT NULL DEFAULT 'default',
                query_text TEXT NOT NULL,
                corpus_version TEXT NOT NULL,
                embedding_json TEXT NOT NULL,
                response_text TEXT NOT NULL,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
        """)
        # Backward-compatible migrations if table already exists without columns
        try:
            conn.execute("ALTER TABLE semantic_cache ADD COLUMN app TEXT NOT NULL DEFAULT 'docground'")
        except Exception:
            pass
        try:
            conn.execute("ALTER TABLE semantic_cache ADD COLUMN tenant_id TEXT NOT NULL DEFAULT 'default'")
        except Exception:
            pass

        conn.execute("CREATE INDEX IF NOT EXISTS idx_cache_tenant ON semantic_cache(app, tenant_id, corpus_version)")
        conn.commit()
        if not self._is_memory:
            conn.close()

    @staticmethod
    def _cosine_similarity(vec_a: List[float], vec_b: List[float]) -> float:
        if not vec_a or not vec_b or len(vec_a) != len(vec_b):
            return 0.0
        dot = sum(a * b for a, b in zip(vec_a, vec_b))
        norm_a = math.sqrt(sum(a * a for a in vec_a))
        norm_b = math.sqrt(sum(b * b for b in vec_b))
        if norm_a == 0.0 or norm_b == 0.0:
            return 0.0
        return dot / (norm_a * norm_b)

    def find_match(
        self,
        query_embedding: List[float],
        corpus_version: str,
        threshold: Optional[float] = None,
        app: str = "docground",
        tenant_id: str = "default",
    ) -> Optional[Tuple[str, float]]:
        """
        Finds highest similarity cached response matching tenant_id, app, and corpus_version.
        Guarantees strict multi-tenant isolation (prevents Cross-Tenant Data Leaks).
        Returns (response_text, similarity) or None.
        """
        thresh = threshold if threshold is not None else self.threshold
        best_match: Optional[Tuple[str, float]] = None
        highest_sim = 0.0

        conn = self._get_connection()
        try:
            cursor = conn.cursor()
            cursor.execute(
                "SELECT response_text, embedding_json FROM semantic_cache WHERE app = ? AND tenant_id = ? AND corpus_version = ?",
                (app, tenant_id, corpus_version)
            )
            rows = cursor.fetchall()
            for resp_text, emb_json in rows:
                try:
                    cached_emb = json.loads(emb_json)
                    sim = self._cosine_similarity(query_embedding, cached_emb)
                    if sim > highest_sim and sim >= thresh:
                        highest_sim = sim
                        best_match = (resp_text, sim)
                except Exception:
                    continue
        finally:
            if not self._is_memory:
                conn.close()

        return best_match

    def store(
        self,
        query_text: str,
        corpus_version: str,
        query_embedding: List[float],
        response_text: str,
        app: str = "docground",
        tenant_id: str = "default",
    ):
        """Stores a new query-response pair partitioned by app and tenant_id."""
        if not response_text or not query_embedding:
            return
        conn = self._get_connection()
        try:
            conn.execute(
                "INSERT INTO semantic_cache (app, tenant_id, query_text, corpus_version, embedding_json, response_text) VALUES (?, ?, ?, ?, ?, ?)",
                (app, tenant_id, query_text, corpus_version, json.dumps(query_embedding), response_text)
            )
            conn.commit()
        finally:
            if not self._is_memory:
                conn.close()

    def clear(self):
        conn = self._get_connection()
        try:
            conn.execute("DELETE FROM semantic_cache")
            conn.commit()
        finally:
            if not self._is_memory:
                conn.close()


semantic_cache = SemanticCache()
