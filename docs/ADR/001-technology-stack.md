# ADR-001: Выбор технологического стека (Infrastructure & Stack, 2026, РФ)

## Статус
Принято

## Контекст
Требуется обоснованный выбор стека для On-premise решения (актуальность: 2026, РФ) без доступа к внешним API. Система должна работать в air-gapped среде на потребительском GPU оборудовании, доступном в РФ с учетом санкционных ограничений.

**Ключевые ограничения:**
- Нет доступа к облачным API (OpenAI, Anthropic, AWS, GCP)
- GPU: RTX 3070/4090 (8-24GB VRAM), A100 недоступны из-за санкций
- Лицензии: Apache 2.0 или совместимые для коммерческого использования
- Поддержка русского языка: критична для корпоративных документов

## Решение

| Компонент | Выбор | ADR с детальным Trade-off |
|-----------|-------|---------------------------|
| LLM Serving | **vLLM** (PagedAttention, Continuous Batching, AWQ) | [ADR-003](003-llm-serving-engine.md) |
| Модели | **Qwen2.5-32B-Instruct AWQ** (основная), **T-lite/Saiga-7B** (guardrails) | [ADR-004](004-llm-model-selection.md) |
| Vector DB | **Qdrant** (self-hosted, payload filtering для RBAC) | [ADR-005](005-vector-database.md) |
| Graph DB | **Neo4j Community Edition** (Knowledge Graph, RBAC на узлах) | Настоящий ADR |
| Оркестрация | **LangGraph** (Stateful Agents, циклические графы, линейные запрещены) | [ADR-006](006-orchestration-langgraph.md) |
| Observability | **OpenTelemetry** + **Prometheus/Grafana** + **Langfuse** | [ADR-007](007-observability-stack.md) |
| Frontend | **Gradio** (чат-интерфейс, потоковый вывод) | Настоящий ADR |
| API Gateway | **FastAPI** (Guardrails, JWT, PII фильтрация) | Настоящий ADR |
| State Storage | **PostgreSQL** (AsyncPostgresSaver, Agent State) | Настоящий ADR |
| Long-term Memory | **mem0** + **pgvector** (User Context, Cache) | Настоящий ADR |
| Secrets | **HashiCorp Vault** (динамические секреты, API Keys) | Настоящий ADR |
| Rate Limiting | **slowapi** (per-endpoint limits, IP-based) | Настоящий ADR |
| LLM Fallback | **LLMRegistry** + **tenacity** (Circular Fallback, Retries) | Настоящий ADR |
| Cache | **Valkey/Redis** (Memory Cache, Rate Limit State) | Настоящий ADR |

## Trade-off Analysis по компонентам

### LLM Serving: vLLM vs SGLang vs TGI

| Критерий | vLLM ✅ | SGLang | TGI |
|----------|---------|--------|-----|
| Throughput | 2-4x выше TGI | Быстрее на single-request | Базовый |
| KV-cache | PagedAttention (30-50% экономия) | RadixAttention | Стандартный |
| AWQ Support | Нативная | Ограниченная | Частичная |
| Community | 30k+ stars | 8k stars | 9k stars |
| Production readiness | Высокий | Средний | Высокий |
| Air-gapped | ✅ Легко | ✅ Легко | ⚠️ Зависимость от HF |

**Решение: vLLM** — лучший throughput, зрелость, нативная AWQ поддержка.

### Модели: Qwen 2.5/3 vs DeepSeek-V3 vs Llama 3/4

| Критерий | Qwen 2.5/3 ✅ | DeepSeek-V3 | Llama 3/4 | T-lite/Saiga |
|----------|---------------|-------------|-----------|--------------|
| Русский язык | Отличный | Отличный | Средний | Отличный |
| Лицензия | Apache 2.0 | MIT | Meta License | Apache/MIT |
| Размер | 7B-72B | 671B (MoE) | 8B-405B | 7B-8B |
| Квантование | AWQ/GGUF | Сложно (MoE) | AWQ/GGUF | GGUF |
| VRAM (32B AWQ) | ~20GB | ~2x24GB | ~20GB | ~5-6GB |
| Санкционные риски | Средние | Высокие | Высокие | Низкие |

**Решение:** Qwen2.5-32B AWQ (основная) + T-lite/Saiga-7B (guardrails).

### Vector DB: Qdrant vs Milvus vs Weaviate

| Критерий | Qdrant ✅ | Milvus | Weaviate |
|----------|-----------|--------|----------|
| Payload Filtering | Лучший | Средний | Средний |
| RBAC на чанках | ✅ Нативный | ⚠️ Ограниченный | ⚠️ Ограниченный |
| Развертывание | 1 контейнер | 5+ контейнеров | 1 контейнер |
| RAM (1M vectors) | 4-8GB | 8GB+ | 4-6GB |
| Гибридный поиск | ✅ Sparse+Dense | ✅ | ✅ |
| Air-gapped | ✅ Легко | ⚠️ Сложно | ⚠️ Модули API |
| Latency | <10ms | <20ms | <30ms |

**Решение: Qdrant** — лучший payload filtering для RBAC, простое развертывание.

### Graph DB: Neo4j vs ArangoDB vs JanusGraph

| Критерий | Neo4j CE ✅ | ArangoDB | JanusGraph |
|----------|-------------|----------|------------|
| Cypher Query | ✅ Де-факто стандарт | AQL (свойственный) | Gremlin |
| RBAC на узлах | ✅ | ⚠️ | ⚠️ |
| Community Edition | ✅ Бесплатная | ✅ | ✅ |
| Python Driver | ✅ Официальный | ✅ | ⚠️ |
| LangChain Integration | ✅ Нативная | ⚠️ | ❌ |
| Production Use | Массовый | Средний | Редкий |

**Решение: Neo4j Community Edition** — лучший Cypher, RBAC, интеграция с LangChain.

### Оркестрация: LangGraph vs LlamaIndex Workflows

| Критерий | LangGraph ✅ | LlamaIndex Workflows |
|----------|-------------|---------------------|
| Циклические графы | ✅ Нативно | ✅ Event-driven |
| Persistent State | ✅ PostgreSQL | ❌ Нет встроенного |
| Линейные цепочки | ❌ Запрещены | ⚠️ Возможны |
| Langfuse Integration | ✅ Нативная | ⚠️ Кастомная |
| Community | 10k+ stars | 3k stars |

**Решение: LangGraph** — циклические графы, persistent state, нативная observability.

### Observability: OTel + Prometheus/Grafana + Langfuse

| Критерий | OTel+Prom+Langfuse ✅ | LangSmith Cloud | ELK Stack |
|----------|----------------------|-----------------|-----------|
| Air-gapped | ✅ | ❌ Cloud-only | ✅ |
| LLM Analytics | ✅ Langfuse | ✅ Лучший | ❌ |
| Infrastructure Metrics | ✅ Prometheus | ❌ | ⚠️ |
| Tracing | ✅ OTel | ✅ | ⚠️ |
| RAM Overhead | ~4GB | 0 | ~4GB+ |

**Решение:** OTel + Prometheus/Grafana + Langfuse — полный стек, self-hosted.

### State Persistence: AsyncPostgresSaver vs MemorySaver vs RedisSaver

| Критерий | AsyncPostgresSaver ✅ | MemorySaver | RedisSaver |
|----------|----------------------|-------------|------------|
| Persistence | ✅ PostgreSQL (WAL) | ❌ In-memory | ✅ Redis |
| Async | ✅ AsyncConnectionPool | ❌ Sync | ⚠️ |
| Production | ✅ HA, Replication | ❌ Lost on restart | ⚠️ |
| Air-gapped | ✅ | ✅ | ✅ |
| Complexity | Средняя | Низкая | Средняя |

**Решение:** AsyncPostgresSaver — production-ready, async, persistent.

### LLM Fallback: LLMRegistry + tenacity vs Single Model

| Критерий | LLMRegistry + tenacity ✅ | Single Model |
|----------|--------------------------|--------------|
| Resilience | ✅ Circular Fallback | ❌ Single point of failure |
| Retries | ✅ Exponential backoff | ⚠️ Manual |
| Structured Output | ✅ Pydantic schema | ⚠️ Manual parsing |
| Timeout Budget | ✅ 60s total | ❌ No budget |
| Complexity | Средняя | Низкая |

**Решение:** LLMRegistry + tenacity — resilience при отказе моделей.

### Rate Limiting: slowapi vs FastAPI-limiter vs Custom

| Критерий | slowapi ✅ | FastAPI-limiter | Custom |
|----------|-----------|-----------------|--------|
| Per-endpoint | ✅ | ✅ | ⚠️ |
| IP-based | ✅ | ✅ | ⚠️ |
| Redis Backend | ✅ | ✅ | ❌ |
| Integration | ✅ Starlette | ⚠️ | ❌ |
| Complexity | Низкая | Средняя | Высокая |

**Решение:** slowapi — простая интеграция с FastAPI, per-endpoint limits.

### Long-term Memory: mem0 + pgvector vs Custom vs None

| Критерий | mem0 + pgvector ✅ | Custom | None |
|----------|-------------------|--------|------|
| User Context | ✅ Automatic | ⚠️ Manual | ❌ |
| Embeddings | ✅ pgvector | ⚠️ Separate | ❌ |
| Cache Layer | ✅ Valkey/Redis | ⚠️ | ❌ |
| Complexity | Средняя | Высокая | — |

**Решение:** mem0 + pgvector — автоматическое управление пользовательским контекстом.

## Итоговая архитектура стека

```
┌─────────────────────────────────────────────────┐
│ DMZ                                              │
│  ┌─────────────────────────────────────────┐     │
│  │ Nginx / Load Balancer (Reverse Proxy)   │     │
│  └────────────────┬────────────────────────┘     │
└───────────────────┼──────────────────────────────┘
                    │ HTTPS (TLS 1.3)
┌───────────────────┼──────────────────────────────┐
│ Control Plane     │                              │
│  ┌────────────────▼────────────────────────┐     │
│  │ Gradio UI (Python/Gradio)               │     │
│  └────────────────┬────────────────────────┘     │
│  ┌────────────────▼────────────────────────┐     │
│  │ FastAPI Gateway + Guardrails            │     │
│  │ (PII, Prompt Injection, JWT Auth)       │     │
│  └────────────────┬────────────────────────┘     │
│  ┌────────────────▼────────────────────────┐     │
│  │ Multi-Agent Orchestrator (LangGraph)    │     │
│  │ Plan-Observe-Act, State Management      │     │
│  └────────────────┬────────────────────────┘     │
│  ┌────────────────▼────────────────────────┐     │
│  │ PostgreSQL Checkpointer (Agent State)   │     │
│  └─────────────────────────────────────────┘     │
└───────────────────┬──────────────────────────────┘
                    │
┌───────────────────┼──────────────────────────────┐
│ Data Plane (GPU)  │                              │
│  ┌────────────────▼────────────────────────┐     │
│  │ vLLM Load Balancer                      │     │
│  └───┬─────────────────────────┬──────────┘     │
│  ┌───▼──────────┐  ┌──────────▼──────────┐      │
│  │ vLLM GPU 0   │  │ vLLM GPU 1          │      │
│  │ Qwen2.5-32B  │  │ Qwen2.5-32B AWQ     │      │
│  │ AWQ          │  │                     │      │
│  └──────────────┘  └─────────────────────┘      │
│  ┌─────────────────────────────────────────┐     │
│  │ Qdrant (Vector DB + RBAC payload)       │     │
│  └─────────────────────────────────────────┘     │
│  ┌─────────────────────────────────────────┐     │
│  │ Neo4j (Graph DB + RBAC на узлах)        │     │
│  └─────────────────────────────────────────┘     │
└─────────────────────────────────────────────────┘

┌─────────────────────────────────────────────────┐
│ Observability                                    │
│  OTel Collector → Prometheus → Grafana          │
│  OTel Collector → Langfuse (LLM Analytics)      │
└─────────────────────────────────────────────────┘

┌─────────────────────────────────────────────────┐
│ Security                                         │
│  HashiCorp Vault (Secrets, API Keys, DB Creds)  │
└─────────────────────────────────────────────────┘
```

## Последствия

### Положительные
- ✅ Полностью суверенный стек (on-premise, air-gapped, open-source)
- ✅ Актуален для 2026 (РФ): доступное GPU, нет санкционных зависимостей
- ✅ Каждый компонент обоснован Trade-off анализом
- ✅ Масштабируемость: от 1x RTX 4090 до кластера A100
- ✅ Production-ready: AsyncPostgresSaver, Circular Fallback, Rate Limiting, SSE Streaming
- ✅ Long-term Memory (mem0) для персонализации ответов

### Отрицательные
- ⚠️ Сложность: 12+ сервисов для развертывания и поддержки
- ⚠️ Требуется GPU с >=20GB VRAM для 32B модели (AWQ)
- ⚠️ Команда должна освоить LangGraph, Qdrant, Neo4j, OTel, mem0
- ⚠️ Valkey/Redis для Rate Limiting и Cache (дополнительная инфраструктура)

### Нейтральные
- Компоненты можно заменять независимо (микросервисная архитектура)
- Модели можно обновлять без изменения инфраструктуры

## Ссылки на детальные ADR
- [ADR-003: LLM Serving Engine](003-llm-serving-engine.md)
- [ADR-004: LLM Model Selection](004-llm-model-selection.md)
- [ADR-005: Vector Database](005-vector-database.md)
- [ADR-006: Orchestration (LangGraph)](006-orchestration-langgraph.md)
- [ADR-007: Observability Stack](007-observability-stack.md)
- [ADR-002: Security-by-Design](002-security-by-design.md)