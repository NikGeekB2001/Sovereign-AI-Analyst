# -*- coding: utf-8 -*-
"""Unit-тесты чистых функций метрик RAGAS-стиля (без LLM и БД)."""
import pytest

from backend.evaluation.ragas_metrics import (
    _extract_json,
    cosine_similarity,
    precision_from_relevance,
    supported_ratio,
)


class TestCosineSimilarity:
    def test_identical(self):
        assert cosine_similarity([1.0, 0.0], [1.0, 0.0]) == pytest.approx(1.0)

    def test_orthogonal(self):
        assert cosine_similarity([1.0, 0.0], [0.0, 1.0]) == pytest.approx(0.0)

    def test_opposite(self):
        assert cosine_similarity([1.0, 0.0], [-1.0, 0.0]) == pytest.approx(-1.0)

    def test_mismatched_lengths(self):
        assert cosine_similarity([1.0], [1.0, 2.0]) == 0.0

    def test_zero_vector(self):
        assert cosine_similarity([0.0, 0.0], [1.0, 0.0]) == 0.0

    def test_partial(self):
        # a=[1,1], b=[1,0] -> 1/sqrt(2)
        assert cosine_similarity([1.0, 1.0], [1.0, 0.0]) == pytest.approx(1 / 2 ** 0.5)


class TestPrecisionFromRelevance:
    def test_all_relevant(self):
        # precision@1=1, precision@2=1 -> (1*1 + 1*1)/2 = 1
        assert precision_from_relevance([1, 1]) == pytest.approx(1.0)

    def test_relevant_second(self):
        # rel=[0,1]: precision@2=0.5, total=1 -> 0.5
        assert precision_from_relevance([0, 1]) == pytest.approx(0.5)

    def test_relevant_first(self):
        # rel=[1,0]: precision@1=1 -> 1/1 = 1
        assert precision_from_relevance([1, 0]) == pytest.approx(1.0)

    def test_empty(self):
        assert precision_from_relevance([]) == 0.0

    def test_no_relevant(self):
        assert precision_from_relevance([0, 0, 0]) == 0.0


class TestSupportedRatio:
    def test_all_supported(self):
        assert supported_ratio(["a", "b"], [1, 1]) == pytest.approx(1.0)

    def test_half(self):
        assert supported_ratio(["a", "b", "c"], [1, 0, 1]) == pytest.approx(2 / 3)

    def test_no_claims(self):
        assert supported_ratio([], []) == 0.0

    def test_short_supported_list(self):
        # судья вернул меньше ответов, чем утверждений: хвост считаем неподтверждённым
        assert supported_ratio(["a", "b", "c"], [1]) == pytest.approx(1 / 3)


class TestExtractJson:
    def test_clean(self):
        assert _extract_json('{"claims": ["a", "b"]}') == {"claims": ["a", "b"]}

    def test_with_markdown_fences(self):
        assert _extract_json('```json\n{"supported": [1, 0]}\n```') == {"supported": [1, 0]}

    def test_with_prefix_noise(self):
        assert _extract_json('Вот результат:\n{"questions": ["q1"]}') == {"questions": ["q1"]}

    def test_empty(self):
        assert _extract_json("") is None
        assert _extract_json("текст без json") is None

    def test_broken_json(self):
        assert _extract_json('{"a": [1, 2,}') is None
