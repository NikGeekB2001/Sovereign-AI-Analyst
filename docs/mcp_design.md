# Sovereign-AI-Analyst: MCP-контракт и дизайн API-поверхности

> **Статус:** черновик v0.1 · **Дата:** 2026-08-16 · **Автор:** по итогам живого E2E-прогона
> **Метод:** contract-first — сначала контракт, потом реализация (как проектируют API аналитики и архитекторы).
> **Миссия проекта:** sovereign AI-аналитик для правовых документов (on-premise, air-gapped).

---

## 1. Контекст и принципы

Проект сегодня: LangGraph-граф (planner → graph_query → retrieve → synthesize → guardrails), FastAPI (SSE), Qdrant + Neo4j, Ollama/vLLM, Langfuse. Доказано E2E: конвейер отвечает на вопросы, RBAC-фильтр работает, стриминг работает.

**Проблема:** расширение через «ещё один инструмент в графе» без контракта. Каждый новый инструмент (Firecrawl, Office, Charts из плана v2) — это API: имя, схема входов/выходов, ошибки, права, метрики. Без контракта поверхность растёт хаотично, внешние агенты и UI не могут подключиться без чтения кода.

**Принципы:**
1. **Contract-first** — JSON Schema для каждого инструмента до реализации.
2. **Least privilege** — каждый инструмент получает scope; роль пользователя маппится в scopes.
3. **Read-only by default** — write-инструменты явно помечаются и требуют scope `*:write`.
4. **Идемпотентность** — повторный вызов с тем же ключом не меняет состояние.
5. **Единая модель ошибок** — коды, retryable, backoff; никаких сырых исключений наружу.
6. **Версии и совместимость** — `deprecated_since`, период поддержки.
7. **Всё наблюдаемо** — каждый tool_call трейсится (tokens, latency, cache_hit, error).

---

## 2. Двойная роль MCP

Система работает в двух режимах одновременно:

| Режим | Роль | Потребители | Пример |
|---|---|---|---|
| **MCP-клиент** | потребляет внешние MCP-серверы | сам агент (LangGraph tools) | Firecrawl (search/scrape/crawl), filesystem |
| **MCP-сервер** | предоставляет свои инструменты | UI, внешние агенты, ноутбуки | graph.query, vector.search, office.read |

Архитектурно это два независимых контура:

```
┌─────────────────────────────┐     stdio/HTTP      ┌──────────────────┐
│  LangGraph Agent            │ ──────────────────▶ │  Firecrawl MCP   │
│  (MCP-клиент, tools)        │                     │  Filesystem MCP  │
└─────────────┬───────────────┘                     └──────────────────┘
              │ внутренние вызовы (Python API)
┌─────────────▼───────────────┐
│  Tool Registry (контракт)   │  ← mcp.json + JSON Schema
└─────────────┬───────────────┘
              │ SSE / HTTP
┌─────────────▼───────────────┐
│  FastAPI /api/v1            │  ← Streamlit UI, внешние агенты
└─────────────────────────────┘
```

**Решение для air-gapped:** внешние MCP-серверы (Firecrawl, cloud LLM fallback) — только если явно включены; по умолчанию контур закрыт (`ALLOW_EXTERNAL=false`).

---

## 3. Каталог инструментов (контракт)

Каждый инструмент определяется полем `mcp.json`:

```json
{
  "name": "web.scrape",
  "description": "Извлечение чистого Markdown со страницы (Firecrawl)",
  "version": "1.0.0",
  "deprecated_since": null,
  "scopes": ["web:read"],
  "idempotent": true,
  "inputSchema": {
    "type": "object",
    "required": ["url"],
    "properties": {
      "url":        {"type": "string", "format": "uri"},
      "max_tokens": {"type": "integer", "default": 8000, "maximum": 32000},
      "cache":      {"type": "boolean", "default": true}
    }
  },
  "outputSchema": {
    "type": "object",
    "properties": {
      "markdown": {"type": "string"},
      "tokens":   {"type": "integer"},
      "source":   {"enum": ["fresh", "cache"]}
    }
  },
  "errorCodes": ["SOV-2001", "SOV-2002", "SOV-2003"]
}
```

### 3.1 Существующие (уже работают, обернуть в контракт)

| Инструмент | Описание | Scope | Статус |
|---|---|---|---|
| `graph.query` | Выполнение read-only Cypher | `graph:read` | есть (guard уже блокирует write) |
| `graph.schema` | Схема графа (labels, rels, props) | `graph:read` | частично (fallback-промпты) |
| `vector.search` | Семантический поиск + фильтр RBAC | `vector:read` | есть (retriever) |
| `vector.rerank` | Реранкинг кандидатов | `vector:read` | есть |
| `vector.upsert` | Запись документов в Qdrant | `vector:write` | есть (загрузчик) |
| `chat.complete` | Полный ответ агента | `chat:read` | есть (/chat) |
| `chat.stream` | SSE-поток ответа | `chat:read` | есть (/chat/stream) |

### 3.2 Планируемые (план v2)

| Инструмент | Описание | Scope | Оценка |
|---|---|---|---|
| `web.search` | Поиск страниц (Firecrawl search) | `web:read` | 4 ч |
| `web.scrape` | URL → Markdown (−50% токенов) | `web:read` | 2 ч |
| `web.crawl` | Рекурсивный обход (нужен API-ключ) | `web:read` | 6 ч |
| `web.map` | Карта сайта | `web:read` | 2 ч |
| `office.xlsx.read` | Схема+превью Excel (не весь DataFrame) | `office:read` | 4 ч |
| `office.docx.read` | Текст/структура Word | `office:read` | 3 ч |
| `office.write` | Запись/мерж документов | `office:write` | 8 ч |
| `chart.generate` | График (matplotlib/plotly) | `chart:read` | 4 ч |
| `chart.dashboard` | Дашборд | `chart:read` | 6 ч |
| `admin.config.get` | Текущий конфиг | `admin` | 1 ч |
| `admin.cache.clear` | Очистка response_cache | `admin` | 1 ч |
| `admin.index.rebuild` | Пересборка Qdrant/Neo4j | `admin` | 3 ч |

### 3.3 Конвенции именования

- `домен.действие` — snake_case, обязательный префикс домена.
- Домены: `graph`, `vector`, `web`, `office`, `chart`, `chat`, `admin`.
- Аргументы: camelCase запрещён; `max_tokens`, `user_role`, `top_k` (уже так в коде).

---

## 4. Онтология-контракт (данные)

Контракт схемы данных — то, что раньше было «молчаливой» договорённостью загрузчика и ретривера (и расходилось — из-за этого система не отвечала до фиксов).

**Neo4j:**
```
(:LegalAct {act_id, title, doc_type, doc_number, date, status, signed_by, is_widely_used, access_level, classifier})
(:Authority {name})
(:Keyword {value})
(:LegalAct)-[:ISSUED_BY]->(:Authority)
(:LegalAct)-[:HAS_KEYWORD]->(:Keyword)
(:LegalAct)-[:REFERENCES]->(:LegalAct)
```

**Qdrant `ruslawod`:** vectors 1024d (bge-m3), payload `{act_id, title, status, authority, text_preview, access_level}`.

**RBAC-поле `access_level`:** `public | internal | restricted` — обязательное на узлах и в payload (этот баг был исправлен в E2E: отсутствие поля = ретривер всегда пуст).

**Правило синхронизации:** любое изменение схемы — сначала этот документ, потом загрузчик, потом ретривер. Добавить тест «загрузчик → Neo4j/Qdrant → выборка по роли» как CI-гейт.

---

## 5. Модель ошибок

Единый формат:

```json
{
  "error": {
    "code": "SOV-2003",
    "message": "Firecrawl rate limit (50 req/h noauth)",
    "retryable": true,
    "retry_after_sec": 3600,
    "details": {"tool": "web.scrape", "url": "https://..."}
  }
}
```

| Диапазон | Домен | Примеры |
|---|---|---|
| SOV-1xxx | Конфигурация/валидация | SOV-1001 invalid args · SOV-1002 missing scope · SOV-1003 unknown tool |
| SOV-2xxx | Инструменты | SOV-2001 URL invalid · SOV-2002 scrape failed · SOV-2003 rate limited · SOV-2004 not found |
| SOV-3xxx | LLM/провайдер | SOV-3001 timeout · SOV-3002 model unavailable · SOV-3003 JSON parse fail |
| SOV-4xxx | RBAC/безопасность | SOV-4001 role denied · SOV-4002 PII redacted · SOV-4003 blocked query |
| SOV-5xxx | Инфраструктура | SOV-5001 Neo4j down · SOV-5002 Qdrant down · SOV-5003 Ollama down |

Политика: `retryable=true` → экспоненциальный backoff (1с, 2с, 4с, max 30с) + `Retry-After`; не-retryable ошибки не повторяются автоматически. Наружу (в SSE/HTTP) сырые исключения не выходят никогда — только SOV-формат.

---

## 6. Безопасность

### 6.1 Аутентификация (сегодня — демо, минимум — завтра)

| Уровень | Механизм | Срок |
|---|---|---|
| 0 (сейчас) | роль в теле запроса | — |
| 1 | `X-API-Key` → роль (маппинг в env/БД) | неделя 2 |
| 2 | JWT (access + refresh), роли в claims | неделя 3–4 |

Правило: **роль никогда не приходит от клиента** — только из сессии/ключа.

### 6.2 Scopes → роли

| Роль | Scopes |
|---|---|
| junior | `graph:read, vector:read, web:read(без crawl), chat:read` |
| senior | + `web:crawl, office:read, chart:read` |
| admin | + `office:write, vector:write, admin` |

### 6.3 Инструментальный sandbox

- **Cypher:** read-only whitelist (MATCH/OPTIONAL MATCH/UNWIND/WITH/RETURN/SHOW/CALL) + blacklist write — уже реализовано в E2E-фиксе.
- **Файлы (filesystem MCP):** только allowlist-каталоги, `../` запрещён, размер файла ≤ N МБ.
- **Web:** crawl только с явным scope; noauth-лимит 50 req/ч — кэш URL→markdown в Qdrant.
- **Office write:** отдельный scope, журнал изменений (кто/что/когда).

### 6.4 Audit log

Каждый вызов write-инструмента и каждый отказ RBAC пишется в audit-лог (JSON lines): `ts, user, role, tool, args_hash, decision(allow/deny), code`.

---

## 7. Контракт стриминга (SSE)

Формализация существующего потока. Текущий формат: `data: {"node": ..., "content": ...}` + терминатор `data: [DONE]`.

**Целевой формат событий:**

| Тип | Поля | Назначение |
|---|---|---|
| `node_start` | node, step, ts | начало узла графа |
| `node_end` | node, output_summary, tokens, latency_ms | завершение узла |
| `tool_call` | tool, args_hash, tokens, latency_ms, cache_hit | вызов инструмента |
| `tool_result` | tool, status, error_code?, tokens | результат инструмента |
| `error` | code, message, retryable | ошибка в потоке (без обрыва) |
| `done` | response_id, tokens_total, sources, latency_total | терминальное событие |

Требования: heartbeat `: ping` каждые 15с (для прокси/таймаутов), терминальное `done` с метриками, ошибки — событием, а не разрывом соединения.

---

## 8. Наблюдаемость (observability-контракт)

- Каждый `tool_call` → span Langfuse с обязательными атрибутами: `tool, args_hash, tokens, latency_ms, cache_hit, error_code, user_role`.
- Prometheus: `tool_calls_total{tool,status}`, `llm_tokens_total{provider,model}`, `request_latency_seconds{endpoint}`, `cache_hit_ratio`.
- **Alert-правило из плана v2:** `tokens_per_request` среднее ≤ 5 500 (цель) — alert при превышении 8 000.
- Стоимость/лимиты: `firecrawl_requests_total`, `llm_calls_total` для контроля noauth-лимитов.

---

## 9. Версии и манифест

- API: `/api/v1/chat`, `/api/v1/tools` (новые); `/chat` остаётся как legacy без изменений.
- Инструменты: поле `version` + `deprecated_since`; удаление через 2 версии.
- `mcp.json` в корне репо — манифест (инструменты + схемы + scopes): внешние агенты и UI подключаются по манифесту, без чтения кода.
- OpenAPI: сгенерировать из FastAPI (`/api/v1/openapi.json`) как единый источник правды для REST-части.

---

## 10. План внедрения (фазы)

| Фаза | Содержание | Оценка | Критерий готовности |
|---|---|---|---|
| 0 | Тесты+CI (загрузчик→БД, retriever, py_compile, GH Actions) | 2 дня | `CI зелёный`, регрессия ловится |
| 1 | Tool Registry + mcp.json + обёртки существующих инструментов | 2 дня | `/api/v1/tools` отдаёт манифест, graph.query через контракт |
| 2 | API-ключ → роль (уровень 1) | 1 день | роль из ключа, не из тела |
| 3 | SSE-контракт (события node_start/…/done) | 1 день | UI показывает метрики; done с response_id |
| 4 | web.* (Firecrawl, кэш, лимиты) | 2–3 дня | scrape со счётчиком лимита, кэш в Qdrant |
| 5 | office.* + chart.* | 3–4 дня | read/write/merge с audit-логом |
| 6 | JWT + scopes полный | 2 дня | deny-тесты для каждой роли |

Итого: ~2 недели до полного контура. Параллельно с планом v2 (Firecrawl/Office) — контракт делает эти фичи стабильными, а не «ещё одним инструментом».

---

## 11. Что ломать нельзя (инварианты)

1. `/chat` и `/chat/stream` — работающий контракт (200 + SSE). Любое изменение поверхностей — обратно совместимо.
2. Read-only Cypher guard — никогда не снимать.
3. `access_level` — обязателен на узлах и в payload; тест на RBAC-фильтр в CI.
4. Ответ на русском — в промптах явное требование (7b иногда уходит в мультиязычность).
5. `.env` — единственное место для секретов; в коде и git — никогда.
