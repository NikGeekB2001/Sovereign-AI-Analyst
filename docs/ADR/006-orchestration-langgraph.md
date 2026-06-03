# ADR-006: Выбор фреймворка оркестрации (LangGraph vs LlamaIndex Workflows)

## Статус
Принято

## Контекст
Необходимо выбрать фреймворк для оркестрации мультиагентной системы с поддержкой:
- Циклические workflows (Plan-Observe-Act) — **линейные цепочки запрещены**
- Self-reflection и коррекция ошибок
- Persistent State (долгосрочная память между сессиями)
- Условная маршрутизация и ветвление
- Интеграция с MCP-инструментами (Qdrant, Neo4j, vLLM)
- Observability (Langfuse трейсинг каждого узла)

## Решение
Выбран **LangGraph** с **PostgreSQL Checkpointer** для управления состоянием.

### Архитектура оркестрации
```
User Request → Input Guardrails → Coordinator → [Loop: Planner → Retriever → Reranker → Analyst → Security Guard] → Synthesizer → Output Guardrails → Response
```

### Ключевые компоненты
1. **StateGraph** — основной граф выполнения с циклическими ребрами
2. **AgentState (TypedDict)** — типизированная схема состояния
3. **PostgresSaver** — персистентное хранение состояния между запросами
4. **Conditional Edges** — динамическая маршрутизация на основе промежуточных результатов

## Обоснование (Trade-off Analysis)

### Почему LangGraph?

**Преимущества:**
1. **Циклические workflows:** Нативная поддержка циклов — критично для Plan-Observe-Act. Агент может возвращаться к планированию при ошибках (self-reflection)
2. **Stateful Agents:** Встроенный PostgreSQL Checkpointer — состояние сохраняется между запросами. Пользователь может вернуться через неделю
3. **Conditional Routing:** Динамическая маршрутизация на основе результатов предыдущего шага (например, replan при ошибке)
4. **Type Safety:** TypedDict для AgentState — проверка типов на этапе разработки
5. **Streaming:** Нативная поддержка `astream()` для потокового вывода ответов
6. **Observability:** Полная интеграция с Langfuse для трейсинга каждого узла графа
7. **Recursion Limit:** Защита от бесконечных циклов (`recursion_limit=25`)
8. **Экосистема LangChain:** Совместимость с LangChain tools, retrievers, LLM wrappers

**Недостатки:**
1. **Зависимость от LangChain:** Привязка к экосистеме, риск breaking changes
2. **PostgreSQL overhead:** Дополнительная инфраструктура для Checkpointer
3. **Кривая обучения:** Новые концепции (StateGraph, conditional edges, channels)

### Почему не LlamaIndex Workflows?

**Преимущества LlamaIndex Workflows:**
- Event-driven архитектура (более гибкая для сложных сценариев)
- Меньше зависимостей (не требует LangChain)
- Хорошая интеграция с LlamaIndex retrieval pipeline

**Недостатки для нашего случая:**
1. **Менее зрелый:** Проект моложе LangGraph, меньше production deployments
2. **Ограниченный state management:** Нет встроенного PostgreSQL Checkpointer
3. **Меньше community:** 3k vs 10k+ GitHub stars, меньше документации
4. **Хуже observability:** Нет нативной интеграции с Langfuse
5. **Event-driven overhead:** Для нашего синхронного флоу (Plan-Observe-Act) event-driven модель избыточна
6. **Меньше примеров:** Меньше готовых паттернов для мультиагентных систем

### Почему не Apache Airflow?

**Недостатки Airflow:**
1. **Линейные DAG:** Не поддерживает циклы — **противоречит требованию задания**
2. **Нет состояния агента:** Не предназначен для хранения контекста диалога
3. **Overhead:** Избыточен для real-time взаимодействия (секунды vs миллисекунды)
4. **Batch-oriented:** Ориентирован на batch-обработку, не на интерактивные запросы

### Почему не чистый LangChain (LCEL)?

**Недостатки LCEL:**
1. **Только линейные цепочки:** LCEL не поддерживает циклы — **запрещено требованиями**
2. **Нет persistent state:** Состояние теряется после выполнения цепочки
3. **Ограниченная маршрутизация:** Сложно реализовать динамическое ветвление

### Почему не кастомная State Machine?

**Преимущества:**
- Полный контроль, нет зависимостей

**Недостатки:**
1. **Reinventing the wheel:** LangGraph уже решает все наши задачи
2. **Нет observability:** Придется писать интеграцию с Langfuse с нуля
3. **Нет community:** Нет готовых паттернов и примеров
4. **Больше кода:** 2-3x больше кода для аналогичного функционала

## Implementation Details

### Определение состояния
```python
from typing import TypedDict, Annotated, List
from langgraph.graph.message import add_messages

class AgentState(TypedDict):
    messages: Annotated[list, add_messages]     # История сообщений
    plan: List[str]                              # План действий
    current_step_idx: int                        # Текущий шаг
    context: Annotated[list, add_messages]       # Накопленный контекст
    user_role: str                               # Для RBAC
    clearance_level: int                         # Уровень доступа
    needs_replan: bool                           # Флаг для повторного планирования
    iteration_count: int                         # Счетчик итераций (защита от циклов)
```

### Построение графа (Production)
```python
from langgraph.graph import StateGraph, END
from langgraph.checkpoint.postgres.aio import AsyncPostgresSaver
from langgraph.graph.state import Command
from langgraph.types import RetryPolicy
from psycopg_pool import AsyncConnectionPool

# Async Connection Pool для PostgreSQL
connection_pool = AsyncConnectionPool(
    "postgresql://user:pass@localhost:5432/langgraph_db",
    open=False,
    max_size=20,
    kwargs={"autocommit": True, "connect_timeout": 5}
)
await connection_pool.open()

# AsyncPostgresSaver (Production Checkpointer)
checkpointer = AsyncPostgresSaver(connection_pool)
await checkpointer.setup()

# Построение графа с Command-based routing
workflow = StateGraph(AgentState)

# Добавление узлов с retry policy
workflow.add_node("coordinator", coordinator_node, destinations=("planner", "retriever", "__end__"))
workflow.add_node("planner", planner_node, destinations=("retriever", "coordinator"))
workflow.add_node("retriever", retriever_node, destinations=("analyst", "coordinator"))
workflow.add_node("analyst", analyst_node, destinations=("security_guard", "coordinator"))
workflow.add_node(
    "security_guard",
    security_guard_node,
    destinations=("synthesizer", "planner"),  # Self-reflection: replan
    retry_policy=RetryPolicy(max_attempts=3),
)
workflow.add_node("synthesizer", synthesizer_node, destinations=("__end__", "retriever"))

# Command-based conditional routing (вместо add_conditional_edges)
async def security_guard_node(state: AgentState) -> Command:
    filtered = apply_rbac_filter(state.context, state.user_role, state.clearance_level)
    if not filtered:
        # Self-reflection: возврат к планированию
        return Command(update={"needs_replan": True}, goto="planner")
    return Command(update={"filtered_context": filtered}, goto="synthesizer")

# Компиляция с AsyncPostgresSaver
app = workflow.compile(
    checkpointer=checkpointer,
    name="Sovereign-AI-Analyst Agent (production)"
)
```

### Self-Reflection Pattern (Command-based)
```python
from langgraph.graph.state import Command

async def security_guard_node(state: AgentState) -> Command:
    """RBAC фильтрация + PII маскировка с Command routing"""
    filtered_context = apply_rbac_filter(
        state.context,
        user_role=state.user_role,
        clearance_level=state.clearance_level
    )

    if not filtered_context:
        # Self-reflection: возврат к планированию
        return Command(
            update={
                "needs_replan": True,
                "iteration_count": state.iteration_count + 1
            },
            goto="planner"
        )

    return Command(
        update={"filtered_context": filtered_context, "needs_replan": False},
        goto="synthesizer"
    )
```

### SSE Streaming Response
```python
async def get_stream_response(messages, session_id, user_id, username):
    """Потоковая генерация ответа через SSE"""
    config = {
        "configurable": {"thread_id": session_id},
        "callbacks": [langfuse_callback_handler],
        "metadata": {"user_id": user_id, "username": username}
    }
    graph = await _get_graph()

    # Concurrent: state check + memory search
    state, relevant_memory = await asyncio.gather(
        graph.aget_state(config),
        memory_service.search(user_id, messages[-1].content),
    )

    if state.next:
        # Resume interrupted graph
        graph_input = Command(resume=messages[-1].content)
    else:
        graph_input = {"messages": messages, "long_term_memory": relevant_memory}

    # SSE Streaming
    async for token, _ in graph.astream(graph_input, config, stream_mode="messages"):
        if isinstance(token, (AIMessage, AIMessageChunk)):
            text = extract_text_content(token.content)
            if text:
                yield text
```

### Long-term Memory (mem0)
```python
from mem0 import AsyncMemory

class MemoryService:
    def __init__(self):
        self._memory = None

    async def _get_memory(self) -> AsyncMemory:
        if self._memory is None:
            self._memory = await AsyncMemory.from_config({
                "vector_store": {
                    "provider": "pgvector",
                    "config": {
                        "collection_name": "longterm_memory",
                        "dbname": POSTGRES_DB,
                        "user": POSTGRES_USER,
                        "password": POSTGRES_PASSWORD,
                        "host": POSTGRES_HOST,
                        "port": POSTGRES_PORT,
                    },
                },
                "llm": {"provider": "openai", "config": {"model": LLM_MODEL}},
                "embedder": {"provider": "openai", "config": {"model": EMBEDDER_MODEL}},
            })
        return self._memory

    async def search(self, user_id: str, query: str) -> str:
        memory = await self._get_memory()
        results = await memory.search(user_id=user_id, query=query)
        return "\n".join([f"* {r['memory']}" for r in results["results"]])

    async def add(self, user_id: str, messages: list, metadata: dict = None):
        memory = await self._get_memory()
        await memory.add(messages, user_id=user_id, metadata=metadata)
```

## Capacity Planning

### PostgreSQL Checkpointer
| Параметр | Значение |
|----------|----------|
| Storage на шаг | ~1KB |
| Сессия (10 шагов) | ~10KB |
| 1000 сессий/день | ~10MB/day |
| Месячный объем | ~300MB |
| Latency INSERT | <10ms |

### Performance
| Метрика | Значение |
|---------|----------|
| Latency добавления шага | <10ms |
| Latency загрузки состояния | <50ms |
| Overhead vs без checkpointer | <5% |

## Последствия

### Положительные
- ✅ Циклические workflows (Plan-Observe-Act) — требование задания выполнено
- ✅ Persistent state для долгосрочной памяти
- ✅ Self-reflection при ошибках (replan)
- ✅ Защита от бесконечных циклов (recursion_limit)
- ✅ Полная observability через Langfuse
- ✅ Type-safe разработка с TypedDict

### Отрицательные
- ⚠️ Зависимость от экосистемы LangChain
- ⚠️ PostgreSQL для Checkpointer (дополнительная инфраструктура)
- ⚠️ Кривая обучения для команды

### Нейтральные
- LangGraph работает поверх asyncio, требует async/await
- Граф компилируется один раз, затем переиспользуется
- Состояние хранится в JSON в PostgreSQL

## Альтернативы рассмотрены
1. **LlamaIndex Workflows** — отклонено (менее зрелый, нет PostgreSQL Checkpointer, хуже observability)
2. **Apache Airflow** — отклонено (линейные DAG, нет state, batch-oriented)
3. **LangChain LCEL** — отклонено (линейные цепочки, нет циклов — **запрещено**)
4. **Custom State Machine** — отклонено (reinventing the wheel, нет observability)
5. **Prefect / Dagster** — отклонено (аналогично Airflow, batch-oriented)
6. **CrewAI / AutoGen** — отклонено (высокоуровневые, меньше контроля)

## Ссылки
- [LangGraph Documentation](https://langchain-ai.github.io/langgraph/)
- [LangGraph Persistence](https://langchain-ai.github.io/langgraph/concepts/persistence/)
- [PostgreSQL Saver](https://langchain-ai.github.io/langgraph/how-tos/persistence_postgres/)
- [LlamaIndex Workflows](https://docs.llamaindex.ai/en/stable/module_guides/workflow/)
- [Plan-Observe-Act Pattern](https://langchain-ai.github.io/langgraph/concepts/agentic_concepts/)
