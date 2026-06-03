# ADR-005: Выбор Vector Database (Self-hosted, RBAC)

## Статус
Принято

## Контекст
Необходимо выбрать self-hosted векторную БД для хранения эмбеддингов чанков с поддержкой:
- Фильтрация по payload (критично для RBAC на уровне чанков)
- Гибридный поиск (векторный + keyword)
- Высокая производительность (latency <50ms для поиска)
- Self-hosted, air-gapped развертывание
- Совместимость с LangChain/LangGraph экосистемой

## Решение
Выбран **Qdrant** как основная векторная БД.

### Конфигурация
- **Режим:** Qdrant Standalone (single-node, WAL persistence)
- **Хранение:** RAM для индекса + SSD для payload
- **Коллекции:** Одна коллекция на домен документов (finance, legal, technical)
- **RBAC:** Payload-фильтрация на каждом запросе

## Обоснование (Trade-off Analysis)

### Почему Qdrant?

**Преимущества:**
1. **Payload Filtering:** Нативная поддержка фильтрации по метаданным — критично для RBAC. Каждый чанк имеет `access_level`, `allowed_roles`, `department_id` — фильтрация происходит на уровне БД, а не в приложении
2. **Высокая производительность:** Rust-ядро, latency <10ms для типичных запросов
3. **Гибридный поиск:** Поддержка sparse + dense векторов (BM25 + semantic)
4. **Self-hosted:** Единый бинарник, нет внешних зависимостей, Docker-ready
5. **API-first:** gRPC + REST API, удобная интеграция с Python
6. **Активное развитие:** Регулярные релизы, растущее сообщество, коммерческая поддержка
7. **Quantization:** Встроенная поддержка scalar/product quantization для сжатия индекса

**Недостатки:**
1. **Нет встроенного RBAC:** Фильтрация по payload — не то же самое, что ролевой доступ. RBAC логика реализуется на уровне приложения
2. **Single-node по умолчанию:** Для HA требуется Qdrant Cluster (Enterprise)
3. **Меньше ecosystem:** Меньше интеграций, чем у Pinecone/Weaviate

### Почему не Milvus?

**Преимущества Milvus:**
- Горизонтальное масштабирование (distributed architecture)
- Поддержка множества индексов (IVF, HNSW, ScaNN)
- Высокая производительность на больших датасетах (>100M векторов)

**Недостатки для нашего случая:**
1. **Сложность развертывания:** Требует etcd, MinIO, Pulsar — 5+ контейнеров для production
2. **Избыточность:** Для нашего масштаба (<10M векторов) Milvus overkill
3. **Хуже payload filtering:** Фильтрация менее гибкая, чем у Qdrant
4. **Больше ресурсов:** Минимум 8GB RAM для пустого кластера
5. **Сложнее в air-gapped:** Много зависимостей, сложнее переносить

### Почему не Weaviate?

**Преимущества Weaviate:**
- Встроенный модуль генерации (RAG module)
- GraphQL API
- Мультимодальность (векторизация изображений)

**Недостатки для нашего случая:**
1. **Go runtime:** Дополнительная зависимость, больше потребление памяти
2. **Модули векторизации:** Зависят от внешних API (OpenAI, Cohere) — нарушает air-gapped
3. **Хуже производительность:** Latency выше на 20-40% по сравнению с Qdrant
4. **Меньше контроль над фильтрацией:** Сложнее реализовать fine-grained RBAC
5. **Лицензия BSD-3:** Допускает коммерцию, но Enterprise фичи платные

### Почему не ChromaDB?

**Преимущества ChromaDB:**
- Простота (pip install, in-memory режим)
- Хорош для прототипирования

**Недостатки для нашего случая:**
1. **Не production-ready:** Нет WAL, нет репликации, нет кластеризации
2. **Ограниченный масштаб:** Проблемы при >1M векторов
3. **Нет gRPC:** Только HTTP API, выше latency
4. **Нет гибридного поиска:** Только dense векторы

## RBAC Implementation в Qdrant

### Схема payload чанка
```python
payload = {
    "chunk_id": "uuid-1234",
    "doc_id": "doc-5678",
    "text": "Содержимое чанка...",
    "access_level": 3,           # 0=public, 1=internal, 2=confidential, 3=secret
    "allowed_roles": ["finance", "executive"],
    "department_id": "finance",
    "doc_type": "contract",
    "chunk_index": 5,
    "created_at": "2026-01-15T10:30:00Z"
}
```

### Фильтрация при поиске
```python
from qdrant_client.models import Filter, FieldCondition, MatchAny

# RBAC фильтр: только чанки, доступные роли пользователя
rbac_filter = Filter(
    must=[
        FieldCondition(
            key="allowed_roles",
            match=MatchAny(any=user_roles)  # ["finance", "executive"]
        ),
        FieldCondition(
            key="access_level",
            range={"lte": user_clearance_level}  # <= 3
        )
    ]
)

results = qdrant_client.search(
    collection_name="documents",
    query_vector=query_embedding,
    query_filter=rbac_filter,
    limit=10
)
```

## Capacity Planning

### Storage Requirements
| Параметр | Значение |
|----------|----------|
| Векторов (1M чанков) | ~4GB (768-dim, float32) |
| С payload | ~6GB |
| С quantization (int8) | ~2GB |
| RAM для индекса | ~4-8GB |
| SSD для persistence | ~10GB |

### Performance
| Метрика | Значение |
|---------|----------|
| Search latency (top-10) | <10ms |
| Search latency (с фильтром) | <20ms |
| Indexing throughput | ~10K vectors/sec |
| RAM usage (1M vectors) | ~4-8GB |

## Последствия

### Положительные
- ✅ Нативная payload фильтрация для RBAC
- ✅ Высокая производительность (Rust, <10ms latency)
- ✅ Простое развертывание (один Docker контейнер)
- ✅ Гибридный поиск (sparse + dense)
- ✅ Встроенное квантование для экономии памяти

### Отрицательные
- ⚠️ RBAC логика на уровне приложения (не встроена в БД)
- ⚠️ Single-node — нет HA из коробки
- ⚠️ Требуется отдельный сервис для эмбеддингов

### Нейтральные
- Можно мигрировать на Qdrant Cluster при росте нагрузки
- ChromaDB остается опцией для dev/тестов

## Альтернативы рассмотрены
1. **Milvus** — отклонено (сложность развертывания, избыточность, хуже payload filtering)
2. **Weaviate** — отклонено (зависимость от внешних API для векторизации, выше latency)
3. **ChromaDB** — отклонено (не production-ready, нет масштабирования)
4. **pgvector** — отклонено (ниже производительность, нет гибридного поиска)
5. **Pinecone** — отклонено (cloud-only, нарушает air-gapped)

## Ссылки
- [Qdrant Documentation](https://qdrant.tech/documentation/)
- [Qdrant Payload Filtering](https://qdrant.tech/documentation/concepts/filtering/)
- [Milvus](https://milvus.io/)
- [Weaviate](https://weaviate.io/)
- [Vector DB Benchmark (2025)](https://ann-benchmarks.com/)
