import chainlit as cl
import requests
from datetime import datetime
import sys
import os
from dotenv import load_dotenv
import uuid
import time
# Metrics: локальные заглушки (модуль metrics отсутствует в репозитории)
def log_business_metrics(**kwargs):
    print(f"[metrics] business: {kwargs}")

def log_performance_metrics(**kwargs):
    print(f"[metrics] perf: {kwargs}")

def log_security_metrics(**kwargs):
    print(f"[metrics] security: {kwargs}")

def log_error_metrics(**kwargs):
    print(f"[metrics] error: {kwargs}")

def flush_metrics():
    pass

# Загружаем переменные окружения
load_dotenv()

# Конфигурация
API_URL = "http://localhost:8000"

def check_api_health():
    """Проверка статуса API с retries."""
    for attempt in range(3):  # 3 попытки
        try:
            response = requests.get(f"{API_URL}/health", timeout=5)
            if response.status_code == 200:
                return True
        except:
            if attempt < 2:  # Не ждем после последней попытки
                time.sleep(1)
    return False

@cl.on_chat_start
async def start():
    """Инициализация чата при запуске."""
    # Проверяем статус API
    if check_api_health():
        await cl.Message(content="🤖 Привет! Я ваш AI аналитик.\n\n✅ API доступен, готов к работе.").send()
        cl.user_session.set("api_available", True)
    else:
        await cl.Message(content="🤖 Привет! Я ваш AI аналитик.\n\n❌ API недоступен. Пожалуйста, запустите сервер: `cd backend/api && python main.py`").send()
        cl.user_session.set("api_available", False)
    
    # Генерируем ID сессии
    session_id = str(uuid.uuid4())
    cl.user_session.set("session_id", session_id)
    
    # Устанавливаем роль пользователя по умолчанию
    user_role = "junior"
    cl.user_session.set("user_role", user_role)
    
    # Отображаем кнопку для выбора роли
    await cl.Message(content=f"Выберите свою роль: {user_role}").send()
    
    # Логируем начало сессии
    log_business_metrics(
        user_role=user_role,
        query_type="session_start",
        response_length=0,
        processing_time=0.0,
        session_id=session_id
    )

@cl.on_message
async def main(message: cl.Message):
    """Обработка входящего сообщения."""
    # Получаем информацию о пользователе и предыдущем состоянии API
    api_available = cl.user_session.get("api_available", False)
    session_id = cl.user_session.get("session_id", str(uuid.uuid4()))
    user_role = cl.user_session.get("user_role", "junior")
    
    # Логируем начало обработки сообщения
    start_time = time.time()
    
    # Логируем пользовательское взаимодействие
    log_business_metrics(
        user_role=user_role,
        query_type="user_message",
        response_length=len(message.content),
        processing_time=0.0,  # Время обработки будет известно позже
        session_id=session_id
    )
    
    # Логируем метрики производительности
    log_performance_metrics(
        trace_name="ui-message-processing",
        duration=0.0,  # Будет обновлено позже
        user_role=user_role,
        query_type="user_message"
    )
    
    # Логика проверки API (с ретраями)
    if not api_available and not check_api_health():
        await cl.Message(content="❌ API недоступен. Пожалуйста, запустите сервер: `cd backend/api && python main.py`").send()
        
        # Логируем ошибку безопасности
        log_security_metrics(
            event_type="api_unavailable",
            severity="medium",
            details={"user_role": user_role, "session_id": session_id}
        )
        return
    
    # Отправляем сообщение в API
    try:
        print(f"[DEBUG] Sending message: {message.content}")
        
        response = requests.post(
            f"{API_URL}/chat",
            json={
                "message": message.content,
                "user_role": user_role,  # Используем сохраненную роль пользователя
                "session_id": session_id  # Передаем ID сессии
            },
            timeout=120
        )
        
        processing_time = time.time() - start_time  # Время обработки запроса
        
        print(f"[DEBUG] Response status: {response.status_code}")
        
        if response.status_code != 200:
            error_msg = f"❌ Ошибка API: {response.status_code}"
            print(error_msg)
            await cl.Message(content=error_msg).send()
            
            # Логируем ошибку безопасности
            log_security_metrics(
                event_type="api_error",
                severity="high",
                details={"status_code": response.status_code, "user_role": user_role, "session_id": session_id}
            )
            
            # Логируем метрики ошибок
            log_error_metrics(
                error_type="api_error",
                error_message=f"Status code: {response.status_code}",
                user_role=user_role,
                severity="high"
            )
            
            flush_metrics()  # Принудительно отправляем метрики
            return
            
        result = response.json()
        ai_response = result.get("response", "Нет ответа")
        
        if not ai_response or ai_response.strip() == "":
            ai_response = "Пустой ответ от API"
            
        print(f"[DEBUG] Received response: {ai_response[:100]}...")
        
        # Отправляем ответ пользователю
        await cl.Message(content=ai_response).send()
        
        # Обновляем метрики после получения ответа
        log_performance_metrics(
            trace_name="ui-response-processing",
            duration=processing_time,
            user_role=user_role,
            query_type="user_response"
        )
        
        log_business_metrics(
            user_role=user_role,
            query_type="ai_response",
            response_length=len(ai_response),
            processing_time=processing_time,
            session_id=session_id
        )
        
        # Логируем успешное взаимодействие
        log_business_metrics(
            user_role=user_role,
            query_type="successful_interaction",
            response_length=len(message.content) + len(ai_response),
            processing_time=processing_time,
            session_id=session_id
        )
        
    except Exception as e:
        import traceback
        error_msg = f"❌ Ошибка: {str(e)}"
        print(f"[ERROR] {error_msg}")
        print(traceback.format_exc())
        await cl.Message(content=error_msg).send()
        
        processing_time = time.time() - start_time  # Время до возникновения ошибки
        
        # Логируем ошибку безопасности
        log_security_metrics(
            event_type="ui_error",
            severity="critical",
            details={"error": str(e), "user_role": user_role, "session_id": session_id, "processing_time": processing_time}
        )
        
        # Логируем метрики ошибок
        log_error_metrics(
            error_type="ui_error",
            error_message=str(e),
            user_role=user_role,
            severity="critical"
        )
        
        # Логируем метрики производительности при ошибке
        log_performance_metrics(
            trace_name="ui-error-processing",
            duration=processing_time,
            user_role=user_role,
            query_type="error"
        )
    
    finally:
        flush_metrics()  # Принудительно отправляем все метрики