# -*- coding: utf-8 -*-
"""Интеграционные тесты контракта данных (требуют живых Neo4j/Qdrant).

Проверяют договор загрузчика: access_level обязателен на узлах Neo4j
и в payload Qdrant; RBAC-фильтр junior видит только public.
Пропускаются автоматически, если БД недоступны (CI без инфраструктуры).
"""
import os
import subprocess
import sys

import pytest

from qdrant_client import QdrantClient
from qdrant_client.models import FieldCondition, Filter, MatchAny, MatchValue
from neo4j import GraphDatabase

NEO4J_URI = os.getenv("NEO4J_URI", "bolt://localhost:7687")
NEO4J_USER = os.getenv("NEO4J_USER", "neo4j")
NEO4J_PASSWORD = os.getenv("NEO4J_PASSWORD", "password")
QDRANT_URL = os.getenv("QDRANT_URL", "http://localhost:6333")
COLLECTION = os.getenv("QDRANT_COLLECTION", "ruslawod")


def _dbs_ready() -> bool:
    try:
        driver = GraphDatabase.driver(NEO4J_URI, auth=(NEO4J_USER, NEO4J_PASSWORD))
        driver.verify_connectivity()
        driver.close()
        QdrantClient(url=QDRANT_URL).get_collections()
        return True
    except Exception:
        return False


pytestmark = pytest.mark.integration
if not _dbs_ready():
    # БД недоступны: тесты остаются integration (выбираются по -m), но пропускаются с причиной
    pytestmark = [
        pytest.mark.integration,
        pytest.mark.skipif(True, reason="Neo4j/Qdrant недоступны — пропуск интеграционных тестов"),
    ]


def _neo4j():
    driver = GraphDatabase.driver(NEO4J_URI, auth=(NEO4J_USER, NEO4J_PASSWORD))
    return driver


def test_neo4j_schema_and_access_level():
    with _neo4j().session() as s:
        total = s.run("MATCH (a:LegalAct) RETURN count(a) AS c").single()["c"]
        assert total >= 1, "Нет узлов LegalAct — запустите data/load_to_dbs.py"

        missing = s.run(
            "MATCH (a:LegalAct) WHERE a.access_level IS NULL RETURN count(a) AS c"
        ).single()["c"]
        assert missing == 0, f"{missing} узлов LegalAct без access_level (контракт нарушен)"

        statuses = set(
            s.run("MATCH (a:LegalAct) RETURN DISTINCT a.access_level AS l").value("l")
        )
        assert statuses, "access_level пуст"
        assert statuses <= {"public", "internal", "restricted"}


def test_qdrant_payload_contract():
    client = QdrantClient(url=QDRANT_URL)
    coll = client.get_collection(COLLECTION)
    assert coll.points_count >= 1, "Qdrant пуст — запустите data/load_to_dbs.py"

    pts = client.scroll(collection_name=COLLECTION, limit=100, with_payload=True, with_vectors=False)[0]
    assert pts, "Нет точек"
    for p in pts:
        assert p.payload.get("access_level") in {"public", "internal", "restricted"}, \
            f"Точка {p.id} без access_level"
        assert p.payload.get("act_id"), f"Точка {p.id} без act_id"
        assert p.payload.get("text_preview"), f"Точка {p.id} без text_preview"


def test_rbac_filter_junior_vs_admin():
    """Контракт RBAC: junior видит public, admin — все уровни."""
    client = QdrantClient(url=QDRANT_URL)

    junior_filter = Filter(must=[FieldCondition(key="access_level", match=MatchValue(value="public"))])
    admin_filter = Filter(must=[FieldCondition(key="access_level", match=MatchAny(any=["public", "internal", "restricted"]))])

    junior_n = client.count(collection_name=COLLECTION, count_filter=junior_filter, exact=True).count
    admin_n = client.count(collection_name=COLLECTION, count_filter=admin_filter, exact=True).count
    total = client.count(collection_name=COLLECTION, exact=True).count

    assert 0 <= junior_n <= total
    assert admin_n == total, f"admin видит {admin_n} из {total}"
    assert junior_n <= admin_n


def test_loader_idempotent_rerun():
    """Повторный запуск загрузчика не падает (MERGE/upsert идемпотентны)."""
    repo = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    env = dict(os.environ)
    env.update({
        "NEO4J_URI": NEO4J_URI,
        "NEO4J_USER": NEO4J_USER,
        "NEO4J_PASSWORD": NEO4J_PASSWORD,
        "QDRANT_URL": QDRANT_URL,
        "OLLAMA_BASE_URL": os.getenv("OLLAMA_BASE_URL", "http://localhost:11434"),
        "EMBEDDING_MODEL": os.getenv("EMBEDDING_MODEL", "bge-m3"),
        "MAX_DOCS": "10",
    })
    res = subprocess.run(
        [sys.executable, "data/load_to_dbs.py"],
        cwd=repo,
        env=env,
        capture_output=True,
        text=True,
        timeout=300,
    )
    assert res.returncode == 0, f"Загрузчик упал: {res.stderr[-2000:]}"
