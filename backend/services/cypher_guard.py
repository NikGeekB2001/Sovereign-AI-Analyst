# -*- coding: utf-8 -*-
"""Валидация Cypher: только read-only запросы.

Защита от LLM-галлюцинаций: LLM может сгенерировать не-Cypher текст
или write-запрос (CREATE/MERGE/DELETE). Этот модуль — единственная
точка валидации, используется tool_executor и покрыта unit-тестами.
"""
import re

# Убирает обрамление ```cypher ... ```
FENCE_RE = re.compile(r'^```(?:cypher)?\s*|\s*```$', re.IGNORECASE)

# Разрешённые начальные ключевые слова (read-only)
READONLY_START_RE = re.compile(
    r'^(MATCH|OPTIONAL\s+MATCH|UNWIND|WITH|RETURN|SHOW|CALL)\b',
    re.IGNORECASE | re.DOTALL,
)

# Запрещённые write-операции в любом месте запроса
WRITE_OPS_RE = re.compile(
    r'\b(CREATE|MERGE|DELETE|SET|DROP|REMOVE|DETACH|LOAD CSV)\b',
    re.IGNORECASE | re.DOTALL,
)


def clean_cypher(query: str) -> str:
    """Очищает запрос от markdown-обёртки и пробелов."""
    q = (query or "").strip()
    return FENCE_RE.sub("", q).strip()


def is_safe_readonly_cypher(query: str) -> bool:
    """True, если запрос — валидный read-only Cypher (или пустой → False)."""
    q = clean_cypher(query)
    if not q:
        return False
    if not READONLY_START_RE.match(q):
        return False
    if WRITE_OPS_RE.search(q):
        return False
    return True
