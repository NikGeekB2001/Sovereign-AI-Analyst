"""
RAG Retriever & Reranker for SovereignAI Analyst.
Implements semantic search with Qdrant + semantic reranking.

Схема согласована с data/load_to_dbs.py:
  Qdrant collection: ruslawod (env: QDRANT_COLLECTION)
  payload: act_id, title, doc_type, date, status, authority, text_preview, access_level
  Эмбеддинги: nomic-embed-text через Ollama /api/embed (768 dims)
"""

import os
from typing import List, Dict, Any
from qdrant_client import QdrantClient
from qdrant_client.http import models
import requests
import numpy as np


class RAGRetriever:
    """Semantic retriever using local embedding model."""

    def __init__(self, qdrant_url: str = None,
                 collection_name: str = None,
                 embedding_model_url: str = None,
                 embedding_model: str = None):
        qdrant_url = qdrant_url or os.getenv(
            "QDRANT_URL",
            f"http://{os.getenv('QDRANT_HOST', 'localhost')}:{os.getenv('QDRANT_PORT', '6333')}",
        )
        self.qdrant_client = QdrantClient(url=qdrant_url)
        self.collection_name = collection_name or os.getenv("QDRANT_COLLECTION", "ruslawod")
        self.embedding_model_url = embedding_model_url or os.getenv("OLLAMA_BASE_URL", "http://localhost:11434")
        self.embedding_model = embedding_model or os.getenv("EMBEDDING_MODEL", "nomic-embed-text")
        self.vector_size = 768  # nomic-embed-text

    def get_embedding(self, text: str) -> List[float]:
        """Получение эмбеддинга через Ollama (/api/embed, nomic-embed-text)."""
        try:
            response = requests.post(
                f"{self.embedding_model_url}/api/embed",
                json={
                    "model": self.embedding_model,
                    "input": text
                },
                timeout=30
            )
            response.raise_for_status()
            embeddings = response.json().get("embeddings") or []
            if embeddings:
                return embeddings[0]
            raise ValueError("Пустой ответ эмбеддинга")
        except Exception as e:
            print(f"⚠️ Ошибка получения эмбеддинга: {e}")
            # Fallback: хеш-вектор той же размерности (768)
            return self._fallback_embedding(text)

    def _fallback_embedding(self, text: str) -> List[float]:
        """Fallback эмбеддинг на основе хеша текста (размерность 768)."""
        import hashlib
        hash_obj = hashlib.md5(text.encode("utf-8"))
        hash_bytes = hash_obj.digest()
        vector = list(np.frombuffer(hash_bytes, dtype=np.uint8).astype(float) / 255.0)
        # Повторяем до 768
        vector = (vector * (self.vector_size // len(vector)) +
                  vector[: self.vector_size % len(vector)])
        return vector

    def retrieve(self, query: str, user_role: str = "куратор",
                 top_k: int = 3, min_score: float = 0.3) -> List[Dict[str, Any]]:
        """
        Поиск релевантных документов в Qdrant.

        Args:
            query: Поисковый запрос
            user_role: Роль пользователя (для RBAC)
            top_k: Количество результатов
            min_score: Минимальный порог похожести

        Returns:
            Список документов с метаданными
        """
        print(f"🔍 Retriever: Поиск по запросу '{query[:50]}...'")

        # 1. Получаем эмбеддинг запроса
        query_vector = self.get_embedding(query)

        # 2. Формируем фильтр по RBAC (поле access_level в payload)
        rbac_filter = None
        if user_role == "admin":
            rbac_filter = models.Filter(must=[
                models.FieldCondition(key="access_level", match=models.MatchAny(any=["public", "internal", "restricted"]))
            ])
        elif user_role == "специалист отдела":
            rbac_filter = models.Filter(must=[
                models.FieldCondition(key="access_level", match=models.MatchAny(any=["public", "internal"]))
            ])
        else:  # куратор
            rbac_filter = models.Filter(must=[
                models.FieldCondition(key="access_level", match=models.MatchValue(value="public"))
            ])

        # 3. Поиск в Qdrant
        try:
            search_results = self.qdrant_client.query_points(
                collection_name=self.collection_name,
                query=query_vector,
                limit=top_k * 2,  # Берем больше для реранкинга
                query_filter=rbac_filter,
                with_payload=True,
                with_vectors=False
            )

            points = search_results.points if hasattr(search_results, 'points') else []

            # 4. Фильтрация по минимальному score
            filtered_results = []
            for point in points:
                if point.score >= min_score:
                    filtered_results.append({
                        "id": point.id,
                        "text": point.payload.get("text_preview", ""),
                        "doc_type": point.payload.get("doc_type", ""),
                        "act_id": point.payload.get("act_id", ""),
                        "score": point.score,
                        "access_level": point.payload.get("access_level", "public")
                    })

            print(f"✅ Retriever: Найдено {len(filtered_results)} документов")
            return filtered_results

        except Exception as e:
            print(f"⚠️ Ошибка поиска в Qdrant: {e}")
            return []


class RAGReranker:
    """Семантический реранкер для улучшения результатов поиска."""

    def __init__(self, reranker_model_url: str = None, model: str = None):
        self.model_url = reranker_model_url or os.getenv("OLLAMA_BASE_URL", "http://localhost:11434")
        self.model = model or os.getenv("RERANKER_MODEL", "qwen2.5:7b")

    def rerank(self, query: str, documents: List[Dict[str, Any]],
               top_k: int = 2) -> List[Dict[str, Any]]:
        """
        Реранкинг документов по релевантности запросу.

        Использует LLM для оценки релевантности каждого документа.

        Args:
            query: Исходный запрос
            documents: Список документов от retriever
            top_k: Количество топ результатов

        Returns:
            Отсортированный список документов
        """
        if not documents:
            return []

        print(f"🔄 Reranker: Оценка {len(documents)} документов...")

        # Для каждого документа оцениваем релевантность через LLM
        scored_docs = []
        for doc in documents:
            relevance_score = self._calculate_relevance(query, doc["text"])
            # Комбинируем score от retriever и reranker
            combined_score = (doc["score"] * 0.4) + (relevance_score * 0.6)
            doc["combined_score"] = combined_score
            doc["relevance_score"] = relevance_score
            scored_docs.append(doc)

        # Сортируем по combined_score
        scored_docs.sort(key=lambda x: x["combined_score"], reverse=True)

        # Возвращаем топ-K
        results = scored_docs[:top_k]
        print(f"🔄 Reranker: Возвращено {len(results)} документов")

        return results

    def _calculate_relevance(self, query: str, document_text: str) -> float:
        """
        Оценка релевантности документа запросу через LLM.

        Returns:
            Score от 0.0 до 1.0
        """
        prompt = f"""Оцени релевантность документа запросу по шкале от 0 до 1.

Запрос: {query}

Документ: {document_text[:500]}

Критерии:
- 1.0: Документ напрямую отвечает на запрос
- 0.7: Документ частично релевантен
- 0.3: Документ слабо связан с запросом
- 0.0: Документ не релевантен

Ответь только числом от 0.0 до 1.0:"""

        try:
            response = requests.post(
                f"{self.model_url}/api/generate",
                json={
                    "model": self.model,
                    "prompt": prompt,
                    "stream": False,
                    "options": {
                        "temperature": 0.1,
                        "num_predict": 10
                    }
                },
                timeout=15
            )
            response.raise_for_status()
            result = response.json()["response"].strip()

            # Парсим число из ответа
            import re
            match = re.search(r'(\d+\.\d+|\d+)', result)
            if match:
                score = float(match.group(1))
                return max(0.0, min(1.0, score))  # Ограничиваем [0, 1]

            return 0.5  # Default score

        except Exception as e:
            print(f"⚠️ Ошибка реранкинга: {e}")
            return 0.5  # Default score


# Глобальные инстансы для переиспользования
_retriever = None
_reranker = None


def get_retriever() -> RAGRetriever:
    """Получить или создать RAGRetriever."""
    global _retriever
    if _retriever is None:
        _retriever = RAGRetriever()
    return _retriever


def get_reranker() -> RAGReranker:
    """Получить или создать RAGReranker."""
    global _reranker
    if _reranker is None:
        _reranker = RAGReranker()
    return _reranker
