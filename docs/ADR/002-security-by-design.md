# ADR-002: Архитектура Security-by-Design

## Статус
Принято

## Контекст
Система работает в высокозащищенных средах (финансовый/государственный сектор), где данные не должны покидать периметр организации. Необходимо исключить:
- Утечки данных между департаментами (User B не должен видеть секретные документы User A)
- Prompt Injection атаки
- PII утечки через ответы LLM
- Cross-user data contamination
- Hallucination-based information leakage

Традиционные подходы (perimeter-based, post-processing) недостаточны для AI систем из-за их генеративной природы.

## Решение
Разделение на **Control Plane** и **Data Plane** с многоуровневой защитой Security-by-Design.

### Уровень 1: Data Plane — RBAC на чанках и узлах графа
- При индексации каждый чанк и узел графа (Entity) получают метаданные: `access_level`, `allowed_roles`, `department_id`
- В Qdrant: payload filtering на каждом поисковом запросе
- В Neo4j: Cypher WHERE фильтрация по RBAC меткам на узлах и ребрах
- Фильтрация происходит **до** ранжирования — недоступные данные никогда не попадают в контекст

### Уровень 2: Control Plane — Input/Output Guardrails
- **Input Guardrails** (API Gateway, FastAPI):
  - PII detection (персональные данные в запросе)
  - Prompt Injection detection (regex + ML классификатор)
  - Query sanitization перед передачей в Agent Loop
- **Output Guardrails** (API Gateway, FastAPI):
  - Data Leakage prevention (паттерн-матчинг)
  - Internal information disclosure detection
  - RBAC enforcement на финальном ответе

### Уровень 3: Network Segmentation
- **DMZ:** Nginx / Load Balancer (единственная точка входа, TLS 1.3)
- **Control Plane:** Gradio UI, FastAPI, LangGraph, PostgreSQL
- **Data Plane:** vLLM, Qdrant, Neo4j (air-gapped, нет прямого доступа извне)
- **Security Zone:** HashiCorp Vault (управление секретами, динамические креды)

### Уровень 4: Secrets Management
- **HashiCorp Vault:** Динамические секреты для БД, API Keys для vLLM
- Нет хардкода кредов в конфигурации
- Ротация секретов по расписанию

## Trade-off Analysis

### RBAC на уровне данных vs RBAC на уровне приложения

| Критерий | RBAC на данных ✅ | RBAC на приложении |
|----------|-------------------|-------------------|
| Надежность | Высокая (БД не вернет недоступные данные) | Средняя (ошибка в коде = утечка) |
| Производительность | Фильтрация в БД, оптимально | Фильтрация в Python, медленнее |
| Сложность | Метаданные при индексации | Фильтрация после извлечения |
| Аудит | Логируются запросы к БД | Сложнее отследить |

**Решение:** RBAC на уровне данных (Qdrant payload filter + Neo4j node properties) — надежнее и быстрее.

### Input Guardrails vs Post-Processing Redaction

| Критерий | Input Guardrails ✅ | Post-Processing |
|----------|---------------------|-----------------|
| Момент проверки | До Agent Loop | После генерации |
| Защита от injection | ✅ Запрещает вредоносный ввод | ❌ LLM уже обработал injection |
| Защита от PII | ✅ PII не попадает в контекст | ⚠️ PII может попасть в ответ |
| Latency overhead | +50-100ms (до обработки) | +50-100ms (после обработки) |

**Решение:** Input Guardrails — защита до обработки, а не после.

### Vault vs Environment Variables vs Kubernetes Secrets

| Критерий | Vault ✅ | Env Variables | K8s Secrets |
|----------|---------|---------------|-------------|
| Динамические секреты | ✅ | ❌ | ❌ |
| Ротация | ✅ Автоматическая | ❌ Ручная | ⚠️ External Secrets Operator |
| Аудит доступа | ✅ Полный | ❌ | ⚠️ Ограниченный |
| Air-gapped | ✅ Self-hosted | ✅ | ✅ |
| Сложность | Средняя | Низкая | Низкая |

**Решение:** Vault — динамические секреты, ротация, аудит.

## Implementation Details

### RBAC при индексации (Ingestion)
```python
# Каждый чанк получает RBAC метаданные
chunk.metadata = {
    'access_level': 3,                    # 0=public, 1=internal, 2=confidential, 3=secret
    'allowed_roles': ['finance', 'executive'],
    'department_id': 'finance',
    'classification': 'confidential'
}

# Каждый узел графа получает RBAC свойства
node_properties = {
    'access_level': 3,
    'allowed_roles': ['finance', 'executive'],
    'department_id': 'finance'
}
```

### RBAC при поиске (Qdrant)
```python
from qdrant_client.models import Filter, FieldCondition, MatchAny

rbac_filter = Filter(must=[
    FieldCondition(key="allowed_roles", match=MatchAny(any=user_roles)),
    FieldCondition(key="access_level", range={"lte": user_clearance_level})
])

results = qdrant_client.search(
    collection_name="documents",
    query_vector=query_embedding,
    query_filter=rbac_filter,  # Фильтрация ДО ранжирования
    limit=10
)
```

### RBAC при обходе графа (Neo4j)
```cypher
MATCH (e:Entity)-[r:RELATED_TO]->(e2:Entity)
WHERE e.access_level <= $clearance_level
  AND ANY(role IN $user_roles WHERE role IN e.allowed_roles)
RETURN e, r, e2
```

### Input Guardrails
```python
from guardrails import Guard

guard = Guard()

# PII detection
guard.validate(
    text=user_query,
    validators=["PIIDetector", "PromptInjectionDetector"]
)

# Если обнаружен PII или injection — запрос отклоняется
if not guard.passed:
    return {"error": "Query rejected by security guardrails", "reason": guard.failure_reason}
```

## Последствия

### Положительные
- ✅ Zero-trust security model на всех уровнях
- ✅ RBAC на уровне данных — недоступные чанки/узлы никогда не попадают в контекст
- ✅ Input Guardrails — защита до обработки, а не после
- ✅ Network segmentation — DMZ / Control Plane / Data Plane
- ✅ Vault — динамические секреты, ротация, аудит
- ✅ Соответствие требованиям ФЗ-152 и air-gapped

### Отрицательные
- ⚠️ Повышенная сложность retrieval pipeline (RBAC фильтры на каждом запросе)
- ⚠️ Снижение recall для пользователей с низким clearance level
- ⚠️ Необходимость полной разметки метаданных при индексации
- ⚠️ Дополнительный сервис Vault (операционный overhead)

### Нейтральные
- RBAC метаданные можно обновлять без переиндексации (payload update в Qdrant)
- Guardrails можно расширять новыми правилами без изменения архитектуры

## Альтернативы рассмотрены
1. **Post-processing redaction** — отклонено (слишком поздно, PII уже в контексте LLM)
2. **Perimeter security only** — отклонено (недостаточно для генеративных AI систем)
3. **RBAC на уровне приложения** — отклонено (ошибка в коде = утечка данных)
4. **Environment variables для секретов** — отклонено (нет ротации, нет аудита)
5. **Kubernetes Secrets** — отклонено (нет динамических секретов)

## Ссылки
- [Qdrant Payload Filtering](https://qdrant.tech/documentation/concepts/filtering/)
- [Neo4j Security](https://neo4j.com/docs/security/current/)
- [HashiCorp Vault](https://www.vaultproject.io/docs)
- [OWASP LLM Security](https://owasp.org/www-project-top-10-for-large-language-model-applications/)