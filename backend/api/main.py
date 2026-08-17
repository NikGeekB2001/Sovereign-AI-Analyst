"""
FastAPI Service for SovereignAI Analyst.
Implements Streaming (SSE) and integrates with LangGraph Multi-Agent System.
"""
import os
import logging
import time  # Для измерения времени выполнения
from typing import Optional, Dict, Any
from fastapi import FastAPI, HTTPException

# Загрузка .env (креды БД/LLM) — файл gitignored
try:
    from dotenv import load_dotenv
    load_dotenv()
except Exception:
    pass
from fastapi.responses import StreamingResponse
from fastapi.responses import PlainTextResponse
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel
import json
import sys
import asyncio
from contextlib import asynccontextmanager

# Windows-консоль: переключаем stdout/stderr на UTF-8 (иначе эмодзи в print падают с UnicodeEncodeError)
if sys.stdout and hasattr(sys.stdout, "reconfigure"):
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
        sys.stderr.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass

# Configure logging
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

# Добавляем путь к корню проекта для импорта агентов
sys.path.append(os.path.join(os.path.dirname(__file__), '..', '..'))

# Импорт агента и Langfuse
from backend.agents.multi_agent_graph import app as agent_app
from backend.services.tool_registry import get_manifest
from backend.services.tool_dispatcher import execute_tool

# --- Prometheus-метрики (текстовый формат 0.0.4, без prometheus_client) ---
import threading
_metrics_lock = threading.Lock()
_METRICS = {
    "sov_chat_requests_total": 0,
    "sov_chat_errors_total": 0,
    "sov_chat_latency_seconds_total": 0.0,
}
_ROLE_LABEL = {}


def _inc_metric(name: str, delta: float = 1.0) -> None:
    with _metrics_lock:
        _METRICS[name] = round(_METRICS[name] + delta, 3)


def _inc_role(role: str) -> None:
    with _metrics_lock:
        _ROLE_LABEL[role] = _ROLE_LABEL.get(role, 0) + 1


# Импорт из langfuse_integration (используем абсолютный путь из корня проекта)
from backend.api.langfuse_integration import (
    langfuse, 
    get_trace, 
    flush_langfuse, 
    log_rag_retrieval,
    log_graph_query,



    # log_llm_call  # Добавляем импорт функции логирования вызова LLM
)

# Импорт дополнительных функций из langfuse_integration
from backend.api.langfuse_integration import (
    log_performance_metrics,
    log_security_metrics,
    log_business_metrics,
    log_error_metrics,
    log_llm_call,
    create_trace_with_fallback  # Добавляем недостающую функцию
)

app = FastAPI(title="SovereignAI Analyst API", version="2.0.0")

# CORS middleware - разрешаем запросы из любого источника (для локальной разработки)
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],  # В production заменить на конкретные домены
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

def _real_trace_id(trace) -> Optional[str]:
    """Возвращает id трассы только для реального Langfuse (не mock)."""
    tid = getattr(trace, "id", None)
    if langfuse and tid and not str(tid).startswith("mock"):
        return str(tid)
    return None


class ChatRequest(BaseModel):
    message: str
    user_role: str = "куратор"  # куратор, специалист отдела, admin
    session_id: Optional[str] = None

async def event_generator(request: ChatRequest):
    """Генератор событий для Streaming Response."""
    # Создаем трейс в Langfuse с расширенной информацией (fallback, если Langfuse выключен)
    trace = create_trace_with_fallback(
        name="api-chat-stream",
        input={"message": request.message, "user_role": request.user_role},
        metadata={
            "session_id": request.session_id,
            "endpoint": "/chat/stream",
            "streaming": True
        }
    )
    
    # Добавляем событие начала обработки
    try:
        trace.event(
            name="request_received",
            input={"message": request.message, "user_role": request.user_role}
        )
    except Exception as e:
        logger.warning(f"Langfuse event failed: {e}")
    
    inputs = {
        "messages": [("user", request.message)],
        "user_role": request.user_role,
        "context": []
    }
    
    try:
        # Добавляем информацию о начале обработки в лог
        logger.info(f"Starting streaming response for message: {request.message[:100]}...")
        
        # Создаем спан для обработки запроса (время в секундах)
        processing_span = trace.span(
            name="request_processing",
            input={"message": request.message},
            start_time=time.time()
        )
        
        # Добавляем метрику времени выполнения в миллисекундах
        start_time_ms = time.time() * 1000
        response_text = ""  # Для сбора текста ответа
        
        # Запуск графа агентов
        async for event in agent_app.astream(inputs, config={"recursion_limit": 50}):
            for node_name, output in event.items():
                if node_name != "__end__":
                    # Формируем SSE событие
                    try:
                        data = json.dumps({"node": node_name, "content": output}, ensure_ascii=False)
                        logger.debug(f"Yielding event from node {node_name}, content length: {len(output) if isinstance(output, str) else 'N/A'}")
                        
                        # Добавляем событие для каждого узла обработки
                        trace.event(
                            name=f"node_{node_name}_processed",
                            output={"node": node_name, "content_length": len(str(output))}
                        )
                        
                        response_text += str(output)  # Собираем текст ответа
                        yield f"data: {data}\n\n"
                        await asyncio.sleep(0.01)  # Небольшая задержка для плавности
                    except Exception as inner_e:
                        logger.error(f"Error processing event from node {node_name}: {str(inner_e)}")
                        # Добавляем событие об ошибке
                        trace.event(
                            name="error_in_node",
                            output={"error": str(inner_e), "node": node_name},
                            level="ERROR"
                        )
                        yield f"data: {{\"error\": \"Error processing response: {str(inner_e)}\"}}\n\n"
                    
        yield "data: [DONE]\n\n"
    except Exception as e:
        logger.error(f"Error in event generator: {str(e)}", exc_info=True)
        # Добавляем событие об ошибке с деталями
        trace.event(
            name="error_occurred",
            input={"message": request.message},
            output={
                "error": str(e),
                "error_type": type(e).__name__,
                "session_id": request.session_id or "unknown"
            },
            level="ERROR"
        )
        yield f"data: {{\"error\": \"Server error: {str(e)}\"}}\n\n"
    finally:
        # Завершаем спан обработки с временем в секундах
        processing_span.end(end_time=time.time())
        
        # Рассчитываем время выполнения в миллисекундах
        execution_time_ms = (time.time() * 1000) - start_time_ms
        
        # Логируем метрики производительности
        log_performance_metrics(
            name="processing_time",  # Изменено с trace_name на name
            value=execution_time_ms/1000,  # Изменено с duration на value
            unit="seconds",
            metadata={
                "tokens_used": 0,  # Предполагаемое значение, замените на реальное если доступно
                "model_name": "streaming-api",  # Предполагаемое значение, замените на реальное если доступно
                "user_id": request.user_role  # Используем user_role как user_id
            },
            trace_id=_real_trace_id(trace)
        )
        
        # Логируем бизнес-метрики
        log_business_metrics(
            metric_name="streaming_chat",  # Правильное имя параметра
            value=len(response_text),    # Правильное имя параметра
            metadata={
                "user_role": request.user_role,
                "session_id": request.session_id,
                "response_length": len(response_text),
                "processing_time": execution_time_ms/1000  # Преобразуем в секунды
            },
            trace_id=_real_trace_id(trace)
        )
        
        # Добавляем событие завершения с метриками
        trace.event(
            name="request_completed",
            input={"message_length": len(request.message)},
            output={
                "status": "completed",
                "response_length": len(response_text),
                "execution_time_ms": execution_time_ms
            }
        )
        
        # Завершаем трейс
        try:
            trace.update(output={"status": "completed"})
            logger.info("Trace updated with completion status")
        except Exception as trace_error:
            logger.error(f"Error updating trace: {str(trace_error)}")
        finally:
            if langfuse:
                langfuse.flush()
            logger.info("Langfuse connection flushed")

@app.post("/chat/stream")
async def chat_stream(request: ChatRequest):
    """Endpoint для потокового ответа от агентов (SSE)."""
    return StreamingResponse(
        event_generator(request),
        media_type="text/event-stream"
    )

@app.post("/chat")
async def chat(request: ChatRequest):
    """Endpoint для обычного JSON ответа."""
    # Запускаем таймер для измерения времени выполнения
    start_time = time.time()
    _inc_metric("sov_chat_requests_total")
    _inc_role(request.user_role)
    
    # Создаем трейс в Langfuse с универсальной функцией
    logger.info(f"Received chat request: {request.message[:100]}...")
    logger.info(f"User role: {request.user_role}")
    
    trace = create_trace_with_fallback(
        name="api-chat",
        input={"message": request.message, "user_role": request.user_role},
        metadata={"session_id": request.session_id}
    )
    
    if not trace:
        logger.error("Failed to create trace in Langfuse")
        # Создаем фоллбэк-объект с заглушками методов
        class TraceFallback:
            def update(self, **kwargs):
                logger.debug(f"TraceFallback update called with {kwargs}")
                return self
            
            def get_langchain_handler(self):
                logger.debug("TraceFallback get_langchain_handler called")
                return None
                
        trace = TraceFallback()
    
    # Спан обработки запроса (привязка к трассе Langfuse)
    processing_span = None
    if langfuse and _real_trace_id(trace):
        processing_span = trace.span(name="request_processing", input={"message": request.message})
    
    inputs = {
        "messages": [("user", request.message)],
        "user_role": request.user_role,
        "context": []
    }
    
    try:
        print(f"\n📥 [API] Получен запрос: {request.message[:100]}...")
        print(f"📥 [API] Роль пользователя: {request.user_role}")
        
        # Проверка на существование agent_app и безопасный вызов
        if agent_app is None:
            print("❌ [API] Agent app is None")
            raise RuntimeError("Agent app is not initialized")
        
        # Проверим, что agent_app действительно является объектом компилированного графа
        print(f"📋 [API] Agent app type: {type(agent_app)}")
        
        result = await agent_app.ainvoke(inputs, config={"recursion_limit": 50})
        
        # Проверка результата
        if result is None:
            response_text = ""
            print("⚠️ [API] Result is None, returning empty response")
        else:
            messages = result.get("messages", [])
            print(f"📊 [API] Получено сообщений: {len(messages)}")
            
            if not messages:
                response_text = ""
                print("⚠️ [API] Нет сообщений в ответе!")
            else:
                last_msg = messages[-1]
                
                # Обработка разных типов сообщений
                if isinstance(last_msg, tuple):
                    response_text = last_msg[1] if len(last_msg) > 1 else ""
                    print(f"📤 [API] Формат: tuple, длина: {len(response_text)}")
                elif hasattr(last_msg, 'content'):
                    response_text = last_msg.content
                    print(f"📤 [API] Формат: Message object, длина: {len(response_text)}")
                else:
                    response_text = str(last_msg)
                    print(f"📤 [API] Формат: fallback, длина: {len(response_text)}")
    
        # Проверка на пустой текст
        if not response_text:
            print("⚠️ [API] Response text is empty after processing")
            response_text = "Система не смогла обработать ваш запрос. Пожалуйста, повторите попытку."

        # Завершаем спан обработки
        if processing_span is not None:
            processing_span.end(output={"response": response_text})

        # Вычисляем время выполнения
        execution_time_ms = (time.time() - start_time) * 1000  # Преобразуем в миллисекунды
        
        # Проверяем что ответ валидный JSON
        response_data = {"response": response_text}
        print(f" [API] Ответ готов: {len(response_text)} символов")
        
        # Логируем метрики производительности
        log_performance_metrics(
            name="api_chat_duration",  # Изменено с duration на name
            value=execution_time_ms/1000,  # Изменено с duration на value
            unit="seconds",  # Добавлено поле unit
            metadata={
                "tokens_used": 0,  # Предполагаемое значение, замените на реальное если доступно
                "model_name": "api-chat",  # Предполагаемое значение, замените на реальное если доступно
                "user_id": request.user_role  # Используем user_role как user_id
            },
            trace_id=_real_trace_id(trace)
        )
        
        # Логируем бизнес-метрики
        log_business_metrics(
            metric_name="regular_chat",  # Правильное имя параметра
            value=len(response_text),    # Правильное имя параметра
            metadata={
                "user_role": request.user_role,
                "session_id": request.session_id
            },
            trace_id=_real_trace_id(trace)
        )
        
        # Обновляем трейс с результатом и метриками
        try:
            trace.update(
                input={"message": request.message},
                output={
                    "response": response_text,
                    "status": "success",
                    "response_length": len(response_text),
                    "execution_time_ms": execution_time_ms
                }
            )
            logger.info("Trace updated with response and metrics")
        except Exception as trace_error:
            logger.error(f"Error updating trace with response: {str(trace_error)}")
        finally:
            if langfuse:
                langfuse.flush()
            logger.info("Langfuse connection flushed after chat endpoint")
        
        _inc_metric("sov_chat_latency_seconds_total", execution_time_ms / 1000.0)
        return response_data
        
    except Exception as e:
        _inc_metric("sov_chat_errors_total")
        import traceback
        error_detail = traceback.format_exc()
        logger.error(f"ERROR in /chat endpoint: {str(e)}\n{error_detail}")
        
        # Логируем метрики безопасности при ошибке
        log_security_metrics(
            event_type="api_error",
            severity="critical",
            description=str(e),  # Правильное имя параметра
            metadata={"user_id": request.user_role},  # Передаем user_id в metadata
            trace_id=_real_trace_id(trace)
        )
        
        # Логируем метрики ошибок
        log_error_metrics(
            error_type="api_error",
            error_message=str(e),
            severity="critical",
            metadata={"user_id": request.user_role},  # Передаем user_id в metadata
            trace_id=_real_trace_id(trace)
        )
        
        # Обновляем трейс с ошибкой
        try:
            trace.update(output={"error": str(e)}, level="ERROR", status_message=str(e))
            logger.info("Trace updated with error information")
        except Exception as trace_error:
            logger.error(f"Error updating trace with error: {str(trace_error)}")
        finally:
            if langfuse:
                langfuse.flush()
            logger.info("Langfuse connection flushed after error")
        
        raise HTTPException(status_code=500, detail=str(e))

@app.get("/health")
def health_check():
    """Health check endpoint с логированием метрик"""
    import psutil
    import os
    
    # Получаем системные метрики
    memory_usage = psutil.virtual_memory().percent
    cpu_usage = psutil.cpu_percent(interval=1)
    disk_usage = psutil.disk_usage('/').percent if os.name != 'nt' else psutil.disk_usage('C:\\').percent
    
    # Логируем метрики производительности для health check
    log_performance_metrics(
        name="health-check-metrics",  # Изменено с trace_name на name
        value=0.0,  # Изменено с duration на value
        metadata={"memory_usage": memory_usage, "cpu_usage": cpu_usage}
    )
    
    # Логируем метрики безопасности для health check
    log_security_metrics(
        event_type="health_check",
        severity="low",
        description=str({"memory_usage": memory_usage, "cpu_usage": cpu_usage, "disk_usage": disk_usage})  # Изменено с details на description
    )
    
    return {
        "status": "ok", 
        "service": "SovereignAI Analyst Core",
        "timestamp": time.time(),
        "resources": {
            "memory_usage_percent": memory_usage,
            "cpu_usage_percent": cpu_usage,
            "disk_usage_percent": disk_usage
        }
    }

@app.get("/metrics")
def metrics():
    """Экспорт метрик для Prometheus (job 'fastapi-app' из deploy/prometheus.yml)."""
    with _metrics_lock:
        lines = [
            "# HELP sov_chat_requests_total Всего запросов /chat и /api/v1/chat",
            "# TYPE sov_chat_requests_total counter",
        ]
        for role, n in sorted(_ROLE_LABEL.items()):
            lines.append(f'sov_chat_requests_total{{role="{role}"}} {n}')
        lines.append("# HELP sov_chat_errors_total Ошибки обработки запросов")
        lines.append("# TYPE sov_chat_errors_total counter")
        lines.append(f"sov_chat_errors_total {_METRICS['sov_chat_errors_total']}")
        lines.append("# HELP sov_chat_latency_seconds_total Суммарная латентность ответов, сек")
        lines.append("# TYPE sov_chat_latency_seconds_total counter")
        lines.append(f"sov_chat_latency_seconds_total {_METRICS['sov_chat_latency_seconds_total']}")
    return PlainTextResponse("\n".join(lines) + "\n")

class ToolCallRequest(BaseModel):
    tool: str
    arguments: dict = {}
    user_role: str = "куратор"


@app.post("/api/v1/tools/call")
async def api_v1_tools_call(req: ToolCallRequest):
    """Единая точка вызова инструмента (см. docs/diagrams/tool_call_sequence.puml).

    Обёртка результата: {"ok": true, "data"} или HTTP-статус по SOV-коду:
    422 - SOV-1xxx (валидация), 403 - SOV-4xxx (RBAC), 502 - SOV-3xxx (LLM),
    503 - SOV-5xxx (инфраструктура).
    """
    result = execute_tool(req.tool, req.arguments or {}, req.user_role)
    if not result["ok"]:
        code = result["error"]["code"]
        status = 422 if code.startswith("SOV-1") else (
            403 if code.startswith("SOV-4") else 503 if code.startswith("SOV-5") else 502
        )
        raise HTTPException(status_code=status, detail=result["error"])
    return {"result": result["data"]}


# --- API v1: контрактная поверхность (см. docs/mcp_design.md) ---

@app.get("/api/v1/tools")
def api_v1_tools():
    """Манифест инструментов (mcp.json) + модель ошибок + scopes по ролям."""
    return get_manifest()


@app.get("/api/v1/health")
def api_v1_health():
    return {"status": "ok", "version": "0.1.0", "api": "v1"}


@app.post("/api/v1/chat")
async def api_v1_chat(request: ChatRequest):
    return await chat(request)


@app.post("/api/v1/chat/stream")
async def api_v1_chat_stream(request: ChatRequest):
    return await chat_stream(request)


if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=8000)

# --- API v1: контрактная поверхность (см. docs/mcp_design.md) ---

@app.get("/api/v1/tools")
def api_v1_tools():
    """Манифест инструментов (mcp.json) + модель ошибок + scopes по ролям."""
    return get_manifest()


@app.get("/api/v1/health")
def api_v1_health():
    return {"status": "ok", "version": "0.1.0", "api": "v1"}


@app.post("/api/v1/chat")
async def api_v1_chat(request: ChatRequest):
    return await chat(request)


@app.post("/api/v1/chat/stream")
async def api_v1_chat_stream(request: ChatRequest):
    return await chat_stream(request)
