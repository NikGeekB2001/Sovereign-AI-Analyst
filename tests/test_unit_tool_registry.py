# -*- coding: utf-8 -*-
"""Unit-тесты Tool Registry: манифест, валидация аргументов, scopes."""
import pytest

from backend.services.tool_registry import (
    SCOPES_BY_ROLE,
    TOOLS,
    get_manifest,
    scopes_for_role,
    tool_names,
    validate_args,
)


class TestManifest:
    def test_manifest_structure(self):
        m = get_manifest()
        assert m["name"] == "sovereign-ai-analyst"
        assert m["version"]
        assert set(m["tools"]) == set(TOOLS)
        assert m["errorModel"]["pattern"] == "SOV-XXXX"

    def test_required_tools_present(self):
        names = set(tool_names())
        for expected in [
            "graph.query",
            "graph.schema",
            "vector.search",
            "vector.rerank",
            "vector.upsert",
            "chat.complete",
            "chat.stream",
        ]:
            assert expected in names, expected

    def test_every_tool_has_contract_fields(self):
        for name, spec in TOOLS.items():
            assert spec["description"], name
            assert spec["version"], name
            assert spec["scopes"], name
            assert "inputSchema" in spec, name
            assert spec["errorCodes"], name
            assert "." in name, f"имя без домена: {name}"

    def test_scopes_by_role_hierarchy(self):
        junior = set(SCOPES_BY_ROLE["junior"])
        senior = set(SCOPES_BY_ROLE["senior"])
        admin = set(SCOPES_BY_ROLE["admin"])
        assert junior <= senior <= admin

    def test_scopes_for_role_default(self):
        assert scopes_for_role("unknown_role") == SCOPES_BY_ROLE["junior"]


class TestValidateArgs:
    def test_valid_query(self):
        assert validate_args("graph.query", {"query": "MATCH (n) RETURN n"}) == []

    def test_missing_required(self):
        errs = validate_args("graph.query", {})
        assert errs and "SOV-1001" in errs[0]

    def test_unknown_tool(self):
        errs = validate_args("nope.nope", {"a": 1})
        assert errs and "SOV-1003" in errs[0]

    def test_unknown_argument(self):
        errs = validate_args("graph.query", {"query": "MATCH (n) RETURN n", "bogus": 1})
        assert any("SOV-1001" in e and "bogus" in e for e in errs)

    def test_enum_validation(self):
        errs = validate_args("vector.search", {"query": "x", "user_role": "hacker"})
        assert any("user_role" in e and "набор" in e for e in errs)

    def test_type_validation(self):
        errs = validate_args("vector.search", {"query": "x", "top_k": "many"})
        assert any("top_k" in e for e in errs)

    def test_bounds_validation(self):
        errs = validate_args("vector.search", {"query": "x", "top_k": 100})
        assert any("top_k" in e and "максимума" in e for e in errs)

    def test_none_args(self):
        assert validate_args("graph.schema", None) == []
