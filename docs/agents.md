# Агенты Sovereign-AI-Analyst: состав и взаимодействие

Пайплайн реализован на **LangGraph** (`backend/agents/multi_agent_graph.py`)
по паттерну **Plan-Observe-Act**: планировщик → исполнитель → проверка →
при необходимости цикл → синтез ответа. Ниже — имена агентов (узлов графа),
их обязанности и маршрутизация. Визуализация: `docs/diagrams/agents_flow.puml`
(PlantUML) и `docs/diagrams/agents_flow.svg` (готовый рендер).

## Состав агентов (7 узлов)

| # | Агент (узел) | Роль | Вход → Выход |
|---|---|---|---|
| 1 | `input_guardrail` | Входная проверка: PII, prompt injection, RBAC-роль | запрос пользователя → разрешён (`planner`) или заблокирован (`END`) |
| 2 | `planner` | Составляет план шагов анализа (Plan-Observe-Act) | защищённый запрос → план шагов |
| 3 | `graph_query_planner` | Генерирует **read-only Cypher** под текущий шаг плана | шаг плана → Cypher-запрос |
| 4 | `tool_executor` | Исполняет инструменты: Cypher → Neo4j (через `cypher_guard`); при невалидном запросе — RAG-fallback: `retriever` (Qdrant + bge-m3) → `reranker` (Qwen2.5-7B) с RBAC-фильтром `access_level` | Cypher/вопрос → факты из графа и/или документы |
| 5 | `security_guard` | Проверка промежуточного вывода: PII, RBAC-утечки, секрет-маркеры | факты → чистые факты; цикл на следующий шаг или к синтезу |
| 6 | `synthesize` | Формирует финальный ответ из накопленного контекста | чистые факты → ответ |
| 7 | `output_guardrail` | Финальная проверка ответа (PII, RBAC) | ответ → ответ пользователю (`END`) |

## Взаимодействие (рёбра графа)

```
entry ──▶ input_guardrail
             │ route_after_guardrail
             ├─ blocked? ──▶ END
             └─ ok ──▶ planner
                        │ decide_next_step
                        ├─ шаги кончились ──▶ synthesize
                        └─ есть шаг ──▶ graph_query_planner
                                          │ (фиксированное ребро)
                                          ▼
                                     tool_executor
                                          │
                                          ▼
                                     security_guard
                                          │ decide_next_step (Plan-Observe-Act loop)
                                          ├─ шаги остались ──▶ graph_query_planner (цикл)
                                          └─ шаги кончились ──▶ synthesize
                                                                  │
                                                                  ▼
                                                             output_guardrail ──▶ END
```

Ключевые маршрутизаторы:

- `route_after_guardrail(state)`: `state["blocked"] == true` → `END`, иначе → `planner`.
- `decide_next_step(state)`: если `current_step_idx >= len(plan)` → `synthesize`,
  иначе → `graph_query_planner` (используется и после `planner`, и после
  `security_guard` — так реализован цикл Plan-Observe-Act).

## Данные между агентами

- `user_query` — исходный запрос (передаётся по цепочке).
- `plan` + `current_step_idx` — план шагов и указатель текущего шага
  (управляет циклом).
- `generated_query` — Cypher от `graph_query_planner`; валидируется
  `cypher_guard` в `tool_executor` (только read-only, иначе RAG-fallback).
- `retrieved_context` / факты — результат `tool_executor` (узлы Neo4j или
  документы Qdrant, отфильтрованные по роли через `access_level`).
- `response` — итоговый ответ после `synthesize`.

## RBAC по ролям

| Роль | Scopes | Видимость документов (access_level) |
|---|---|---|
| `куратор` | graph:read, vector:read, chat:read | public |
| `специалист отдела` | + web:read | public + internal |
| `admin` | + vector:write, office:*, chart:*, admin | все уровни |

Полный контракт инструментов и ошибок SOV-* — в `docs/mcp_design.md`.
Метрики качества работы агентов (RAGAS-стиль) — в `docs/PERFORMANCE_METRICS.md`.
