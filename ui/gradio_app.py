"""
Gradio UI для SovereignAI Analyst.
Современный и красивый чат-интерфейс на русском языке.
"""

import gradio as gr
import requests
from datetime import datetime
import sys
import os

# Добавляем путь к корню проекта для импорта агентов
sys.path.append(os.path.join(os.path.dirname(__file__), '..'))

# Изменяем импорт для правильного пути к langfuse_integration
from backend.api.langfuse_integration import langfuse, get_trace, flush_langfuse, create_trace_with_fallback

# Конфигурация
API_URL = "http://localhost:8000"

def send_message(message: str, user_role: str, history: list) -> tuple:
    """Отправка сообщения в API и обновление истории."""
    
    # Создаем трейс в Langfuse для всего чата
    trace = create_trace_with_fallback(
        name="sov-chat",
        metadata={"message": message, "user_role": user_role, "interface": "sov-ui"}
    )
    
    if not message or not message.strip():
        # Обновляем трейс с результатом
        trace.update(
            output="Пустое сообщение",
            metadata={"status": "error", "error_type": "empty_message"}
        )
        flush_langfuse()
        return history, ""
    
    # Добавляем сообщение пользователя в историю
    history.append({"role": "user", "content": message})
    
    print(f"[DEBUG] Отправка сообщения: {message[:50]}...")  # Добавляем отладочное сообщение
    
    try:
        # Отправляем запрос в API
        response = requests.post(
            f"{API_URL}/chat",
            json={
                "message": message,
                "user_role": user_role
            },
            timeout=120
        )
        response.raise_for_status()
        result = response.json()
        
        ai_response = result.get("response", "Нет ответа")
        if not ai_response:
            ai_response = "Пустой ответ от API"
        
        history.append({"role": "assistant", "content": ai_response})
        
        # Обновляем трейс с результатом
        trace.update(output={"response": ai_response, "status": "success"})
        
    except Exception as e:
        print(f"[SovUI] Ошибка: {e}")
        import traceback
        traceback.print_exc()  # Добавляем полный стек трейса для отладки
        error_msg = f"❌ Ошибка: {str(e)}"
        history.append({"role": "assistant", "content": error_msg})
        # Обновляем трейс с ошибкой
        trace.update(output={"response": error_msg, "status": "error", "error": str(e)}, level="ERROR", status_message=str(e))
    
    # Принудительно отправляем трейс в Langfuse
    flush_langfuse()
    return history, ""

def clear_chat():
    """Очистка чата."""
    return []

def example_click(example: str) -> str:
    """Обработка клика по примеру."""
    return example

def main():
    """Создание интерфейса SovereignAI."""
    
    # Тема оформления (для Gradio 6.x используем строку)
    theme = "soft"
    
    with gr.Blocks(
        title="SovereignAI Analyst - Интерфейс"
    ) as demo:
        
        # Заголовок
        gr.Markdown(
            """
            <div class="main-header">
                <h1> 🤖 SovereignAI Analyst</h1>
                <p>Графо-аналитическая платформа многоагентного интеллекта</p>
            </div>
            """,
            elem_classes="main-header"
        )
        
        # Состояние
        chat_history = gr.State([])
        
        with gr.Row():
            # Левая панель (сайдбар)
            with gr.Column(scale=1):
                gr.Markdown("### ⚙️ Настройки")
                
                # Выбор роли
                gr.Markdown("### 👤 Роль пользователя")
                user_role = gr.Radio(
                    choices=["junior", "senior", "admin"],
                    label="Выберите роль:",
                    value="junior",
                    info="Уровень доступа определяет глубину анализа"
                )
                
                gr.Markdown("---")
                
                # Security Features
                gr.Markdown("### 🔒 Функции безопасности")
                gr.Markdown(
                    """
                    <div>
                        <span class="security-badge">🛡️ Контроль доступа RBAC</span>
                        <span class="security-badge">🔐 Маскировка персональных данных</span>
                        <span class="security-badge">✅ Защитные меры ввода/вывода</span>
                        <span class="security-badge">📝 Журнал аудита</span>
                        <span class="security-badge">🔄 Планирование-Наблюдение-Действие</span>
                    </div>
                    """
                )
                
                gr.Markdown("---")
                
                # Кнопки управления
                clear_btn = gr.Button("🗑️ Очистить чат", variant="secondary")
                
                gr.Markdown("---")
                
                # Примеры запросов
                gr.Markdown("### 💡 Примеры запросов")
                examples = [
                    "Найди договоры с высоким риском",
                    "Покажи связанные компании с ООО Ромашка",
                    "Какие документы у ООО Ромашка?",
                    "Проанализируй риски в договоре DOG-001"
                ]
                
                for example in examples:
                    btn = gr.Button(example, variant="outline", size="sm")
                    btn.click(
                        fn=lambda ex=example: ex,
                        outputs=[gr.Textbox(visible=False)]
                    )
            
            # Правая панель (чат)
            with gr.Column(scale=3):
                gr.Markdown("### 💬 Чат с AI Аналитиком")
                
                # Чат-интерфейс
                chatbot = gr.Chatbot(
                    label="Диалог",
                    height=400
                )
                
                # Поле ввода (снизу)
                with gr.Row():
                    msg_input = gr.Textbox(
                        placeholder="Введите ваш вопрос здесь...",
                        show_label=False,
                        scale=5,
                        container=False
                    )
                    send_btn = gr.Button(" Отправить", variant="primary", scale=1)
        
        # Футер
        gr.Markdown("---")
        gr.Markdown(
            """
            <div style="text-align: center; color: #666; padding: 1rem;">
                <p><strong>SovereignAI Analyst</strong> | Графо-аналитическая платформа многоагентного интеллекта</p>
                <p>Фреймворк: FastAPI + LangGraph | ЯП: Qwen 2.5 через Ollama</p>
                <p>Базы данных: Neo4j (Графовая) + Qdrant (Векторная) + PostgreSQL</p>
                <p><strong>Наблюдаемость Langfuse активна</strong> | Трассировка доступна по адресу: <a href="http://localhost:3000" target="_blank">http://localhost:3000</a></p>
            </div>
            """
        )
        
        # Отправка по Enter
        msg_input.submit(
            fn=send_message,
            inputs=[msg_input, user_role, chat_history],
            outputs=[chatbot, msg_input]
        ).then(
            fn=lambda: "",  # Clear the input textbox after sending
            outputs=[msg_input]
        )
        
        # Отправка сообщения
        send_btn.click(
            fn=send_message,
            inputs=[msg_input, user_role, chat_history],
            outputs=[chatbot, msg_input]
        ).then(
            fn=lambda: "",  # Clear the input textbox after sending
            outputs=[msg_input]
        )
        
        # Очистка чата
        clear_btn.click(
            fn=clear_chat,
            outputs=[chatbot]
        )
    
    return demo

if __name__ == "__main__":
    demo = main()
    
    # Попробуем запустить на порту 7861, если он свободен, иначе найдем свободный порт
    try:
        demo.launch(
            server_name="127.0.0.1",
            server_port=7861,  # Попробуем стандартный порт
            share=False,
            inbrowser=True
        )
    except OSError as e:
        if "Cannot find empty port" in str(e):
            print(f"Порт 7861 занят, ищу свободный порт...")
            # Автоматически найдет свободный порт
            demo.launch(
                server_name="127.0.0.1",
                share=False,
                inbrowser=True
            )
        else:
            raise e