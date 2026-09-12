"""
Unit tests for Semantic Cache using SQLite.
"""

import tempfile
from pathlib import Path
import pytest
from app.cache import SemanticCache


@pytest.fixture
def temp_cache():
    # Use :memory: database to avoid Windows file lock during test cleanup
    cache = SemanticCache(db_path=Path(":memory:"), threshold=0.90)
    yield cache


def test_semantic_cache_hit_and_miss(temp_cache):
    # Vector embeddings: 4-dim unit vectors
    emb_query1 = [1.0, 0.0, 0.0, 0.0]
    emb_query1_similar = [0.98, 0.19, 0.0, 0.0]  # Cosine similarity > 0.95
    emb_query_different = [0.0, 1.0, 0.0, 0.0]   # Orthogonal vector (sim = 0)

    # Initially empty: should miss
    match = temp_cache.find_match(emb_query1, corpus_version="v1")
    assert match is None

    # Store answer for query 1
    temp_cache.store(
        query_text="Jaka jest cena Standard Cloud VM w 2025?",
        corpus_version="v1",
        query_embedding=emb_query1,
        response_text="W cenniku 2025 cena to 189 PLN.",
    )

    # Exact match hit
    hit = temp_cache.find_match(emb_query1, corpus_version="v1")
    assert hit is not None
    text, sim = hit
    assert text == "W cenniku 2025 cena to 189 PLN."
    assert sim >= 0.99

    # Similar query hit
    hit_similar = temp_cache.find_match(emb_query1_similar, corpus_version="v1")
    assert hit_similar is not None
    assert hit_similar[0] == "W cenniku 2025 cena to 189 PLN."
    assert hit_similar[1] >= 0.90

    # Different question: should miss
    miss = temp_cache.find_match(emb_query_different, corpus_version="v1")
    assert miss is None

    # Different corpus_version: should miss even if vector is identical
    diff_version = temp_cache.find_match(emb_query1, corpus_version="v2")
    assert diff_version is None
