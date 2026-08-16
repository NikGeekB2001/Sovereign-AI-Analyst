# -*- coding: utf-8 -*-
"""CLI-прогон автооценки RAG-качества (RAGAS-стиль).

Примеры:
  # полный конвейер в процессе (быстрее, без HTTP)
  python -m backend.evaluation.run_evaluation --dataset backend/evaluation/test_dataset.jsonl \
      --mode graph --limit 3 --role куратор

  # через запущенный API (истинный E2E)
  python -m backend.evaluation.run_evaluation --dataset backend/evaluation/test_dataset.jsonl \
      --mode api --limit 3 --api-url http://localhost:8000

Отчёт: evaluation/reports/report_<ts>.json + report_<ts>.html (для просмотра).
Документация метрик: docs/PERFORMANCE_METRICS.md
"""
from __future__ import annotations

import argparse
import json
import os
import sys
import time
from datetime import datetime
from typing import Dict, List

from dotenv import load_dotenv

load_dotenv()  # .env: NEO4J_PASSWORD, LLM_MODEL, EMBEDDING_MODEL и т.д.

from backend.evaluation.ragas_metrics import RagasEvaluator  # noqa: E402

REPORTS_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "reports")


def load_dataset(path: str) -> List[Dict]:
    """JSONL (question, ground_truth, role?) или JSON-массив."""
    with open(path, encoding="utf-8") as f:
        text = f.read().strip()
    if text.startswith("["):
        return json.loads(text)
    items = []
    for line in text.splitlines():
        line = line.strip()
        if line and not line.startswith("#"):
            items.append(json.loads(line))
    return items


def answer_via_graph(question: str, role: str) -> str:
    from backend.agents.multi_agent_graph import app as agent_app
    inputs = {"messages": [("user", question)], "user_role": role, "context": []}
    result = agent_app.ainvoke(inputs, config={"recursion_limit": 50})
    messages = result.get("messages", []) if result else []
    if not messages:
        return ""
    last = messages[-1]
    if isinstance(last, tuple):
        return last[1] if len(last) > 1 else ""
    return getattr(last, "content", "") or str(last)


def answer_via_api(question: str, role: str, api_url: str) -> str:
    import urllib.request
    body = json.dumps({"message": question, "user_role": role}).encode()
    req = urllib.request.Request(
        api_url + "/api/v1/chat", data=body, headers={"Content-Type": "application/json"}
    )
    with urllib.request.urlopen(req, timeout=900) as resp:
        data = json.loads(resp.read().decode())
    return data.get("answer") or data.get("response") or ""


def build_html(report: Dict, out_path: str) -> None:
    rows = "".join(
        f"<tr><td>{i + 1}</td><td>{it['question'][:80]}</td>"
        f"<td>{it.get('faithfulness', '—')}</td><td>{it.get('answer_relevancy', '—')}</td>"
        f"<td>{it.get('context_precision', '—')}</td><td>{it.get('context_recall', '—')}</td>"
        f"<td>{it.get('hallucination_rate', '—')}</td>"
        f"<td>{it.get('answer_s', '—')}с</td></tr>"
        for i, it in enumerate(report["items"])
    )
    agg = report.get("aggregate", {})
    agg_cells = "".join(
        f'<div class="metric"><div class="m-label">{k}</div><div class="m-val">{v}</div></div>'
        for k, v in agg.items()
    )
    html = f"""<!DOCTYPE html><html lang="ru"><head><meta charset="utf-8">
<title>Отчёт автооценки RAG ({report.get('generated_at', '')})</title>
<style>
 body {{ font-family: 'Segoe UI', Arial, sans-serif; margin: 0; background: #f6f7f9; color: #1f2937; }}
 .wrap {{ max-width: 1100px; margin: 0 auto; padding: 24px 20px; }}
 h1 {{ font-size: 22px; }} .muted {{ color: #6b7280; font-size: 13px; }}
 .metrics {{ display: flex; flex-wrap: wrap; gap: 12px; margin: 16px 0; }}
 .metric {{ background: #fff; border: 1px solid #e5e7eb; border-radius: 12px; padding: 12px 16px; min-width: 130px; }}
 .m-label {{ font-size: 12px; color: #6b7280; }} .m-val {{ font-size: 20px; font-weight: 700; }}
 table {{ width: 100%; border-collapse: collapse; background: #fff; font-size: 13px; }}
 th {{ background: #f1f5f9; text-align: left; padding: 8px; border-bottom: 2px solid #e2e8f0; }}
 td {{ padding: 8px; border-bottom: 1px solid #eef2f7; }}
</style></head><body><div class="wrap">
<h1>Отчёт автооценки RAG (RAGAS-стиль)</h1>
<div class="muted">mode={report.get('mode')} · role={report.get('role')} · top_k={report.get('top_k')} · llm={report.get('llm')} · {report.get('generated_at')}</div>
<div class="metrics">{agg_cells}</div>
<table><thead><tr><th>#</th><th>Вопрос</th><th>Faith</th><th>Rel</th><th>CP</th><th>CR</th><th>Halluc</th><th>Латентн.</th></tr></thead>
<tbody>{rows}</tbody></table>
</div></body></html>"""
    with open(out_path, "w", encoding="utf-8", newline="\n") as f:
        f.write(html)


def main() -> int:
    ap = argparse.ArgumentParser(description="Автооценка RAG-качества (RAGAS-стиль)")
    ap.add_argument("--dataset", required=True, help="JSONL/JSON с вопросами и эталонами")
    ap.add_argument("--mode", choices=["graph", "api"], default="graph")
    ap.add_argument("--api-url", default=os.getenv("API_URL", "http://localhost:8000"))
    ap.add_argument("--limit", type=int, default=0, help="0 = все вопросы")
    ap.add_argument("--role", default=os.getenv("EVAL_ROLE", "куратор"))
    ap.add_argument("--top-k", type=int, default=5)
    ap.add_argument("--model", default=None)
    args = ap.parse_args()

    items = load_dataset(args.dataset)
    if args.limit > 0:
        items = items[:args.limit]
    if not items:
        print("Датасет пуст")
        return 1

    print(f"[eval] вопросов: {len(items)}, mode={args.mode}, role={args.role}, top_k={args.top_k}")
    evaluator = RagasEvaluator(model=args.model, role=args.role, top_k=args.top_k)

    report: Dict = {
        "generated_at": datetime.now().isoformat(timespec="seconds"),
        "mode": args.mode,
        "role": args.role,
        "top_k": args.top_k,
        "llm": getattr(evaluator.llm, "model", "?"),
        "dataset": args.dataset,
        "items": [],
    }

    for i, it in enumerate(items, 1):
        q = it["question"]
        gt = it.get("ground_truth", "")
        role = it.get("role", args.role)
        print(f"\n[{i}/{len(items)}] {q[:80]}")
        t0 = time.time()
        contexts = evaluator.evaluate_retrieval(q)
        retr_ms = (time.time() - t0) * 1000
        t1 = time.time()
        if args.mode == "api":
            answer = answer_via_api(q, role, args.api_url)
        else:
            answer = answer_via_graph(q, role)
        ans_s = time.time() - t1
        if not answer.strip():
            print("  ! пустой ответ, пропуск метрик")
            continue
        m = evaluator.evaluate_answer(q, answer, contexts, gt or None)
        row = {
            "question": q,
            "answer_len": len(answer),
            "contexts": len(contexts),
            "retrieval_ms": round(retr_ms, 1),
            "answer_s": round(ans_s, 1),
            **m,
        }
        report["items"].append(row)
        print("  " + ", ".join(f"{k}={v}" for k, v in m.items())
              + f", latency={ans_s:.0f}s, ctx={len(contexts)}")

    # агрегаты
    keys = ["faithfulness", "answer_relevancy", "context_precision", "context_recall", "hallucination_rate"]
    agg: Dict[str, float] = {}
    n = len(report["items"]) or 1
    for k in keys:
        vals = [it.get(k, 0.0) for it in report["items"] if k in it]
        agg[k] = round(sum(vals) / (len(vals) or 1), 4) if vals else None
    agg["avg_answer_s"] = round(sum(it["answer_s"] for it in report["items"]) / n, 1)
    agg["avg_retrieval_ms"] = round(sum(it["retrieval_ms"] for it in report["items"]) / n, 1)
    report["aggregate"] = agg

    os.makedirs(REPORTS_DIR, exist_ok=True)
    ts = datetime.now().strftime("%Y%m%d_%H%M%S")
    jp = os.path.join(REPORTS_DIR, f"report_{ts}.json")
    hp = os.path.join(REPORTS_DIR, f"report_{ts}.html")
    with open(jp, "w", encoding="utf-8", newline="\n") as f:
        json.dump(report, f, ensure_ascii=False, indent=2)
    build_html(report, hp)

    print("\n=== Агрегат ===")
    for k, v in agg.items():
        print(f"  {k}: {v}")
    print(f"\nОтчёты: {jp}\n        {hp}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
