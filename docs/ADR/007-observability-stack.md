# ADR-007: Observability Stack (OpenTelemetry + Prometheus/Grafana + Langfuse)

## Статус
Принято

## Контекст
Необходима полная наблюдаемость (observability) за мультиагентной AI-системой:
- **Tracing:** Трассировка каждого запроса от входа до ответа (User → Guardrails → Agent Loop → Response)
- **Metrics:** Метрики токенов/сек, latency, GPU utilization, RBAC filter rate
- **LLM Analytics:** Аналитика промптов, cost tracking, quality monitoring
- **Alerting:** Алерты при аномалиях (high latency, injection attempts, RBAC violations)
- **Self-hosted:** Все компоненты развернуты on-premise, air-gapped

## Решение
Комбинированный observability stack из трех слоев:

### Слой 1: OpenTelemetry (Distributed Tracing)
- **OTel Collector** — центральный приемник трейсов, метрик и логов
- **Инструментация:** Python SDK для FastAPI, LangGraph, vLLM
- **Протокол:** OTLP (OpenTelemetry Protocol)

### Слой 2: Prometheus + Grafana (Metrics & Dashboards)
- **Prometheus** — сбор и хранение time-series метрик
- **Grafana** — визуализация, дашборды, алерты
- **Метрики:** tokens/sec, latency p50/p95/p99, GPU utilization, RBAC filter rate

### Слой 3: Langfuse (LLM Observability)
- **Langfuse** — специализированный LLM observability платформ
- **Возможности:** Prompt analytics, cost tracking, session replay, quality scoring
- **Self-hosted:** Docker развертывание, PostgreSQL backend

## Обоснование (Trade-off Analysis)

### Почему OpenTelemetry?

**Преимущества:**
1. **Стандарт индустрии:** CNCF проект, единый протокол для traces/metrics/logs
2. **Vendor-neutral:** Не привязан к конкретному backend (Prometheus, Jaeger, Grafana Tempo)
3. **Автоинструментация:** Python SDK с автоматической оберткой для FastAPI, requests
4. **Контекст распространения:** Trace context автоматически передается между сервисами
5. **OTLP Protocol:** Эффективный бинарный протокол, минимальный overhead

**Недостатки:**
1. **Сложность настройки:** Требует конфигурации Collector, экспортеров, пайплайнов
2. **Overhead:** ~1-3% на instrumentation (приемлемо для production)

### Почему Prometheus + Grafana?

**Преимущества:**
1. **Prometheus:** De-facto стандарт для метрик в Kubernetes/Docker экосистеме
2. **Pull model:** Prometheus сам забирает метрики с endpoints (push gateway для batch)
3. **PromQL:** Мощный язык запросов для агрегации и аналитики
4. **Grafana:** Лучший инструмент визуализации, 100+ готовых дашбордов
5. **Alerting:** Grafana Alerting + Alertmanager для уведомлений

**Недостатки:**
1. **Не для tracing:** Prometheus — только метрики, не трейсы
2. **Storage:** Долгосрочное хранение требует Thanos/Cortex (дополнительная сложность)
3. **Pull model:** Не подходит для short-lived процессов (решается push gateway)

### Почему Langfuse?

**Преимущества:**
1. **LLM-specific:** Специализирован для AI/LLM — понимает prompts, completions, tokens
2. **Prompt Analytics:** Отслеживание версий промптов, A/B тестирование
3. **Cost Tracking:** Автоматический расчет стоимости по токенам и модели
4. **Session Replay:** Воспроизведение сессии пользователя для отладки
5. **Quality Scoring:** Оценка качества ответов (hallucination detection)
6. **Self-hosted:** Open-source, Docker развертывание, PostgreSQL backend
7. **Интеграция с LangGraph:** Нативная поддержка через callback handler

**Недостатки:**
1. **Дополнительный сервис:** PostgreSQL + Langfuse container
2. **Не заменяет OTel:** Langfuse фокусируется на LLM, не на инфраструктурных метриках
3. **Молодой проект:** Меньше production deployments, чем у Prometheus/Grafana

### Почему не Jaeger?

**Преимущества Jaeger:**
- CNCF проект, распределенный tracing
- Хорошая интеграция с OpenTelemetry

**Недостатки для нашего случая:**
1. **Только tracing:** Нет метрик, нет LLM-специфичной аналитики
2. **Избыточность:** OTel Collector + Grafana Tempo покрывают потребности в tracing
3. **Дополнительный сервис:** Еще один контейнер для поддержки

### Почему не ELK Stack?

**Преимущества ELK:**
- Полный стек: Elasticsearch + Logstash + Kibana
- Мощный поиск по логам

**Недостатки для нашего случая:**
1. **Тяжелый:** Минимум 4GB RAM для Elasticsearch
2. **Не для metrics:** Log-based, не time-series
3. **Нет LLM analytics:** Не понимает промпты и токены
4. **Избыточность:** OpenTelemetry + Grafana покрывают потребности

### Почему не LangSmith (cloud)?

**Преимущества LangSmith:**
- Лучший интеграция с LangChain/LangGraph
- Managed service, нет инфраструктуры

**Недостатки для нашего случая:**
1. **Cloud-only:** Нарушает требование air-gapped
2. **Данные уходят наружу:** Утечка промптов и ответов
3. **Стоимость:** $39/seat/month для team plan

## Architecture

### Data Flow
```
FastAPI ──┐
           ├──→ OTel Collector ──→ Prometheus ──→ Grafana (Metrics)
LangGraph ─┤         │
           │         └──→ Langfuse (LLM Traces)
vLLM ─────┘
```

### Метрики (Prometheus)

#### LLM Metrics
| Метрика | Тип | Описание |
|---------|-----|----------|
| `llm_tokens_generated_total` | Counter | Всего сгенерированных токенов |
| `llm_tokens_per_second` | Gauge | Токенов в секунду (throughput) |
| `llm_request_duration_seconds` | Histogram | Latency (p50, p95, p99) |
| `llm_request_total` | Counter | Всего запросов к LLM |
| `llm_request_errors_total` | Counter | Ошибки инференса |

#### GPU Metrics
| Метрика | Тип | Описание |
|---------|-----|----------|
| `gpu_utilization_percent` | Gauge | % использования GPU |
| `gpu_memory_used_bytes` | Gauge | Использованная VRAM |
| `gpu_memory_total_bytes` | Gauge | Общая VRAM |
| `gpu_temperature_celsius` | Gauge | Температура GPU |

#### RBAC Metrics
| Метрика | Тип | Описание |
|---------|-----|----------|
| `rbac_filter_rate_percent` | Gauge | % чанков, отфильтрованных RBAC |
| `rbac_denied_total` | Counter | Всего отказов в доступе |
| `guardrails_pii_detected_total` | Counter | PII обнаружено |
| `guardrails_injection_blocked_total` | Counter | Prompt Injection заблокировано |

#### Agent Metrics
| Метрика | Тип | Описание |
|---------|-----|----------|
| `agent_step_duration_seconds` | Histogram | Время шага агента |
| `agent_iterations_total` | Counter | Итерации агента (Plan-Observe-Act) |
| `agent_replan_total` | Counter | Количество replan циклов |

#### LLM Fallback Metrics
| Метрика | Тип | Описание |
|---------|-----|----------|
| `llm_fallback_total` | Counter | Количество переключений на fallback модель |
| `llm_fallback_duration_seconds` | Histogram | Время переключения на fallback |
| `llm_retry_total` | Counter | Количество повторных попыток (tenacity) |
| `llm_timeout_total` | Counter | Количество таймаутов LLM |

#### Rate Limiting Metrics
| Метрика | Тип | Описание |
|---------|-----|----------|
| `rate_limit_requests_total` | Counter | Всего запросов через rate limiter |
| `rate_limit_violations_total` | Counter | Количество нарушений rate limit |
| `rate_limit_violations_by_endpoint` | Counter | Нарушения по endpoint |

#### Cache Metrics
| Метрика | Тип | Описание |
|---------|-----|----------|
| `cache_hits_total` | Counter | Попаданий в кэш |
| `cache_misses_total` | Counter | Промахов кэша |
| `cache_hit_rate_percent` | Gauge | % попаданий в кэш |

#### Long-term Memory Metrics
| Метрика | Тип | Описание |
|---------|-----|----------|
| `memory_search_duration_seconds` | Histogram | Время поиска в long-term memory |
| `memory_add_duration_seconds` | Histogram | Время добавления в memory |
| `memory_cache_hits_total` | Counter | Попаданий в кэш memory |

### Langfuse Integration

```python
from langfuse.callback import CallbackHandler

# Инициализация Langfuse callback
langfuse_handler = CallbackHandler(
    public_key="pk-...",
    secret_key="sk-...",
    host="http://langfuse:3000"  # Self-hosted
)

# Интеграция с LangGraph
result = await app.ainvoke(
    inputs,
    config={
        "configurable": {"thread_id": session_id},
        "callbacks": [langfuse_handler]  # Автоматический трейсинг
    }
)
```

## Deployment

### Docker Compose (фрагмент)
```yaml
services:
  otel-collector:
    image: otel/opentelemetry-collector-contrib:0.96.0
    ports:
      - "4317:4317"   # OTLP gRPC
      - "4318:4318"   # OTLP HTTP
    volumes:
      - ./otel-config.yaml:/etc/otelcol-contrib/config.yaml

  prometheus:
    image: prom/prometheus:v2.50.0
    ports:
      - "9090:9090"
    volumes:
      - ./prometheus.yml:/etc/prometheus/prometheus.yml
      - prometheus-data:/prometheus

  grafana:
    image: grafana/grafana:10.3.0
    ports:
      - "3000:3000"
    environment:
      - GF_SECURITY_ADMIN_PASSWORD=admin
    volumes:
      - grafana-data:/var/lib/grafana

  langfuse:
    image: langfuse/langfuse:2.36.0
    ports:
      - "3001:3000"
    environment:
      - DATABASE_URL=postgresql://langfuse:pass@postgres:5432/langfuse
      - NEXTAUTH_SECRET=your-secret
    depends_on:
      - postgres
```

## Capacity Planning

### Resource Requirements
| Сервис | CPU | RAM | Storage |
|--------|-----|-----|---------|
| OTel Collector | 0.5 core | 512MB | 1GB |
| Prometheus | 1 core | 2GB | 50GB (30 days) |
| Grafana | 0.5 core | 512MB | 5GB |
| Langfuse | 1 core | 1GB | 10GB (PostgreSQL) |
| **Total** | **3 cores** | **4GB** | **66GB** |

### Data Volume
| Источник | Объем/день | Retention |
|----------|-----------|-----------|
| OTel Traces | ~500MB | 7 days |
| Prometheus Metrics | ~100MB | 30 days |
| Langfuse Data | ~200MB | 90 days |

## Последствия

### Положительные
- ✅ Полная наблюдаемость: traces + metrics + LLM analytics
- ✅ OpenTelemetry — стандарт, не vendor lock-in
- ✅ Prometheus/Grafana — проверенный стек для метрик
- ✅ Langfuse — специализированный LLM observability
- ✅ Self-hosted: все компоненты on-premise, air-gapped
- ✅ Алертинг при аномалиях (latency, RBAC violations, injection attempts)

### Отрицательные
- ⚠️ 4 дополнительных сервиса (OTel, Prometheus, Grafana, Langfuse)
- ⚠️ ~4GB RAM overhead для observability stack
- ⚠️ Сложность настройки OTel Collector пайплайнов
- ⚠️ Langfuse — молодой проект, возможны breaking changes

### Нейтральные
- Можно заменить Langfuse на Grafana Tempo для tracing при необходимости
- Prometheus long-term storage требует Thanos/Cortex

## Альтернативы рассмотрены
1. **LangSmith (cloud)** — отклонено (cloud-only, нарушает air-gapped)
2. **Jaeger** — отклонено (только tracing, избыточность с OTel+Tempo)
3. **ELK Stack** — отклонено (тяжелый, не для metrics, нет LLM analytics)
4. **Datadog / New Relic** — отклонено (cloud SaaS, нарушает air-gapped)
5. **Только Prometheus** — отклонено (нет tracing, нет LLM analytics)
6. **Только Langfuse** — отклонено (нет инфраструктурных метрик, GPU monitoring)

## Ссылки
- [OpenTelemetry Documentation](https://opentelemetry.io/docs/)
- [Prometheus Documentation](https://prometheus.io/docs/)
- [Grafana Documentation](https://grafana.com/docs/)
- [Langfuse Documentation](https://langfuse.com/docs)
- [Langfuse Self-Hosted](https://langfuse.com/docs/deployment/self-host)
- [OTel Python SDK](https://opentelemetry-python.readthedocs.io/)
