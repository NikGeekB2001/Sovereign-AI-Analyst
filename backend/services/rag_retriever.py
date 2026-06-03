"""
RAG Retriever & Reranker for SovereignAI Analyst.
Implements semantic search with Qdrant + semantic reranking.
"""

import os
from typing import List, Dict, Any
from qdrant_client import QdrantClient
from qdrant_client.http import models
import requests
import numpy as np

class RAGRetriever:
    """Semantic retriever using local embedding model."""
    
    def __init__(self, qdrant_url: str = "http://localhost:6333", 
                 collection_name: str = "legal_documents",
                 embedding_model_url: str = "http://localhost:11434"):
        self.qdrant_client = QdrantClient(url=qdrant_url)
        self.collection_name = collection_name
        self.embedding_model_url = embedding_model_url
        self.vector_size = 384  # all-MiniLM-L6-v2 размерность
    
    def get_embedding(self, text: str) -> List[float]:
        """Получение эмбеддинга через Ollama (nomic-embed-text)."""
        try:
            response = requests.post(
                f"{self.embedding_model_url}/api/embeddings",
                json={
                    "model": "nomic-embed-text",
                    "prompt": text
                },
                timeout=10
            )
            response.raise_for_status()
            return response.json()["embedding"]
        except Exception as e:
            print(f"️ Ошибка получения эмбеддинга: {e}")
            # Fallback: используем простой хеш-вектор
            return self._fallback_embedding(text)
    
    def _fallback_embedding(self, text: str) -> List[float]:
        """Fallback эмбеддинг на основе хеша текста."""
        import hashlib
        hash_obj = hashlib.md5(text.encode())
        hash_bytes = hash_obj.digest()
        # Создаем вектор из хеша
        vector = list(np.frombuffer(hash_bytes, dtype=np.uint8).astype(float) / 255.0)
        # Дополняем до 384
        vector = vector * (384 // len(vector)) + vector[:384 % len(vector)]
        return vector
    
    def retrieve(self, query: str, user_role: str = "junior", 
                 top_k: int = 3, min_score: float = 0.5) -> List[Dict[str, Any]]:
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
        
        # 2. Формируем фильтр по RBAC
        rbac_filter = None
        if user_role == "admin":
            rbac_filter = models.Filter(must=[
                models.FieldCondition(key="access_level", match=models.MatchAny(any=["public", "internal", "restricted"]))
            ])
        elif user_role == "senior":
            rbac_filter = models.Filter(must=[
                models.FieldCondition(key="access_level", match=models.MatchAny(any=["public", "internal"]))
            ])
        else:  # junior
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
                        "text": point.payload.get("text", ""),
                        "doc_type": point.payload.get("doc_type", ""),
                        "contract_id": point.payload.get("contract_id", ""),
                        "score": point.score,
                        "access_level": point.payload.get("access_level", "")
                    })
            
            print(f" Retriever: Найдено {len(filtered_results)} документов")
            return filtered_results
            
        except Exception as e:
            print(f" Ошибка поиска в Qdrant: {e}")
            return []


class RAGReranker:
    """Семантический реранкер для улучшения результатов поиска."""
    
    def __init__(self, reranker_model_url: str = "http://localhost:11434"):
        self.model_url = reranker_model_url
    
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
                    "model": "qwen2.5:7b",
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
