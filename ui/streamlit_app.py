"""
Streamlit UI для SovereignAI Analyst.
Альтернативный интерфейс на Python.
"""

import streamlit as st
import requests
import json
from datetime import datetime

# Конфигурация
API_URL = "http://localhost:8000"

st.set_page_config(
    page_title="SovereignAI Analyst",
    page_icon="🤖",
    layout="wide",
    initial_sidebar_state="expanded"
)

# Стили CSS
st.markdown("""
<style>
    .main-header {
        background: linear-gradient(135deg, #667eea 0%, #764ba2 100%);
        padding: 2rem;
        border-radius: 15px;
        color: white;
        text-align: center;
        margin-bottom: 2rem;
    }
    .main-header h1 {
        font-size: 2.5rem;
        margin-bottom: 0.5rem;
    }
    .main-header p {
        font-size: 1.1rem;
        opacity: 0.9;
    }
    .chat-message {
        padding: 1rem;
        border-radius: 10px;
        margin: 0.5rem 0;
    }
    .user-message {
        background: linear-gradient(135deg, #667eea 0%, #764ba2 100%);
        color: white;
        margin-left: 20%;
    }
    .assistant-message {
        background: #f0f2f6;
        color: #333;
        margin-right: 20%;
    }
    .error-message {
        background: #ff6b6b;
        color: white;
        padding: 1rem;
        border-radius: 10px;
        margin: 0.5rem 0;
    }
    .security-badge {
        background: #e8f5e9;
        padding: 0.5rem 1rem;
        border-radius: 20px;
        color: #2e7d32;
        font-size: 0.9rem;
        display: inline-block;
        margin: 0.25rem;
    }
    .stButton>button {
        background: linear-gradient(135deg, #667eea 0%, #764ba2 100%);
        color: white;
        border: none;
        padding: 0.75rem 2rem;
        border-radius: 25px;
        font-weight: bold;
    }
</style>
""", unsafe_allow_html=True)

def check_api_health():
    """Проверка статуса API."""
    try:
        response = requests.get(f"{API_URL}/health", timeout=2)
        return response.status_code == 200
    except:
        return False

def send_message(message: str, user_role: str) -> dict:
    """Отправка сообщения в API."""
    try:
        response = requests.post(
            f"{API_URL}/chat",
            json={
                "message": message,
                "user_role": user_role
            },
            timeout=120
        )
        response.raise_for_status()
        return response.json()
    except Exception as e:
        return {"error": str(e)}

def init_session_state():
    """Инициализация состояния сессии."""
    if "messages" not in st.session_state:
        st.session_state.messages = []
    if "user_role" not in st.session_state:
        st.session_state.user_role = "junior"

def main():
    """Главная функция Streamlit приложения."""
    init_session_state()
    
    # Заголовок
    st.markdown("""
    <div class="main-header">
        <h1>🤖 SovereignAI Analyst</h1>
        <p>GraphRAG Multi-Agent Intelligence Platform</p>
    </div>
    """, unsafe_allow_html=True)
    
    # Сайдбар с настройками
    with st.sidebar:
        st.header("⚙️ Настройки")
        
        # Проверка API
        api_status = check_api_health()
        if api_status:
            st.success("✅ API Connected")
        else:
            st.error("❌ API Offline")
            st.warning("Запустите: `cd backend/api && python main.py`")
        
        st.divider()
        
        # Выбор роли
        st.subheader(" Роль пользователя")
        role = st.radio(
            "Выберите роль:",
            ["junior", "senior", "admin"],
            format_func=lambda x: {
                "junior": "👤 Junior Analyst",
                "senior": " Senior Analyst",
                "admin": "🔑 Administrator"
            }[x],
            index=["junior", "senior", "admin"].index(st.session_state.user_role)
        )
        st.session_state.user_role = role
        
        st.divider()
        
        # Информация о безопасности
        st.subheader("🔒 Security Features")
        st.markdown("""
        <span class="security-badge">🛡️ RBAC Access Control</span>
        <span class="security-badge">🔐 PII Data Masking</span>
        <span class="security-badge">✅ Input/Output Guardrails</span>
        <span class="security-badge">📝 Audit Logging</span>
        <span class="security-badge">🔄 Plan-Observe-Act</span>
        """, unsafe_allow_html=True)
        
        st.divider()
        
        # Кнопка очистки чата
        if st.button("🗑️ Очистить чат"):
            st.session_state.messages = []
            st.rerun()
        
        # Примеры запросов
        st.subheader(" Примеры запросов")
        examples = [
            "Найди договоры с высоким риском",
            "Покажи связанные компании с ООО Ромашка",
            "Какие документы у ООО Ромашка?",
            "Проанализируй риски в договоре DOG-001"
        ]
        for example in examples:
            if st.button(example, key=f"example_{example}"):
                st.session_state.messages.append({"role": "user", "content": example})
                st.rerun()
    
    # Основная область чата
    st.subheader("💬 Чат с AI Аналитиком")
    
    # Отображение истории сообщений
    for msg in st.session_state.messages:
        if msg["role"] == "user":
            st.markdown(
                f'<div class="chat-message user-message">{msg["content"]}</div>',
                unsafe_allow_html=True
            )
        elif msg["role"] == "assistant":
            st.markdown(
                f'<div class="chat-message assistant-message">{msg["content"]}</div>',
                unsafe_allow_html=True
            )
        elif msg["role"] == "error":
            st.markdown(
                f'<div class="error-message">❌ {msg["content"]}</div>',
                unsafe_allow_html=True
            )
    
    # Поле ввода
    if not api_status:
        st.warning("⚠️ API недоступен. Запустите сервер перед отправкой сообщений.")
    
    with st.form("chat_form", clear_on_submit=True):
        user_input = st.text_area(
            "Ваш вопрос:",
            placeholder="Введите ваш вопрос здесь...",
            height=80,
            label_visibility="collapsed",
            disabled=not api_status
        )
        
        submit_button = st.form_submit_button("🚀 Отправить", use_container_width=True)
        
        if submit_button and user_input.strip() and api_status:
            # Добавляем сообщение пользователя
            st.session_state.messages.append({"role": "user", "content": user_input})
            
            # Показываем индикатор загрузки
            with st.spinner("🤔 Агенты анализируют запрос..."):
                # Отправляем запрос в API
                response = send_message(user_input, st.session_state.user_role)
                
                if "error" in response:
                    st.session_state.messages.append({
                        "role": "error",
                        "content": f"Ошибка: {response['error']}"
                    })
                else:
                    ai_response = response.get("response", "Нет ответа")
                    st.session_state.messages.append({
                        "role": "assistant",
                        "content": ai_response
                    })
            
            # Обновляем страницу
            st.rerun()
    
    # Футер с информацией
    st.divider()
    st.markdown("""
    <div style="text-align: center; color: #666; padding: 1rem;">
        <p><strong>SovereignAI Analyst</strong> | GraphRAG Multi-Agent Platform</p>
        <p>Framework: Streamlit + FastAPI + LangGraph | LLM: Qwen 2.5 via Ollama</p>
        <p>Databases: Neo4j (Graph) + Qdrant (Vector) + PostgreSQL</p>
    </div>
    """, unsafe_allow_html=True)

if __name__ == "__main__":
    main()
