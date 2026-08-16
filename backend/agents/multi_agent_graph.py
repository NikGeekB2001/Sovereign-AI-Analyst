"""
Основной модуль мульти-агентной системы на базе LangGraph.
Интеграция с Langfuse для Observability (ADR #005).
Refactored by GLM-5.1 & Lingma Agent.
"""
from typing import Annotated, TypedDict, List, Literal
from langgraph.graph import StateGraph, END
from langgraph.graph.message import add_messages
# Загрузка .env (креды БД/LLM) — файл gitignored
try:
    from dotenv import load_dotenv
    load_dotenv()
except Exception:
    pass

import json
import os
import hashlib
import uuid
import sys

# Windows-консоль: переключаем stdout/stderr на UTF-8 (иначе эмодзи в print падают с UnicodeEncodeError)
if sys.stdout and hasattr(sys.stdout, "reconfigure"):
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
        sys.stderr.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass
from neo4j import GraphDatabase
from qdrant_client import QdrantClient
from qdrant_client.http.models import Filter, FieldCondition, MatchValue

# Добавляем путь к корню проекта для импорта сервисов
sys.path.append(os.path.join(os.path.dirname(__file__), '..', '..'))

# Import system prompts
from backend.prompts import (
    PLANNER_PROMPT,
    GRAPH_QUERY_PLANNER_PROMPT,
    SYNTHESIZER_PROMPT,
    JUDGE_SAFETY_PROMPT,
    JUDGE_QUALITY_PROMPT
)

# Unified LLM client (backend: ollama | vllm)
from backend.services.unified_llm_client import get_unified_client


def _llm_text(result):
    """Нормализует результат LLM-клиента в plain text (dict или str)."""
    if isinstance(result, dict):
        return result.get("response", "")
    return str(result)


# Initialize LLM client
llm_client = get_unified_client(
    backend=os.getenv("LLM_BACKEND", "ollama"),
    model=os.getenv("LLM_MODEL", "qwen2.5:3b")
)

# Simple Cache for MVP (Optimization Guide)
response_cache = {}

def get_cache_key(messages: list) -> str:
    """Генерирует ключ кэша на основе последнего сообщения пользователя."""
    if messages:
        last_msg = messages[-1][1] if isinstance(messages[-1], tuple) else str(messages[-1])
        return hashlib.md5(last_msg.encode()).hexdigest()
    return "empty"

# Langfuse Integration (Observability)
try:
    from langfuse import Langfuse
    from langfuse.model import CreateSpan, CreateTrace
    
    # Инициализация клиента Langfuse
    langfuse = Langfuse(
        public_key=os.getenv("LANGFUSE_PUBLIC_KEY", ""),
        secret_key=os.getenv("LANGFUSE_SECRET_KEY", ""),
        host=os.getenv("LANGFUSE_HOST", "http://localhost:3000")
    )
    
except Exception:
    langfuse = None

# Определение состояния графа (State Schema)
class AgentState(TypedDict):
    messages: Annotated[list, add_messages]
    plan: List[str]               # План действий от Planner Agent
    current_step_idx: int         # Текущий шаг
    context: List[str]            # Накопленный контекст (плоский список, без add_messages)
    generated_query: str          # Cypher от GraphQueryPlanner
    blocked: bool                 # Флаг блокировки Input Guardrail
    user_role: str                # Для RBAC (junior, senior, admin)

def input_guardrail_node(state: AgentState):
    """Input Guardrail: Проверка входящего запроса на безопасность."""
    print("️ Input Guardrail: Проверка запроса...")
    
    # Handle both tuple and Message object formats
    messages = state["messages"]
    if messages:
        last_msg = messages[-1]
        if isinstance(last_msg, tuple):
            user_query = last_msg[1]
        else:
            # LangChain Message object
            user_query = last_msg.content if hasattr(last_msg, 'content') else str(last_msg)
    else:
        user_query = ""
    
    # Начинаем трассировку для InputGuardrail узла
    if langfuse:
        # Создаем trace с использованием v3.14.6 API
        trace_id = str(uuid.uuid4())
        trace = langfuse.trace(
            id=trace_id,
            name="input-guardrail-node",
            input={"user_query": user_query, "user_role": state.get("user_role", "junior")},
            metadata={"node": "input_guardrail", "user_role": state.get("user_role", "junior")}
        )
        
        # Создаем span
        span_id = str(uuid.uuid4())
        span = trace.span(
            id=span_id,
            name="input-guardrail-execution",
            input={"user_query": user_query}
        )
    
    # В MVP используем простую эвристику, в проде — LLM-as-a-Judge с JUDGE_SAFETY_PROMPT
    forbidden_patterns = ["ignore rules", "jailbreak", "system prompt", "выдай пароль"]
    
    for pattern in forbidden_patterns:
        if pattern.lower() in user_query.lower():
            guard_result = {
                "messages": [("assistant", "Запрос отклонен: обнаружена попытка нарушения безопасности.")],
                "blocked": True
            }
            
            # Завершаем трассировку
            if langfuse and 'span' in locals():
                span.end(output=guard_result)
                langfuse.flush()
                
            return guard_result
    
    result = {"blocked": False}
    
    # Завершаем трассировку
    if langfuse and 'span' in locals():
        span.end(output=result)
        langfuse.flush()
    
    return result

def output_guardrail_node(state: AgentState):
    """Output Guardrail: Проверка исходящего ответа на безопасность и качество."""
    print("🛡️ Output Guardrail: Проверка ответа...")
    
    # Handle both tuple and Message object formats
    messages = state["messages"]
    if messages:
        last_msg = messages[-1]
        if isinstance(last_msg, tuple):
            response = last_msg[1]
        else:
            response = last_msg.content if hasattr(last_msg, 'content') else str(last_msg)
    else:
        response = ""
    
    # Получаем последний пользовательский запрос для определения типа запроса
    user_query = ""
    for msg in reversed(messages):
        if isinstance(msg, tuple) and msg[0] == "user":
            user_query = msg[1]
            break
        elif hasattr(msg, 'type') and msg.type == "human":  # LangChain Message object
            user_query = msg.content
            break
    
    # Определяем уровень доступа для каждой роли
    access_levels = {
        "junior": ["risks"],  # Junior может видеть риски
        "senior": ["risks", "companies", "documents"],  # Senior может видеть риски, компании и документы
        "admin": ["risks", "companies", "documents", "secrets"]  # Admin имеет полный доступ
    }
    
    user_role = state.get("user_role", "junior")
    allowed_actions = access_levels.get(user_role, [])
    
    # Проверяем, какие действия запрашивает пользователь
    restricted_access = False
    if "риски" in user_query.lower() or "риск" in user_query.lower():
        if "risks" not in allowed_actions:
            restricted_access = True
    elif "связанные компании" in user_query.lower() or "компании" in user_query.lower():
        if "companies" not in allowed_actions:
            restricted_access = True
    elif "документы" in user_query.lower() or "ромашка" in user_query.lower():
        if "documents" not in allowed_actions:
            restricted_access = True
    elif "секретные договора" in user_query.lower() or "секретн" in user_query.lower():
        if "secrets" not in allowed_actions:
            restricted_access = True
    elif "анализ" in user_query.lower() and ("риск" in user_query.lower() or "договор" in user_query.lower()):
        # Анализ рисков разрешен для всех ролей
        pass  # Не ограничиваем доступ
    
    # Начинаем трассировку для OutputGuardrail узла
    if langfuse:
        # Создаем trace с использованием v3.14.6 API
        trace_id = str(uuid.uuid4())
        trace = langfuse.trace(
            id=trace_id,
            name="output-guardrail-node",
            input={"response": response, "user_role": state.get("user_role", "junior"), "user_query": user_query},
            metadata={"node": "output_guardrail", "user_role": state.get("user_role", "junior"), "restricted_access": restricted_access}
        )
        
        # Создаем span
        span_id = str(uuid.uuid4())
        span = trace.span(
            id=span_id,
            name="output-guardrail-execution",
            input={"response": response, "user_query": user_query, "restricted_access": restricted_access}
        )
    
    # Проверка на утечку PII (ИНН, паспорта) для junior и проверка RBAC
    if restricted_access:
        response = f"[ДОСТУП ОГРАНИЧЕН ПОЛИТИКОЙ RBAC ДЛЯ РОЛИ {user_role.upper()}] Информация недоступна из-за ограничений безопасности."
    elif state.get("user_role") == "junior":
        import re
        inn_pattern = r'\b\d{10,12}\b'
        if re.search(inn_pattern, response):
            response = "[ДАННЫЕ ЗАЩИЩЕНЫ ПОЛИТИКОЙ RBAC]"
    
    result = {"messages": [("assistant", response)]}
    
    # Завершаем трассировку
    if langfuse and 'span' in locals():
        span.end(output=result)
        langfuse.flush()
    
    return result

def planner_node(state: AgentState):
    """Planner Agent: Декомпозиция задачи с использованием response_format=json."""
    print("🧠 Planner: Создание плана (Qwen via Ollama + JSON mode)...")
    
    # Handle both tuple and Message object formats
    messages = state["messages"]
    if messages:
        last_msg = messages[-1]
        if isinstance(last_msg, tuple):
            user_query = last_msg[1]
        else:
            user_query = last_msg.content if hasattr(last_msg, 'content') else str(last_msg)
    else:
        user_query = ""
    
    # Начинаем трассировку для Planner узла
    if langfuse:
        # Создаем trace с использованием v3.14.6 API
        trace_id = str(uuid.uuid4())
        trace = langfuse.trace(
            id=trace_id,
            name="planner-node",
            input={"user_query": user_query, "user_role": state.get("user_role", "junior")},
            metadata={"node": "planner", "user_role": state.get("user_role", "junior")}
        )
        
        # Создаем span
        span_id = str(uuid.uuid4())
        span = trace.span(
            id=span_id,
            name="planner-execution",
            input={"user_query": user_query}
        )
    
    # Инициализируем result заранее, чтобы избежать UnboundLocalError
    result = {"plan": ["1. Поиск информации", "2. Анализ данных"], "current_step_idx": 0}
    
    try:
        # Call LLM with JSON mode
        response = _llm_text(llm_client.generate(
            prompt=f"Запрос пользователя: {user_query}\n\nСоздай план действий в формате JSON списка.",
            system_prompt=PLANNER_PROMPT,
            json_mode=True,
            temperature=0.3
        ))
        
        # Parse JSON response
        try:
            plan = json.loads(response)
            if isinstance(plan, list):
                print(f"✅ План создан: {plan}")
                result = {"plan": plan, "current_step_idx": 0}
        except json.JSONDecodeError:
            print(f"⚠️ Не удалось распарсить JSON, используем fallback план")
        
            # Fallback plan if JSON parsing fails
            mock_plan = ["1. Поиск договора в Neo4j", "2. Проверка сумм в Qdrant", "3. Анализ рисков"]
            result = {"plan": mock_plan, "current_step_idx": 0}
    except Exception as e:
        print(f"❌ Ошибка Planner: {e}")
        import traceback
        traceback.print_exc()
        # Always return a fallback plan even on error
        mock_plan = ["1. Поиск информации", "2. Анализ данных"]
        result = {"plan": mock_plan, "current_step_idx": 0}
    
    # Завершаем трассировку для Planner узла
    if langfuse and 'span' in locals():
        span.end(output=result)
        langfuse.flush()
    
    return result

def graph_query_planner_node(state: AgentState):
    """GraphQueryPlanner: Генерация Cypher/SQL запросов (Qwen via Ollama)."""
    step = state["plan"][state["current_step_idx"]]
    print(f"📝 GraphQueryPlanner: Генерация запроса для шага '{step}'...")
    
    # Начинаем трассировку для GraphQueryPlanner узла
    if langfuse:
        # Создаем trace с использованием v3.14.6 API
        trace_id = str(uuid.uuid4())
        trace = langfuse.trace(
            id=trace_id,
            name="graph-query-planner-node",
            input={"step": step, "user_role": state.get("user_role", "junior")},
            metadata={"node": "graph_query_planner", "user_role": state.get("user_role", "junior")}
        )
        
        # Создаем span
        span_id = str(uuid.uuid4())
        span = trace.span(
            id=span_id,
            name="graph-query-planner-execution",
            input={"step": step}
        )
    
    # Инициализируем result заранее
    result = {"generated_query": "MATCH (a:LegalAct) RETURN a LIMIT 5"}
    
    try:
        # Generate Cypher query using LLM
        cypher_query = _llm_text(llm_client.generate(
            prompt=f"Шаг плана: {step}\n\nСгенерируй Cypher запрос для Neo4j.",
            system_prompt=GRAPH_QUERY_PLANNER_PROMPT,
            temperature=0.2
        ))
        
        print(f"✅ Сгенерирован Cypher: {cypher_query[:100]}...")
        result = {"generated_query": cypher_query}
    except Exception as e:
        print(f"⚠️ Ошибка генерации запроса: {e}")
        result = {"generated_query": "MATCH (a:LegalAct) RETURN a LIMIT 5"}
    
    # Завершаем трассировку для GraphQueryPlanner узла
    if langfuse and 'span' in locals():
        span.end(output=result)
        langfuse.flush()
    
    return result

def tool_executor_node(state: AgentState):
    """ToolExecutor: Выполнение запросов к Neo4j/Qdrant с RAG pipeline (Observe phase)."""
    print("🛠️ ToolExecutor: Выполнение запроса к БД с RAG...")
    
    # Начинаем трассировку для ToolExecutor узла
    if langfuse:
        # Создаем trace с использованием v3.14.6 API
        trace_id = str(uuid.uuid4())
        trace = langfuse.trace(
            id=trace_id,
            name="tool-executor-node",
            input={"current_step_idx": state["current_step_idx"], "user_role": state["user_role"]},
            metadata={"node": "tool_executor", "user_role": state["user_role"]}
        )
        
        # Создаем span
        span_id = str(uuid.uuid4())
        span = trace.span(
            id=span_id,
            name="tool-executor-execution",
            input={"current_step_idx": state["current_step_idx"]}
        )
    
    # Инициализируем result заранее
    result = {
        "context": state["context"] + [f"[Error]: Не удалось подключиться к БД"],
        "current_step_idx": state["current_step_idx"] + 1
    }
    
    # Инициализация клиентов
    neo4j_driver = GraphDatabase.driver(
        os.getenv("NEO4J_URI", "bolt://localhost:7687"), 
        auth=(os.getenv("NEO4J_USER", "neo4j"), os.getenv("NEO4J_PASSWORD", "password"))
    )
    
    new_context = []
    
    # Handle both tuple and Message object formats
    messages = state["messages"]

    # Извлекаем оригинальный запрос пользователя (первое сообщение)
    user_query = ""
    for msg in messages:
        if isinstance(msg, tuple) and msg[0] == "user":
            user_query = msg[1]
            break
        elif hasattr(msg, 'type') and msg.type == "human":
            user_query = msg.content
            break

    # Извлекаем последнее сообщение
    if messages:
        last_msg = messages[-1]
        if isinstance(last_msg, tuple):
            last_msg_content = last_msg[1]
        else:
            last_msg_content = last_msg.content if hasattr(last_msg, 'content') else str(last_msg)
    else:
        last_msg_content = ""

    # Для анализа запроса используем оригинальный запрос пользователя
    # (last_msg_content может быть результатом работы graph_query_planner)
    query_for_analysis = user_query if user_query else last_msg_content
    print(f"  Оригинальный запрос: {user_query[:100]}...")
    print(f"  Последнее сообщение: {last_msg_content[:100]}...")

    try:
        # 1. RAG Retriever + Reranker для Qdrant
        try:
            from backend.services.rag_retriever import get_retriever, get_reranker
            
            retriever = get_retriever()
            reranker = get_reranker()
            
            # Поиск с эмбеддингом запроса
            retrieved_docs = retriever.retrieve(
                query=query_for_analysis if query_for_analysis else last_msg_content,
                user_role=state["user_role"],
                top_k=5,
                min_score=0.3
            )
            
            if retrieved_docs:
                # Реранкинг для улучшения качества
                reranked_docs = reranker.rerank(
                    query=query_for_analysis if query_for_analysis else last_msg_content,
                    documents=retrieved_docs,
                    top_k=3
                )
                
                for doc in reranked_docs:
                    new_context.append(
                        f"[Qdrant RAG] (score: {doc['combined_score']:.2f}, type: {doc['doc_type']})\n"
                        f"{doc['text'][:500]}"
                    )
            else:
                new_context.append("[Qdrant]: Документы не найдены")
                
        except Exception as e:
            print(f"⚠️ Ошибка RAG pipeline: {e}")
            new_context.append(f"[Qdrant Error]: {str(e)}")
        
        # 2. Поиск в Neo4j: выполняем сгенерированный Cypher, иначе fallback по схеме LegalAct
        try:
            import re
            cypher_query = (state.get("generated_query") or "").strip()
            cypher_query = re.sub(r'^```(?:cypher)?\s*|\s*```$', '', cypher_query).strip()

            # Защита: выполняем только read-only Cypher, чтобы LLM не мог изменить граф
            if not re.match(r'^(MATCH|OPTIONAL\s+MATCH|UNWIND|WITH|RETURN|SHOW|CALL)\b', cypher_query, re.IGNORECASE | re.DOTALL) \
                    or re.search(r'\b(CREATE|MERGE|DELETE|SET|DROP|REMOVE|DETACH|LOAD CSV)\b', cypher_query, re.IGNORECASE | re.DOTALL):
                print("⚠️ Сгенерированный Cypher невалиден/не read-only, пропускаю (fallback)")
                cypher_query = ""

            user_role = state.get("user_role", "junior")
            # Определяем разрешенные уровни доступа для роли
            if user_role == "admin":
                allowed_access = ["public", "internal", "restricted"]
            elif user_role == "senior":
                allowed_access = ["public", "internal"]
            else:
                allowed_access = ["public"]

            if cypher_query:
                print(f"🕸️ Neo4j: Выполняем сгенерированный Cypher запрос...")
                with neo4j_driver.session() as session:
                    result_neo4j = session.run(cypher_query)
                    records = list(result_neo4j)

                    if records:
                        for record in records:
                            new_context.append(f"[Neo4j]: {dict(record)}")
                    else:
                        new_context.append("[Neo4j]: Запрос не вернул результатов")
            else:
                # Fallback: интеллектуальные запросы по схеме LegalAct/Authority/Keyword
                print("🕸️ Neo4j: Используем интеллектуальный fallback запрос")
                q = query_for_analysis.lower()

                with neo4j_driver.session() as session:
                    if "риск" in q:
                        # Акты по ключевому слову 'риск'
                        result_neo4j = session.run(
                            "MATCH (a:LegalAct)-[:HAS_KEYWORD]->(k:Keyword) "
                            "WHERE toLower(k.value) CONTAINS 'риск' AND a.access_level IN $allowed_access "
                            "RETURN a.act_id, a.title, a.doc_type, a.date, a.status "
                            "LIMIT 10",
                            allowed_access=allowed_access
                        )
                        for record in result_neo4j:
                            new_context.append(f"[Neo4j Risk Act]: {dict(record)}")

                    elif "компани" in q or "связан" in q or "орган" in q or "власт" in q:
                        # Акты по органу власти
                        result_neo4j = session.run(
                            "MATCH (a:LegalAct)-[:ISSUED_BY]->(auth:Authority) "
                            "WHERE a.access_level IN $allowed_access "
                            "RETURN auth.name, a.act_id, a.title, a.doc_type, a.date "
                            "LIMIT 10",
                            allowed_access=allowed_access
                        )
                        for record in result_neo4j:
                            new_context.append(f"[Neo4j Authority Act]: {dict(record)}")

                    elif "документ" in q or "договор" in q or "акт" in q:
                        # Свежие акты
                        result_neo4j = session.run(
                            "MATCH (a:LegalAct) "
                            "WHERE a.access_level IN $allowed_access "
                            "RETURN a.act_id, a.title, a.doc_type, a.date, a.status "
                            "ORDER BY a.date DESC LIMIT 10",
                            allowed_access=allowed_access
                        )
                        for record in result_neo4j:
                            new_context.append(f"[Neo4j LegalAct]: {dict(record)}")

                    else:
                        # Общий запрос
                        result_neo4j = session.run(
                            "MATCH (a:LegalAct) "
                            "WHERE a.access_level IN $allowed_access "
                            "RETURN a.act_id, a.title, a.doc_type, a.date, a.status "
                            "LIMIT 5",
                            allowed_access=allowed_access
                        )
                        for record in result_neo4j:
                            new_context.append(f"[Neo4j LegalAct]: {dict(record)}")
        except Exception as e:
            print(f"⚠️ Ошибка Neo4j: {e}")
            new_context.append(f"[Neo4j Error]: {str(e)}")
                
    except Exception as e:
        new_context.append(f"[Error]: Не удалось подключиться к БД: {str(e)}")
    finally:
        neo4j_driver.close()

    result = {
        "context": list(dict.fromkeys(state["context"] + new_context)),
        "current_step_idx": state["current_step_idx"] + 1,
        "generated_query": ""
    }

    # Завершаем трассировку для ToolExecutor узла
    if langfuse and 'span' in locals():
        span.end(output=result)
        langfuse.flush()
    
    return result

def security_guard_node(state: AgentState):
    """Security Guard: RBAC Filter и PII Masking на лету."""
    print("🛡️ Security Guard: Проверка прав доступа и PII...")
    
    # Начинаем трассировку для SecurityGuard узла
    if langfuse:
        # Создаем trace с использованием v3.14.6 API
        trace_id = str(uuid.uuid4())
        trace = langfuse.trace(
            id=trace_id,
            name="security-guard-node",
            input={"user_role": state["user_role"], "context_length": len(state["context"])},
            metadata={"node": "security_guard", "user_role": state["user_role"]}
        )
        
        # Создаем span
        span_id = str(uuid.uuid4())
        span = trace.span(
            id=span_id,
            name="security-guard-execution",
            input={"user_role": state["user_role"]}
        )
    
    original_context_length = len(state["context"])
    redacted_count = 0
    safe_context = []
    
    for item in state["context"]:
        # Convert to string if it's a Message object
        item_str = str(item)
        if hasattr(item, 'content'):
            item_str = item.content
        
        # Если пользователь junior и в тексте есть маркер секрета - вырезаем
        if state["user_role"] == "junior" and "секретно" in item_str.lower():
            safe_context.append("[DATA REDACTED DUE TO RBAC POLICY]")
            redacted_count += 1
            continue
        safe_context.append(item)
        
    # Перезаписываем контекст отфильтрованным
    result = {"context": safe_context}
    
    # Добавляем метаданные в трассировку
    if langfuse and 'span' in locals():
        span.end(
            output=result,
            metadata={
                "original_context_length": original_context_length,
                "redacted_items": redacted_count,
                "filtered_context_length": len(safe_context)
            }
        )
        langfuse.flush()
    
    return result

def synthesize_node(state: AgentState):
    """Synthesizer: Финальная генерация ответа (Streaming ready + Cache)."""
    cache_key = get_cache_key(state["messages"])
    
    # Проверка кэша (Optimization Guide)
    if cache_key in response_cache:
        print("⚡ Ответ взят из кэша.")
        return {"messages": [response_cache[cache_key]]}

    print("✨ Synthesizer: Сборка финального ответа через Ollama...")
    
    # Начинаем трассировку для Synthesize узла
    if langfuse:
        # Создаем trace с использованием v3.14.6 API
        trace_id = str(uuid.uuid4())
        trace = langfuse.trace(
            id=trace_id,
            name="synthesize-node",
            input={"cache_key": cache_key, "user_role": state["user_role"], "context_length": len(state["context"])},
            metadata={"node": "synthesize", "user_role": state["user_role"]}
        )
        
        # Создаем span
        span_id = str(uuid.uuid4())
        span = trace.span(
            id=span_id,
            name="synthesize-execution",
            input={"context_length": len(state["context"])}
        )
    
    # Prepare context for synthesis
    context_text = "\n".join([str(c) for c in state["context"]])

    # Извлекаем оригинальный запрос пользователя (первое сообщение)
    user_query_original = ""
    for msg in state["messages"]:
        if isinstance(msg, tuple) and msg[0] == "user":
            user_query_original = msg[1]
            break
        elif hasattr(msg, 'type') and msg.type == "human":
            user_query_original = msg.content
            break
    
    # Handle both tuple and Message object formats
    messages = state["messages"]
    if messages:
        last_msg = messages[-1]
        if isinstance(last_msg, tuple):
            user_query = last_msg[1]
        else:
            user_query = last_msg.content if hasattr(last_msg, 'content') else str(last_msg)
    else:
        user_query = ""
    
    # Инициализируем result заранее
    response_msg = ("assistant", "Пустой ответ")
    result = {"messages": [response_msg]}
    
    try:
        # Generate final answer using LLM
        final_response = _llm_text(llm_client.generate(
            prompt=f"Вопрос пользователя: {user_query}\n\nКонтекст:\n{context_text}",
            system_prompt=SYNTHESIZER_PROMPT,
            temperature=0.5,
            max_tokens=1024
        ))
        
        print(f"✅ Ответ сгенерирован ({len(final_response)} символов)")
        response_msg = ("assistant", final_response)
        
    except Exception as e:
        print(f"❌ Ошибка синтеза: {e}")
        import traceback
        traceback.print_exc()
        valid_sources = len([c for c in state["context"] if "REDACTED" not in str(c)])
        final_response = f"Ответ для пользователя ({state['user_role']}): На основе {valid_sources} источников сформирован вывод. (Fallback режим)"
        response_msg = ("assistant", final_response)

    # Сохранение в кэш
    response_cache[cache_key] = response_msg
    result = {"messages": [response_msg]}
    
    # Завершаем трассировку для Synthesize узла
    if langfuse and 'span' in locals():
        span.end(output={"response_length": len(final_response), "sources_count": len(state["context"])})
        langfuse.flush()
    
    return result

# --- Функции маршрутизации (Pure Functions) ---

def route_after_guardrail(state: AgentState) -> Literal["planner", "end"]:
    """Router: блокирующий запрос завершает поток сразу, иначе — planner."""
    if state.get("blocked"):
        print("🚫 Запрос заблокирован Input Guardrail, поток завершён.")
        return "end"
    return "planner"

def decide_next_step(state: AgentState) -> Literal["graph_query_planner", "synthesize"]:
    """Router: решает, выполнять ли следующий шаг или переходить к синтезу."""
    if state["current_step_idx"] >= len(state["plan"]):
        return "synthesize"
    
    step = state["plan"][state["current_step_idx"]]
    print(f"🔀 Router: Анализ шага {state['current_step_idx'] + 1}: {step}")
    
    # Простая эвристика маршрутизации - всегда используем graph_query_planner для шагов плана
    return "graph_query_planner"

# --- Построение графа ---

workflow = StateGraph(AgentState)

# Добавляем узлы (Grapheteria Compatible)
workflow.add_node("input_guardrail", input_guardrail_node)
workflow.add_node("planner", planner_node)
workflow.add_node("graph_query_planner", graph_query_planner_node)
workflow.add_node("tool_executor", tool_executor_node)
workflow.add_node("security_guard", security_guard_node)
workflow.add_node("synthesize", synthesize_node)
workflow.add_node("output_guardrail", output_guardrail_node)

# Устанавливаем точку входа
workflow.set_entry_point("input_guardrail")

# Добавляем ребра: guardrail блокирует поток или передаёт в planner
workflow.add_conditional_edges(
    "input_guardrail",
    route_after_guardrail,
    {"planner": "planner", "end": END}
)

workflow.add_conditional_edges(
    "planner", 
    decide_next_step,
    {"graph_query_planner": "graph_query_planner", "synthesize": "synthesize"}
)

# Plan-Observe-Act Loop: Planner -> Executor -> Security
workflow.add_edge("graph_query_planner", "tool_executor")
workflow.add_edge("tool_executor", "security_guard")

# После фильтрации безопасности решаем: есть ли еще шаги в плане?
workflow.add_conditional_edges(
    "security_guard",
    decide_next_step,
    {"graph_query_planner": "graph_query_planner", "synthesize": "synthesize"}
)

workflow.add_edge("synthesize", "output_guardrail")
workflow.add_edge("output_guardrail", END)

# Компиляция с защитой от бесконечных циклов (Best Practice)
app = workflow.compile()

if __name__ == "__main__":
    # Тестовый запуск с Langfuse Tracing
    inputs = {
        "messages": [("user", "Какие риски в договоре №123?")],
        "user_role": "junior",
        "context": []
    }
    
    config = {}
    if langfuse:
        print("📊 Трейсинг Langfuse активирован. Откройте http://localhost:3000")
    
    for event in app.stream(inputs, config=config):
        for k, v in event.items():
            if k != "__end__":
                print(f"--- Узел: {k} ---")