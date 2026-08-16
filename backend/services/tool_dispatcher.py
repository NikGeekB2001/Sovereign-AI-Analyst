# -*- coding: utf-8 -*-
"""Tool Dispatcher: единая точка выполнения инструментов MCP-контракта.

Маршрут вызова (совпадает с docs/diagrams/tool_call_sequence.puml и
docs/C4_Model/Level_3_Component_ToolLayer.puml):

    Tool Registry (валидация SOV-1001/1003) -> Dispatcher (scopes SOV-4001)
        -> реализация инструмента (graph.* / vector.* / chat.*)

Результат всегда в единой обёртке:
    {"ok": true, "data": ...}
    {"ok": false, "error": {"code": "SOV-XXXX", "message": str, "retryable": bool}}

Точка входа API: POST /api/v1/tools/call (backend/api/main.py).
"""
from __future__ import annotations

import os
from typing import Any, Dict, List

from backend.services.cypher_guard import is_safe_readonly_cypher
from backend.services.rag_retriever import get_reranker, get_retriever
from backend.services.tool_registry import TOOLS, scopes_for_role, validate_args

MAX_GRAPH_ROWS = 50


# ---------------------------------------------------------------------------
# Обёртки результата
# ---------------------------------------------------------------------------

def _ok(data: Any) -> Dict[str, Any]:
    return {"ok": True, "data": data}


def _err(code: str, message: str, retryable: bool = False) -> Dict[str, Any]:
    return {"ok": False, "error": {"code": code, "message": message, "retryable": retryable}}


# ---------------------------------------------------------------------------
# graph.*
# ---------------------------------------------------------------------------

def _jsonable(value: Any) -> Any:
    """Сериализация значений Neo4j (Node/Relationship -> свойства) в JSON-безопасное."""
    if isinstance(value, dict):
        return {k: _jsonable(v) for k, v in value.items()}
    if isinstance(value, (list, tuple)):
        return [_jsonable(v) for v in value]
    if hasattr(value, "element_id") and hasattr(value, "keys"):
        # Node / Relationship
        return {k: _jsonable(v) for k, v in value.items()}
    return value


def _graph_query(query: str) -> Dict[str, Any]:
    """graph.query: read-only Cypher в Neo4j (cypher_guard обязателен)."""
    if not is_safe_readonly_cypher(query):
        return _err("SOV-1001", "Cypher не является read-only или невалиден (cypher_guard)")
    from neo4j import GraphDatabase

    driver = GraphDatabase.driver(
        os.getenv("NEO4J_URI", "bolt://localhost:7687"),
        auth=(os.getenv("NEO4J_USER", "neo4j"), os.getenv("NEO4J_PASSWORD", "password")),
    )
    try:
        with driver.session() as session:
            result = session.run(query)
            rows = [_jsonable(dict(r)) for r in result]
        truncated = len(rows) > MAX_GRAPH_ROWS
        return _ok({"rows": rows[:MAX_GRAPH_ROWS], "count": len(rows), "truncated": truncated})
    except Exception as e:  # noqa: BLE001
        return _err("SOV-5001", f"Neo4j недоступен: {e}", retryable=True)
    finally:
        driver.close()


def _graph_schema() -> Dict[str, Any]:
    """graph.schema: метки, типы связей (для генерации Cypher)."""
    from neo4j import GraphDatabase

    driver = GraphDatabase.driver(
        os.getenv("NEO4J_URI", "bolt://localhost:7687"),
        auth=(os.getenv("NEO4J_USER", "neo4j"), os.getenv("NEO4J_PASSWORD", "password")),
    )
    try:
        with driver.session() as session:
            labels = session.run("CALL db.labels() YIELD label RETURN label").value("label")
            rels = session.run(
                "CALL db.relationshipTypes() YIELD relationshipType RETURN relationshipType"
            ).value("relationshipType")
        return _ok({"labels": sorted(labels), "relationship_types": sorted(rels)})
    except Exception as e:  # noqa: BLE001
        return _err("SOV-5001", f"Neo4j недоступен: {e}", retryable=True)
    finally:
        driver.close()


# ---------------------------------------------------------------------------
# vector.*
# ---------------------------------------------------------------------------

def _vector_search(args: Dict[str, Any], role: str) -> Dict[str, Any]:
    """vector.search: семантический поиск по Qdrant с RBAC-фильтром access_level."""
    try:
        retriever = get_retriever()
        docs = retriever.retrieve(
            args["query"],
            user_role=role,
            top_k=int(args.get("top_k", 5)),
            min_score=float(args.get("min_score", 0.3)),
        )
        return _ok({"documents": docs})
    except Exception as e:  # noqa: BLE001
        return _err("SOV-5002", f"Qdrant/эмбеддинг недоступен: {e}", retryable=True)


def _vector_rerank(args: Dict[str, Any]) -> Dict[str, Any]:
    """vector.rerank: реранкинг кандидатов кросс-энкодером (Qwen2.5-7B)."""
    try:
        reranker = get_reranker()
        docs = reranker.rerank(args["query"], args["documents"], top_k=int(args.get("top_k", 3)))
        return _ok({"documents": docs})
    except Exception as e:  # noqa: BLE001
        return _err("SOV-3002", f"Реранкер недоступен: {e}", retryable=True)


def _vector_upsert(args: Dict[str, Any]) -> Dict[str, Any]:
    """vector.upsert: запись точек в Qdrant (scope vector:write = только admin)."""
    try:
        from qdrant_client import QdrantClient
        from qdrant_client.models import PointStruct

        client = QdrantClient(url=os.getenv("QDRANT_URL", "http://localhost:6333"))
        retriever = get_retriever()
        collection = os.getenv("QDRANT_COLLECTION", "ruslawod")
        points: List[PointStruct] = []
        for p in args["points"]:
            text = str(p.get("text", ""))
            vector = retriever.get_embedding(text)
            point_id = p.get("id") or (abs(hash(text)) % (10 ** 12))
            points.append(
                PointStruct(
                    id=point_id,
                    vector=vector,
                    payload={
                        "act_id": p.get("act_id", ""),
                        "doc_type": p.get("doc_type", "external"),
                        "text_preview": text[:500],
                        "access_level": p.get("access_level", "public"),
                    },
                )
            )
        client.upsert(collection_name=collection, points=points)
        return _ok({"upserted": len(points)})
    except Exception as e:  # noqa: BLE001
        return _err("SOV-5002", f"Qdrant недоступен: {e}", retryable=True)


# ---------------------------------------------------------------------------
# chat.*
# ---------------------------------------------------------------------------

def _chat_complete(args: Dict[str, Any], role: str) -> Dict[str, Any]:
    """chat.complete: полный конвейер агента (7 узлов LangGraph)."""
    try:
        import asyncio

        from backend.agents.multi_agent_graph import app as agent_app

        async def _run() -> str:
            inputs = {
                "messages": [("user", args["message"])],
                "user_role": role,
                "context": [],
            }
            result = await agent_app.ainvoke(inputs, config={"recursion_limit": 50})
            messages = result.get("messages", []) if result else []
            if not messages:
                return ""
            last = messages[-1]
            if isinstance(last, tuple):
                return last[1] if len(last) > 1 else ""
            return getattr(last, "content", "") or str(last)

        answer = asyncio.run(_run())
        return _ok({"answer": answer})
    except Exception as e:  # noqa: BLE001
        return _err("SOV-3001", f"Конвейер агента: {e}", retryable=True)


# ---------------------------------------------------------------------------
# Единая точка входа
# ---------------------------------------------------------------------------

def execute_tool(tool_name: str, args: Dict[str, Any] | None, role: str = "куратор") -> Dict[str, Any]:
    """Выполняет инструмент по контракту: валидация -> scopes -> исполнение."""
    args = args or {}

    # 1. Инструмент существует
    if tool_name not in TOOLS:
        return _err(
            "SOV-1003",
            f"Неизвестный инструмент '{tool_name}'. Доступно: {', '.join(sorted(TOOLS))}",
        )

    # 2. Валидация аргументов (Tool Registry)
    errs = validate_args(tool_name, args)
    if errs:
        return _err("SOV-1001", "; ".join(errs))

    # 3. Scopes по роли
    allowed = set(scopes_for_role(role))
    missing = [s for s in TOOLS[tool_name]["scopes"] if s not in allowed]
    if missing:
        return _err("SOV-4001", f"Роль '{role}' не имеет scope: {', '.join(missing)}")

    # 4. Диспетчеризация
    if tool_name == "graph.query":
        return _graph_query(args["query"])
    if tool_name == "graph.schema":
        return _graph_schema()
    if tool_name == "vector.search":
        return _vector_search(args, role)
    if tool_name == "vector.rerank":
        return _vector_rerank(args)
    if tool_name == "vector.upsert":
        return _vector_upsert(args)
    if tool_name == "chat.complete":
        return _chat_complete(args, role)
    if tool_name == "chat.stream":
        return _err(
            "SOV-1002",
            "chat.stream требует SSE-контракт (фаза 3). Используйте POST /api/v1/chat/stream",
        )
    return _err("SOV-1003", f"Инструмент '{tool_name}' не реализован в диспетчере")
