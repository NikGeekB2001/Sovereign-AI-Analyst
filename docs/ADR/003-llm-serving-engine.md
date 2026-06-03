# ADR-003: Выбор LLM Serving Engine и стратегия квантования

## Статус
Принято

## Контекст
Необходимо выбрать высокопроизводительный LLM Serving сервер для запуска моделей в air-gapped среде (актуальность: 2026, РФ). Требования:
- Поддержка квантования AWQ/GGUF для запуска на Consumer GPU (RTX 3070/4090, 8-24GB VRAM)
- KV-cache оптимизация для максимального throughput
- Совместимость с OpenAI API для интеграции с LangGraph
- Self-hosted, без внешних зависимостей

## Решение
Выбран **vLLM** как основной LLM Serving Engine с квантованием **AWQ** для Consumer GPU.

### Конфигурация инференса
| Компонент | Модель | Квантование | VRAM | Назначение |
|-----------|--------|-------------|------|------------|
| vLLM Instance 1 | Qwen2.5-32B-Instruct | AWQ 4-bit | ~20GB (GPU 0) | Основной инференс (Planner, Synthesizer) |
| vLLM Instance 2 | Qwen2.5-32B-Instruct | AWQ 4-bit | ~20GB (GPU 1) | Параллельный инференс (Retriever, Analyst) |
| vLLM Load Balancer | — | — | — | Round-robin между инстансами |

### Квантование: AWQ vs GGUF
- **AWQ** выбран для vLLM: активация-осознанное квантование, минимальная потеря качества (<2% perplexity), нативная поддержка в vLLM
- **GGUF** рассмотрен для llama.cpp: лучше для CPU offloading, но ниже throughput

## Обоснование (Trade-off Analysis)

### Почему vLLM?

**Преимущества:**
1. **PagedAttention:** Оптимизация KV-cache, сокращение использования VRAM на 30-50% по сравнению с наивной реализацией
2. **Continuous Batching:** Динамическая группировка запросов, максимальный GPU utilization
3. **AWQ Support:** Нативная поддержка AWQ квантования без конвертации
4. **OpenAI-Compatible API:** `/v1/completions`, `/v1/chat/completions` — прозрачная интеграция с LangGraph
5. **Высокий Throughput:** 2-4x быстрее TGI при одинаковых условиях (бенчмарки 2025-2026)
6. **Активное сообщество:** 30k+ GitHub stars, регулярные релизы, поддержка новых архитектур

### Почему не SGLang?

**Преимущества SGLang:**
- RadixAttention для prefix caching (ускорение повторных промптов)
- Быстрее на определенных бенчмарках (single-request latency)

**Недостатки SGLang для нашего случая:**
1. **Меньшая зрелость:** Проект моложе vLLM, меньше production deployments
2. **Ограниченная поддержка квантования:** AWQ поддержка менее стабильна
3. **Меньшее сообщество:** 8k vs 30k stars, меньше документации
4. **Совместимость:** Меньше проверенных интеграций с LangGraph/Langfuse

### Почему не TGI (Text Generation Inference)?

**Преимущества TGI:**
- HuggingFace экосистема, Flash Attention
- Хорошая документация

**Недостатки TGI для нашего случая:**
1. **Нижний throughput:** vLLM показывает 2-4x преимущество в throughput
2. **Ограниченный continuous batching:** Менее агрессивная оптимизация batching
3. **Зависимость от HuggingFace:** Тяжелые зависимости, сложнее в air-gapped развертывании
4. **Медленнее поддержка новых моделей:** Задержка 2-4 недели после релиза моделей

### Почему не llama.cpp?

**Преимущества llama.cpp:**
- GGUF формат, CPU offloading, минимальные зависимости
- Идеален для одной модели на consumer GPU

**Недостатки llama.cpp для нашего случая:**
1. **Нет continuous batching:** Обработка по одному запросу
2. **Нижний throughput:** Не оптимизирован для множественных параллельных запросов
3. **Нет OpenAI-compatible server:** Требуется кастомная обертка
4. **Не масштабируется:** Сложно балансировать нагрузку между инстансами

## KV-Cache Optimization

### PagedAttention (vLLM)
```
Traditional:  KV-cache = seq_len x hidden_dim x 2 (K+V) x batch_size
PagedAttention: KV-cache разбит на блоки, аллоцируются по требованию

Экономия VRAM: 30-50% для типичных рабочих нагрузок
```

### Конфигурация vLLM
```bash
python -m vllm.entrypoints.openai.api_server \
    --model Qwen/Qwen2.5-32B-Instruct-AWQ \
    --quantization awq \
    --gpu-memory-utilization 0.90 \
    --max-model-len 8192 \
    --enable-prefix-caching \
    --block-size 16 \
    --swap-space 4
```

## LLM Registry + Circular Fallback (Production)

### Архитектура
```
LangGraph Orchestrator
    ↓
LLM Service (LLMRegistry + tenacity)
    ↓
┌─────────────────────────────────────────┐
│ LLM Registry (Circular Fallback)       │
│                                         │
│  [0] Qwen2.5-32B AWQ (vLLM Instance 1) │ ← Default
│  [1] Qwen2.5-32B AWQ (vLLM Instance 2) │ ← Fallback 1
│  [2] Qwen2.5-14B AWQ (vLLM Instance 3) │ ← Fallback 2
│  [3] T-lite-7B AWQ (vLLM Instance 4)   │ ← Fallback 3
│                                         │
│  Circular: 0 → 1 → 2 → 3 → 0 → ...    │
└─────────────────────────────────────────┘
```

### LLM Registry Implementation
```python
from tenacity import retry, stop_after_attempt, wait_exponential, retry_if_exception_type
from openai import RateLimitError, APITimeoutError, APIError

class LLMRegistry:
    """Registry of LLM models with circular fallback."""
    LLMS = [
        {"name": "qwen-32b-awq-gpu0", "llm": ChatOpenAI(model="qwen-32b-awq", base_url="http://vllm-1:8000/v1")},
        {"name": "qwen-32b-awq-gpu1", "llm": ChatOpenAI(model="qwen-32b-awq", base_url="http://vllm-2:8000/v1")},
        {"name": "qwen-14b-awq", "llm": ChatOpenAI(model="qwen-14b-awq", base_url="http://vllm-3:8000/v1")},
        {"name": "t-lite-7b-awq", "llm": ChatOpenAI(model="t-lite-7b-awq", base_url="http://vllm-4:8000/v1")},
    ]

class LLMService:
    """LLM Service with circular fallback and retries."""

    def __init__(self):
        self._current_model_index = 0
        self._llm = LLMRegistry.LLMS[0]["llm"]
        self._bound_tools = []

    @retry(
        stop=stop_after_attempt(3),
        wait=wait_exponential(multiplier=1, min=2, max=10),
        retry=retry_if_exception_type((RateLimitError, APITimeoutError, APIError)),
    )
    async def _invoke_with_retry(self, llm, messages):
        return await llm.ainvoke(messages)

    def _switch_to_next_model(self) -> bool:
        """Circular fallback: switch to next model in registry."""
        next_index = (self._current_model_index + 1) % len(LLMRegistry.LLMS)
        self._current_model_index = next_index
        self._llm = LLMRegistry.LLMS[next_index]["llm"]
        if self._bound_tools:
            self._llm = self._llm.bind_tools(self._bound_tools)
        return True

    async def call(self, messages, model_name=None, response_format=None):
        """Call LLM with circular fallback and timeout budget."""
        try:
            return await asyncio.wait_for(
                self._call_with_fallback(messages, model_name, response_format),
                timeout=60,  # Total timeout budget
            )
        except asyncio.TimeoutError:
            raise RuntimeError("LLM call timed out after 60s total budget")
```

### Fallback Strategy
| Событие | Действие |
|---------|----------|
| RateLimitError | Retry (exponential backoff) → Circular Fallback |
| APITimeoutError | Retry → Circular Fallback |
| APIError | Circular Fallback (без retry) |
| Все модели недоступны | RuntimeError, HTTP 503 |
| Timeout budget (60s) | RuntimeError, HTTP 504 |

## Capacity Planning

### VRAM Requirements
| Конфигурация | VRAM на инстанс | GPU | Throughput |
|--------------|-----------------|-----|------------|
| Qwen2.5-32B AWQ | ~20GB | RTX 4090 / A100 | 15-25 tok/s |
| Qwen2.5-14B AWQ | ~10GB | RTX 4090 | 30-50 tok/s |
| Qwen2.5-7B AWQ | ~6GB | RTX 3070 | 60-100 tok/s |

### Performance Estimates (2x RTX 4090)
- **Concurrent requests:** 10-20 параллельных запросов
- **Latency (TTFT):** 200-500ms (Time To First Token)
- **Throughput:** 30-50 tok/s (с continuous batching)
- **KV-cache hit rate:** 60-80% (с prefix caching)

## Последствия

### Положительные
- ✅ Максимальный throughput на доступном оборудовании
- ✅ Экономия VRAM через PagedAttention + AWQ
- ✅ OpenAI-compatible API для прозрачной интеграции
- ✅ Горизонтальное масштабирование через Load Balancer

### Отрицательные
- ⚠️ Требуется GPU с >=20GB VRAM для 32B модели (AWQ)
- ⚠️ AWQ квантование дает небольшую потерю качества (~1-2%)
- ⚠️ Сложность настройки двух инстансов vLLM + Load Balancer

### Нейтральные
- Можно переключиться на SGLang при созревании проекта
- GGUF остается fallback для CPU-only сценариев

## Альтернативы рассмотрены
1. **SGLang** — отклонено (менее зрелый, ограниченная AWQ поддержка)
2. **TGI** — отклонено (нижний throughput, зависимость от HuggingFace)
3. **llama.cpp** — отклонено (нет continuous batching, не масштабируется)
4. **Ollama** — отклонено (обертка над llama.cpp, те же ограничения)

## Ссылки
- [vLLM Documentation](https://docs.vllm.ai/)
- [PagedAttention Paper](https://arxiv.org/abs/2309.06180)
- [AWQ Quantization](https://arxiv.org/abs/2306.00978)
- [SGLang](https://github.com/sgl-project/sglang)
- [TGI](https://github.com/huggingface/text-generation-inference)
