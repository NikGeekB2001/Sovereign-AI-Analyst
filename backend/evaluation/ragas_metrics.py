# -*- coding: utf-8 -*-
"""RAGAS-стиль метрики качества RAG-конвейера (лёгкая реализация, без зависимостей ragas).

Judge-LLM: Ollama/vLLM через UnifiedLLMClient (по умолчанию qwen2.5:7b).
Эмбеддинги: bge-m3 через RAGRetriever.get_embedding.

Метрики:
  - faithfulness       — доля утверждений ответа, подтверждённых контекстом
  - answer_relevancy   — средний косинус между эмбеддингом вопроса и вопросами,
                         сгенерированными из ответа
  - context_precision  — насколько релевантные фрагменты стоят высоко в выдаче
                         (формула RAGAS: sum(precision@k * rel_k) / total_relevant)
  - context_recall     — доля утверждений эталонного ответа, покрытых контекстом
  - hallucination_rate — 1 - faithfulness

Документация: docs/PERFORMANCE_METRICS.md
"""
from __future__ import annotations

import json
import os
from typing import Dict, List, Optional

from backend.services.rag_retriever import get_retriever
from backend.services.unified_llm_client import get_unified_client, UnifiedLLMClient


# ---------------------------------------------------------------------------
# Чистые функции (покрыты unit-тестами, без LLM)
# ---------------------------------------------------------------------------

def cosine_similarity(a: List[float], b: List[float]) -> float:
    """Косинусная близость двух векторов (чистый Python, без numpy)."""
    if not a or len(a) != len(b):
        return 0.0
    dot = sum(x * y for x, y in zip(a, b))
    na = sum(x * x for x in a) ** 0.5
    nb = sum(x * x for x in b) ** 0.5
    if na == 0.0 or nb == 0.0:
        return 0.0
    return dot / (na * nb)


def precision_from_relevance(relevance: List[int]) -> float:
    """RAGAS context_precision по вектору релевантности [1,0,...] (порядок выдачи)."""
    total = sum(relevance)
    if total == 0:
        return 0.0
    acc = 0.0
    for k in range(1, len(relevance) + 1):
        acc += (sum(relevance[:k]) / k) * relevance[k - 1]
    return acc / total


def supported_ratio(claims: List[str], supported: List[int]) -> float:
    """Доля подтверждённых утверждений (faithfulness / context_recall)."""
    if not claims:
        return 0.0
    hits = sum(1 for i in range(len(claims)) if i < len(supported) and supported[i])
    return hits / len(claims)


def _extract_json(text: str) -> Optional[dict]:
    """Достаёт первый валидный JSON-объект из текста модели (устойчиво к мусору)."""
    if not text:
        return None
    start = text.find("{")
    if start == -1:
        return None
    depth = 0
    in_str = False
    esc = False
    for i in range(start, len(text)):
        c = text[i]
        if in_str:
            if esc:
                esc = False
            elif c == "\\":
                esc = True
            elif c == '"':
                in_str = False
        else:
            if c == '"':
                in_str = True
            elif c == "{":
                depth += 1
            elif c == "}":
                depth -= 1
                if depth == 0:
                    try:
                        return json.loads(text[start:i + 1])
                    except Exception:
                        return None
    return None


# ---------------------------------------------------------------------------
# Промпты judge-модели
# ---------------------------------------------------------------------------

SYS_JUDGE = (
    "Ты — оценщик качества RAG-системы. Отвечай строго в формате JSON, "
    "без пояснений и markdown-разметки."
)

def _p_claims(answer: str) -> str:
    return (
        "Извлеки из приведённого ответа все отдельные фактические утверждения "
        "(каждое — самостоятельный факт, без вводных слов и оценок).\n\n"
        f"Ответ:\n{answer}\n\n"
        'Верни JSON: {"claims": ["утверждение 1", "утверждение 2", ...]}'
    )

def _p_supported(claims: List[str], contexts: List[str]) -> str:
    ctx = "\n".join(f"{i + 1}. {c[:400]}" for i, c in enumerate(contexts))
    cl = "\n".join(f"{i + 1}. {c}" for i, c in enumerate(claims))
    return (
        "Контекст (фрагменты документов):\n"
        f"{ctx or '(пусто)'}\n\n"
        "Утверждения:\n"
        f"{cl}\n\n"
        "Для каждого утверждения определи, подтверждается ли оно контекстом: "
        "1 — да, 0 — нет. Количество ответов должно совпадать с количеством утверждений.\n"
        'Верни JSON: {"supported": [1, 0, ...]}'
    )

def _p_questions(answer: str) -> str:
    return (
        "Сформулируй 3 разных вопроса, на которые отвечает приведённый ответ "
        "(вопросы должны быть понятны без контекста).\n\n"
        f"Ответ:\n{answer}\n\n"
        'Верни JSON: {"questions": ["вопрос 1", "вопрос 2", "вопрос 3"]}'
    )

def _p_relevant(question: str, contexts: List[str]) -> str:
    ctx = "\n".join(f"{i + 1}. {c[:400]}" for i, c in enumerate(contexts))
    return (
        f"Вопрос: {question}\n\n"
        "Фрагменты контекста:\n"
        f"{ctx}\n\n"
        "Для каждого фрагмента верни 1, если он помогает ответить на вопрос, иначе 0.\n"
        'Верни JSON: {"relevant": [1, 0, ...]}'
    )


# ---------------------------------------------------------------------------
# Оценщик
# ---------------------------------------------------------------------------

class RagasEvaluator:
    def __init__(self, model: Optional[str] = None, role: str = "куратор",
                 top_k: int = 5, temperature: float = 0.0, backend: Optional[str] = None):
        if backend is None:
            backend = os.getenv("LLM_JUDGE_BACKEND", "")
        if backend:
            if model is None:
                model = "GigaChat" if backend == "gigachat" else None
            self.llm = UnifiedLLMClient(backend=backend, model=model or "qwen2.5:7b")
        else:
            self.llm = get_unified_client(model=model)
        self.retriever = get_retriever()
        self.role = role
        self.top_k = top_k
        self.temperature = temperature

    # --- вызовы judge ---
    def _ask(self, prompt: str, max_tokens: int = 1024) -> str:
        res = self.llm.generate(
            prompt=prompt,
            system_prompt=SYS_JUDGE,
            temperature=self.temperature,
            max_tokens=max_tokens,
            json_mode=True,
        )
        return (res.get("response") or "").strip()

    def _extract_claims(self, text: str) -> List[str]:
        data = _extract_json(self._ask(_p_claims(text)))
        claims = (data or {}).get("claims", [])
        return [str(c).strip() for c in claims if str(c).strip()]

    def _check_supported(self, claims: List[str], contexts: List[str]) -> List[int]:
        if not claims:
            return []
        data = _extract_json(self._ask(_p_supported(claims, contexts)))
        raw = (data or {}).get("supported", [])
        out = []
        for x in raw[:len(claims)]:
            s = str(x).strip().lower()
            out.append(1 if s in ("1", "true", "да", "yes", "подтверждено") else 0)
        while len(out) < len(claims):
            out.append(0)
        return out

    def _gen_questions(self, answer: str) -> List[str]:
        data = _extract_json(self._ask(_p_questions(answer)))
        qs = (data or {}).get("questions", [])
        return [str(q).strip() for q in qs if str(q).strip()]

    def _context_relevance(self, question: str, contexts: List[str]) -> List[int]:
        if not contexts:
            return []
        data = _extract_json(self._ask(_p_relevant(question, contexts)))
        raw = (data or {}).get("relevant", [])
        out = []
        for x in raw[:len(contexts)]:
            s = str(x).strip().lower()
            out.append(1 if s in ("1", "true", "да", "yes", "релевантен") else 0)
        while len(out) < len(contexts):
            out.append(0)
        return out

    # --- метрики ---
    def faithfulness(self, answer: str, contexts: List[str]) -> float:
        claims = self._extract_claims(answer)
        if not claims:
            return 0.0
        return round(supported_ratio(claims, self._check_supported(claims, contexts)), 4)

    def answer_relevancy(self, question: str, answer: str) -> float:
        qs = self._gen_questions(answer)
        if not qs:
            return 0.0
        q_emb = self.retriever.get_embedding(question)
        sims = [cosine_similarity(q_emb, self.retriever.get_embedding(q)) for q in qs]
        return round(sum(sims) / len(sims), 4) if sims else 0.0

    def context_precision(self, question: str, contexts: List[str]) -> float:
        rel = self._context_relevance(question, contexts)
        return round(precision_from_relevance(rel), 4)

    def context_recall(self, question: str, contexts: List[str], ground_truth: str) -> float:
        claims = self._extract_claims(ground_truth)
        if not claims:
            return 0.0
        return round(supported_ratio(claims, self._check_supported(claims, contexts)), 4)

    def evaluate_answer(self, question: str, answer: str, contexts: List[str],
                        ground_truth: Optional[str] = None) -> Dict[str, float]:
        out: Dict[str, float] = {}
        out["faithfulness"] = self.faithfulness(answer, contexts)
        out["answer_relevancy"] = self.answer_relevancy(question, answer)
        out["context_precision"] = self.context_precision(question, contexts)
        if ground_truth:
            out["context_recall"] = self.context_recall(question, contexts, ground_truth)
        out["hallucination_rate"] = round(1.0 - out["faithfulness"], 4)
        return out

    # --- полный прогон по золотому набору ---
    def evaluate_retrieval(self, question: str) -> List[str]:
        """Только ретрив: контексты, которые увидит конвейер при заданной роли."""
        docs = self.retriever.retrieve(question, user_role=self.role, top_k=self.top_k)
        return [d["text"] for d in docs]
