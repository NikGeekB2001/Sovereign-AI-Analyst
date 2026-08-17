# -*- coding: utf-8 -*-
"""Tool Registry: контракт инструментов Sovereign-AI-Analyst.

Единый источник правды для:
  - манифеста mcp.json (внешние агенты/UI подключаются без чтения кода)
  - эндпоинта GET /api/v1/tools
  - валидации аргументов инструментов (лёгкий валидатор, без зависимостей)

Принципы контракта (см. docs/mcp_design.md):
  - домен.действие (snake_case), обязательный префикс домена
  - JSON Schema входов/выходов
  - scopes (RBAC): role -> набор прав
  - коды ошибок SOV-1xxx..5xxx
"""
from __future__ import annotations

from typing import Any, Dict, List

MANIFEST_NAME = "sovereign-ai-analyst"
MANIFEST_VERSION = "0.1.0"

# --- Каталог инструментов ---
TOOLS: Dict[str, Dict[str, Any]] = {
    "graph.query": {
        "description": "Выполнение read-only Cypher-запроса к Neo4j (LegalAct/Authority/Keyword). "
                       "Разрешены только MATCH/OPTIONAL MATCH/UNWIND/WITH/RETURN/SHOW/CALL.",
        "version": "1.0.0",
        "scopes": ["graph:read"],
        "idempotent": True,
        "inputSchema": {
            "type": "object",
            "required": ["query"],
            "properties": {
                "query": {"type": "string", "description": "Read-only Cypher"},
                "user_role": {"type": "string", "enum": ["куратор", "специалист отдела", "admin"], "default": "куратор"},
            },
        },
        "errorCodes": ["SOV-1001", "SOV-1003", "SOV-2004", "SOV-4003", "SOV-5001"],
    },
    "graph.schema": {
        "description": "Схема графа: метки, типы связей, свойства (для генерации Cypher).",
        "version": "1.0.0",
        "scopes": ["graph:read"],
        "idempotent": True,
        "inputSchema": {"type": "object", "properties": {}},
        "errorCodes": ["SOV-5001"],
    },
    "vector.search": {
        "description": "Семантический поиск по коллекции Qdrant 'ruslawod' с RBAC-фильтром access_level.",
        "version": "1.0.0",
        "scopes": ["vector:read"],
        "idempotent": True,
        "inputSchema": {
            "type": "object",
            "required": ["query"],
            "properties": {
                "query": {"type": "string"},
                "user_role": {"type": "string", "enum": ["куратор", "специалист отдела", "admin"], "default": "куратор"},
                "top_k": {"type": "integer", "minimum": 1, "maximum": 20, "default": 5},
                "min_score": {"type": "number", "default": 0.3},
            },
        },
        "errorCodes": ["SOV-1001", "SOV-1003", "SOV-5002"],
    },
    "vector.rerank": {
        "description": "Реранкинг кандидатов кросс-энкодером (Qwen2.5-7B).",
        "version": "1.0.0",
        "scopes": ["vector:read"],
        "idempotent": True,
        "inputSchema": {
            "type": "object",
            "required": ["query", "documents"],
            "properties": {
                "query": {"type": "string"},
                "documents": {"type": "array", "items": {"type": "object"}},
                "top_k": {"type": "integer", "default": 3},
            },
        },
        "errorCodes": ["SOV-1001", "SOV-1003", "SOV-3002"],
    },
    "vector.upsert": {
        "description": "Запись документов в Qdrant (только для admin; используется загрузчиком).",
        "version": "1.0.0",
        "scopes": ["vector:write"],
        "idempotent": True,
        "inputSchema": {
            "type": "object",
            "required": ["points"],
            "properties": {"points": {"type": "array", "items": {"type": "object"}}},
        },
        "errorCodes": ["SOV-1001", "SOV-1003", "SOV-4001", "SOV-5002"],
    },
    "chat.complete": {
        "description": "Полный ответ агента (тот же конвейер, что POST /chat).",
        "version": "1.0.0",
        "scopes": ["chat:read"],
        "idempotent": False,
        "inputSchema": {
            "type": "object",
            "required": ["message"],
            "properties": {
                "message": {"type": "string"},
                "user_role": {"type": "string", "enum": ["куратор", "специалист отдела", "admin"], "default": "куратор"},
            },
        },
        "errorCodes": ["SOV-1001", "SOV-1003", "SOV-3001", "SOV-3002"],
    },
    "chat.stream": {
        "description": "SSE-поток ответа агента (тот же конвейер, что POST /chat/stream).",
        "version": "1.0.0",
        "scopes": ["chat:read"],
        "idempotent": False,
        "inputSchema": {
            "type": "object",
            "required": ["message"],
            "properties": {
                "message": {"type": "string"},
                "user_role": {"type": "string", "enum": ["куратор", "специалист отдела", "admin"], "default": "куратор"},
            },
        },
        "errorCodes": ["SOV-1001", "SOV-1002", "SOV-1003", "SOV-3001", "SOV-3002"],
    },
}

ERROR_MODEL = {
    "pattern": "SOV-XXXX",
    "ranges": {
        "SOV-1xxx": "Конфигурация/валидация (invalid args, missing scope, unknown tool)",
        "SOV-2xxx": "Инструменты (URL invalid, scrape failed, rate limited, not found)",
        "SOV-3xxx": "LLM/провайдер (timeout, model unavailable, JSON parse fail)",
        "SOV-4xxx": "RBAC/безопасность (role denied, invalid api key, PII redacted, blocked query)",
        "SOV-5xxx": "Инфраструктура (Neo4j/Qdrant/Ollama down)",
    },
    "retryablePolicy": {"backoff_sec": [1, 2, 4, 30], "retry_after_header": True},
}

SCOPES_BY_ROLE = {
    "куратор": ["graph:read", "vector:read", "chat:read"],
    "специалист отдела": ["graph:read", "vector:read", "chat:read", "web:read"],
    "admin": ["graph:read", "vector:read", "vector:write", "chat:read", "web:read", "office:read", "office:write", "admin"],
}


def get_manifest() -> Dict[str, Any]:
    """Полный манифест для mcp.json и GET /api/v1/tools."""
    return {
        "name": MANIFEST_NAME,
        "version": MANIFEST_VERSION,
        "description": "MCP-манифест: аналитик по правовым документам (on-premise, air-gapped). "
                       "Документация контракта: docs/mcp_design.md.",
        "tools": TOOLS,
        "errorModel": ERROR_MODEL,
        "scopesByRole": SCOPES_BY_ROLE,
    }


def tool_names() -> List[str]:
    return sorted(TOOLS)


# --- Лёгкая валидация аргументов (без jsonschema) ---

def _check_value(value: Any, prop_schema: Dict[str, Any], path: str) -> List[str]:
    errs: List[str] = []
    t = prop_schema.get("type")
    if t == "string" and not isinstance(value, str):
        errs.append(f"{path}: ожидается string, получено {type(value).__name__}")
    elif t == "integer" and (not isinstance(value, int) or isinstance(value, bool)):
        errs.append(f"{path}: ожидается integer, получено {type(value).__name__}")
    elif t == "number" and not isinstance(value, (int, float)):
        errs.append(f"{path}: ожидается number, получено {type(value).__name__}")
    elif t == "boolean" and not isinstance(value, bool):
        errs.append(f"{path}: ожидается boolean, получено {type(value).__name__}")
    elif t == "object" and not isinstance(value, dict):
        errs.append(f"{path}: ожидается object, получено {type(value).__name__}")
    elif t == "array" and not isinstance(value, list):
        errs.append(f"{path}: ожидается array, получено {type(value).__name__}")
    enum = prop_schema.get("enum")
    if enum is not None and value not in enum:
        errs.append(f"{path}: значение вне допустимого набора {enum}")
    minimum = prop_schema.get("minimum")
    if isinstance(value, (int, float)) and minimum is not None and value < minimum:
        errs.append(f"{path}: меньше минимума {minimum}")
    maximum = prop_schema.get("maximum")
    if isinstance(value, (int, float)) and maximum is not None and value > maximum:
        errs.append(f"{path}: больше максимума {maximum}")
    return errs


def validate_args(tool_name: str, args: Dict[str, Any] | None) -> List[str]:
    """Возвращает список ошибок валидации (пустой список = валидно)."""
    args = args or {}
    if tool_name not in TOOLS:
        return [f"SOV-1003: неизвестный инструмент '{tool_name}'"]
    schema = TOOLS[tool_name]["inputSchema"]
    props = schema.get("properties", {})
    errs: List[str] = []
    for req in schema.get("required", []):
        if req not in args:
            errs.append(f"SOV-1001: отсутствует обязательный аргумент '{req}'")
    for key, value in args.items():
        if key in props:
            errs.extend(_check_value(value, props[key], f"'{key}'"))
        else:
            errs.append(f"SOV-1001: неизвестный аргумент '{key}'")
    return errs


def scopes_for_role(role: str) -> List[str]:
    return SCOPES_BY_ROLE.get(role, SCOPES_BY_ROLE["куратор"])


if __name__ == "__main__":
    import json
    import sys

    if "--emit-mcp-json" in sys.argv:
        print(json.dumps(get_manifest(), ensure_ascii=False, indent=2))
