# -*- coding: utf-8 -*-
"""Unit-тесты cypher_guard: защита от не-Cypher текста и write-запросов LLM."""
import pytest

from backend.services.cypher_guard import clean_cypher, is_safe_readonly_cypher


class TestCleanCypher:
    def test_strips_fenced_cypher(self):
        assert clean_cypher("```cypher\nMATCH (n) RETURN n\n```") == "MATCH (n) RETURN n"

    def test_strips_fenced_no_lang(self):
        assert clean_cypher("```\nMATCH (n) RETURN n\n```") == "MATCH (n) RETURN n"

    def test_empty_and_none(self):
        assert clean_cypher("") == ""
        assert clean_cypher(None) == ""
        assert clean_cypher("   ") == ""


class TestIsSafeReadonly:
    @pytest.mark.parametrize(
        "query",
        [
            "MATCH (a:LegalAct) RETURN a.act_id, a.title",
            "OPTIONAL MATCH (a)-[:ISSUED_BY]->(auth) RETURN a, auth",
            "UNWIND [1,2,3] AS x RETURN x",
            "WITH 1 AS x RETURN x",
            "RETURN 1",
            "SHOW CONSTRAINTS",
            "CALL db.labels() YIELD label RETURN label",
            "```cypher\nMATCH (a) WHERE a.status = 'действующий' RETURN a LIMIT 10\n```",
            "MATCH (a:LegalAct)-[:HAS_KEYWORD]->(k:Keyword) WHERE toLower(k.value) CONTAINS 'налог' RETURN a.act_id LIMIT 10",
        ],
    )
    def test_allows_readonly(self, query):
        assert is_safe_readonly_cypher(query) is True, query

    @pytest.mark.parametrize(
        "query",
        [
            "CREATE (n:User {name: 'x'})",
            "MERGE (a:LegalAct {act_id: '1'})",
            "MATCH (n) DELETE n",
            "MATCH (n) DETACH DELETE n",
            "MATCH (n) SET n.x = 1 RETURN n",
            "MATCH (n) DROP CONSTRAINT c",
            "LOAD CSV FROM 'file:///x.csv' AS row RETURN row",
            "MATCH (n) REMOVE n.x RETURN n",
        ],
    )
    def test_blocks_write_ops(self, query):
        assert is_safe_readonly_cypher(query) is False, query

    @pytest.mark.parametrize(
        "query",
        [
            "Конечно! Давайте сгенерируем запрос на основе вашего шага плана.",
            "Извините, но ваша схема графа и правила не содержат информации о полях, связанных с суммами.",
            "В вашей схеме графа нет информации о том, что бы какие-либо узлы содержали данные сумм.",
            "",
            "   ",
        ],
    )
    def test_blocks_nonsense_and_empty(self, query):
        assert is_safe_readonly_cypher(query) is False, query

    def test_blocks_trailing_write_after_read(self):
        assert is_safe_readonly_cypher("MATCH (n) RETURN n SET n.x = 1") is False

    def test_case_insensitive_write(self):
        assert is_safe_readonly_cypher("match (n) create (m) return n") is False
