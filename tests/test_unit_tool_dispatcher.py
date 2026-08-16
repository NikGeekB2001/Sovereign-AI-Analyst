# -*- coding: utf-8 -*-
"""Unit-тесты Tool Dispatcher: маршрутизация, scopes, SOV-коды (без БД/LLM)."""
from backend.services import tool_dispatcher as td


class TestRouting:
    def test_unknown_tool(self):
        r = td.execute_tool("nope.nope", {}, "куратор")
        assert r["ok"] is False
        assert r["error"]["code"] == "SOV-1003"

    def test_invalid_args_missing_required(self):
        r = td.execute_tool("graph.query", {}, "куратор")
        assert r["ok"] is False
        assert r["error"]["code"] == "SOV-1001"

    def test_ok_wrapper_shape(self):
        ok = td._ok({"a": 1})
        err = td._err("SOV-1001", "x")
        assert ok == {"ok": True, "data": {"a": 1}}
        assert err["ok"] is False
        assert err["error"]["retryable"] is False


class TestScopes:
    def test_curator_denied_vector_write(self):
        r = td.execute_tool("vector.upsert", {"points": []}, "куратор")
        assert r["ok"] is False
        assert r["error"]["code"] == "SOV-4001"

    def test_admin_allowed_vector_upsert_shape(self):
        # admin имеет scope vector:write; до реального Qdrant не дойдём —
        # проверяем только, что до исполнения дошло (пустой список точек)
        r = td.execute_tool("vector.upsert", {"points": []}, "admin")
        assert r["ok"] is False  # падает на этапе исполнения (пустой список -> исключение в Qdrant)
        assert r["error"]["code"] in ("SOV-5002", "SOV-1001")


class TestDispatch:
    def test_graph_query_routes_to_impl(self, monkeypatch):
        captured = {}

        def fake_graph_query(query):
            captured["q"] = query
            return td._ok({"rows": []})

        monkeypatch.setattr(td, "_graph_query", fake_graph_query)
        r = td.execute_tool("graph.query", {"query": "MATCH (n) RETURN n"}, "куратор")
        assert r["ok"] is True
        assert captured["q"] == "MATCH (n) RETURN n"

    def test_graph_query_write_blocked_by_guard(self):
        # CREATE отклоняется cypher_guard ещё до обращения к Neo4j
        r = td._graph_query("CREATE (n:User {name: 'x'})")
        assert r["ok"] is False
        assert r["error"]["code"] == "SOV-1001"

    def test_chat_stream_not_implemented(self):
        r = td.execute_tool("chat.stream", {"message": "привет"}, "куратор")
        assert r["ok"] is False
        assert r["error"]["code"] == "SOV-1002"

    def test_chat_complete_routes(self, monkeypatch):
        monkeypatch.setattr(td, "_chat_complete", lambda args, role: td._ok({"answer": "ok"}))
        r = td.execute_tool("chat.complete", {"message": "привет"}, "куратор")
        assert r["ok"] is True
        assert r["data"]["answer"] == "ok"

    def test_vector_search_routes(self, monkeypatch):
        monkeypatch.setattr(td, "_vector_search", lambda args, role: td._ok({"documents": []}))
        r = td.execute_tool("vector.search", {"query": "налоги"}, "куратор")
        assert r["ok"] is True

    def test_scope_check_before_dispatch(self, monkeypatch):
        # даже при работающей реализации — без scope инструмент не выполнится
        monkeypatch.setattr(td, "_vector_upsert", lambda args: td._ok({"upserted": 1}))
        r = td.execute_tool("vector.upsert", {"points": []}, "куратор")
        assert r["ok"] is False
        assert r["error"]["code"] == "SOV-4001"
