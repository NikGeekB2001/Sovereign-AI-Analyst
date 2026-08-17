import os
from typing import Optional, Dict, Any
from langfuse import Langfuse
from contextvars import ContextVar


# Контекстная переменная для отслеживания trace_id
current_trace_id: ContextVar[Optional[str]] = ContextVar("current_trace_id", default=None)


def initialize_langfuse():
    """
    Инициализирует клиент Langfuse, если включена трассировка
    """
    if os.getenv("LANGFUSE_TRACING_ENABLED", "").lower() == "true":
        pk = os.getenv("LANGFUSE_PUBLIC_KEY", "")
        sk = os.getenv("LANGFUSE_SECRET_KEY", "")
        if not (pk and sk):
            print("Langfuse: LANGFUSE_PUBLIC_KEY/SECRET_KEY не заданы, трассировка отключена")
            return None
        try:
            langfuse = Langfuse(
                host=os.getenv("LANGFUSE_HOST", "http://localhost:3000"),
                public_key=pk,
                secret_key=sk
            )
            return langfuse
        except Exception as e:
            print(f"Ошибка инициализации Langfuse: {e}")
            return None
    return None


def get_trace_url(trace_id: str) -> Optional[str]:
    """
    Возвращает URL трассировки для Langfuse
    """
    try:
        langfuse_host = os.getenv("LANGFUSE_HOST", "http://localhost:3000")
        return f"{langfuse_host}/trace/{trace_id}"
    except Exception:
        return None


def flush_langfuse():
    """
    Сбрасывает буферизированные данные Langfuse
    """
    try:
        langfuse = initialize_langfuse()
        if langfuse:
            langfuse.flush()
    except Exception:
        pass


def create_trace_with_fallback(user_id: Optional[str] = None, session_id: Optional[str] = None,
                               name: Optional[str] = None, input: Optional[Any] = None,
                               output: Optional[Any] = None, metadata: Optional[Dict[str, Any]] = None,
                               **kwargs):
    """
    Создает трассировку с fallback-логикой (работает и без Langfuse).
    """
    def _make_mock():
        class MockSpan:
            def __init__(self):
                self.id = "mock-span-id"

            def span(self, **kw):
                return MockSpan()

            def generation(self, **kw):
                return MockGeneration()

            def event(self, **kw):
                return None

            def update(self, **kw):
                return self

            def end(self, **kw):
                return None

        class MockGeneration:
            def __init__(self):
                self.id = "mock-generation-id"

            def end(self, **kw):
                return self

            def update(self, **kw):
                return self

        class MockTrace:
            def __init__(self, **params):
                self.id = params.get("id", "mock-trace-id")

            def span(self, **kw):
                return MockSpan()

            def generation(self, **kw):
                return MockGeneration()

            def event(self, **kw):
                return None

            def update(self, **kw):
                return self

            def get_langchain_handler(self):
                return None

        return MockTrace(**kwargs)

    try:
        langfuse = initialize_langfuse()
        if langfuse:
            trace_params = {
                "user_id": user_id,
                "session_id": session_id,
                "name": name,
                "input": input,
                "output": output,
                "metadata": metadata,
            }
            trace_params = {k: v for k, v in trace_params.items() if v is not None}
            trace_params.update(kwargs)
            return langfuse.trace(**trace_params)
    except Exception as e:
        print(f"Ошибка создания трассировки: {e}")
    return _make_mock()


def log_user_interaction(user_id: str, query: str, response: str, metadata: Optional[Dict[str, Any]] = None):
    """
    Логирует взаимодействие пользователя с системой
    """
    try:
        trace = create_trace_with_fallback(user_id=user_id)
        span = trace.span(
            name="user_interaction",
            input={"query": query},
            output={"response": response},
            metadata=metadata
        )
        span.end()
        return trace.id
    except Exception as e:
        print(f"Ошибка логирования взаимодействия: {e}")
        return None


def log_graph_rag_flow(query: str, context: str, response: str, metadata: Optional[Dict[str, Any]] = None):
    """
    Логирует процесс GraphRAG в Langfuse
    """
    try:
        trace = create_trace_with_fallback()
        span = trace.span(
            name="graph_rag_process",
            input={"query": query},
            output={"response": response},
            metadata=metadata
        )
        
        # Логируем этапы процесса
        retrieval_span = span.span(
            name="retrieval_phase",
            input={"query": query},
            output={"context": context}
        )
        retrieval_span.end()
        
        synthesis_span = span.span(
            name="synthesis_phase",
            input={"context": context, "query": query},
            output={"response": response}
        )
        synthesis_span.end()
        
        span.end()
        return trace.id
    except Exception as e:
        print(f"Ошибка логирования GraphRAG: {e}")
        return None


def log_rag_retrieval(query: str, documents: list, metadata: Optional[Dict[str, Any]] = None):
    """
    Логирует процесс RAG-поиска
    """
    try:
        trace = create_trace_with_fallback()
        span = trace.span(
            name="rag_retrieval",
            input={"query": query},
            output={"documents": documents},
            metadata=metadata
        )
        span.end()
        return trace.id
    except Exception as e:
        print(f"Ошибка логирования RAG-поиска: {e}")
        return None


def log_graph_query(query: str, result: Any, metadata: Optional[Dict[str, Any]] = None):
    """
    Логирует выполнение графового запроса
    """
    try:
        trace = create_trace_with_fallback()
        span = trace.span(
            name="graph_query",
            input={"query": query},
            output={"result": result},
            metadata=metadata
        )
        span.end()
        return trace.id
    except Exception as e:
        print(f"Ошибка логирования графового запроса: {e}")
        return None


def log_performance_metrics(name: str, value: float, unit: str = "", metadata: Optional[Dict[str, Any]] = None, trace_id: Optional[str] = None):
    """
    Логирует метрики производительности
    """
    try:
        langfuse = initialize_langfuse()
        if langfuse:
            meta = dict(metadata) if metadata else {}
            if unit:
                meta["unit"] = unit
            score_kwargs = {"name": name, "value": value, "metadata": meta}
            if trace_id:
                score_kwargs["trace_id"] = trace_id
            langfuse.score(**score_kwargs)
    except Exception as e:
        print(f"Ошибка логирования метрик производительности: {e}")


def log_security_metrics(event_type: str, severity: str, description: str, metadata: Optional[Dict[str, Any]] = None, trace_id: Optional[str] = None):
    """
    Логирует метрики безопасности
    """
    try:
        trace = get_trace(trace_id) if trace_id else create_trace_with_fallback()
        span = trace.span(
            name=f"security_{event_type}",
            input={"severity": severity, "description": description},
            metadata=metadata
        )
        span.end()
    except Exception as e:
        print(f"Ошибка логирования метрик безопасности: {e}")


def log_business_metrics(metric_name: str, value: float, metadata: Optional[Dict[str, Any]] = None, trace_id: Optional[str] = None):
    """
    Логирует бизнес-метрики
    """
    try:
        langfuse = initialize_langfuse()
        if langfuse:
            score_kwargs = {"name": metric_name, "value": value, "metadata": metadata}
            if trace_id:
                score_kwargs["trace_id"] = trace_id
            langfuse.score(**score_kwargs)
    except Exception as e:
        print(f"Ошибка логирования бизнес-метрик: {e}")


def log_error_metrics(error_type: str, error_message: str, severity: str = "medium", metadata: Optional[Dict[str, Any]] = None, trace_id: Optional[str] = None):
    """
    Логирует метрики ошибок
    """
    try:
        trace = get_trace(trace_id) if trace_id else create_trace_with_fallback()
        span = trace.span(
            name="error_event",
            input={"type": error_type, "message": error_message, "severity": severity},
            metadata=metadata
        )
        span.end()
    except Exception as e:
        print(f"Ошибка логирования метрик ошибок: {e}")


def log_llm_call(model: str, prompt: str, response: str, duration_ms: Optional[float] = None, metadata: Optional[Dict[str, Any]] = None):
    """
    Логирует вызов LLM
    """
    try:
        trace = create_trace_with_fallback()
        generation = trace.generation(
            name="llm_call",
            input={"prompt": prompt},
            output={"response": response},
            model=model,
            model_parameters={"temperature": 0.7} if not metadata else metadata.get("model_parameters", {}),
            metadata=metadata
        )
        if duration_ms:
            generation.end(metadata={"duration_ms": duration_ms})
    except Exception as e:
        print(f"Ошибка логирования вызова LLM: {e}")


def get_trace(trace_id: str):
    """
    Получает существующий trace по ID
    """
    try:
        langfuse = initialize_langfuse()
        if langfuse:
            return langfuse.trace(trace_id)
        else:
            # Возвращаем mock-объект, если Langfuse не инициализирован
            class MockTrace:
                def __init__(self, trace_id):
                    self.id = trace_id
                
                def span(self, **kwargs):
                    return MockSpan()
                    
                def generation(self, **kwargs):
                    return MockGeneration()
                    
                def event(self, **kwargs):
                    pass
            
            return MockTrace(trace_id)
    except Exception as e:
        print(f"Ошибка получения трассировки: {e}")
        # Возвращаем mock-объект в случае ошибки
        class MockTrace:
            def __init__(self, trace_id):
                self.id = trace_id
            
            def span(self, **kwargs):
                return MockSpan()
                
            def generation(self, **kwargs):
                return MockGeneration()
                
            def event(self, **kwargs):
                pass
                
        return MockTrace(trace_id)


# Глобальный клиент Langfuse
langfuse = initialize_langfuse()