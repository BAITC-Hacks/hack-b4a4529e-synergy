"""Opt-in real embedding retrieval checks. No chat-model grading or synthetic products.

python -m scripts.retrieval_eval
python -m scripts.retrieval_eval --thresholds 0.30 0.45 0.50 0.55
"""
import argparse
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path

from app.config import ROOT
from app.search import CatalogIndex, MIN_SCORE, search_products

CASES_PATH = Path(__file__).with_name("retrieval_cases.json")


def load_cases():
    return json.loads(CASES_PATH.read_text(encoding="utf-8"))["cases"]


def score_case(case: dict, results: list[dict]) -> dict:
    ids = [p["id"] for p in results[:5]]
    expected = set(case["expected_ids"])
    rank = next((i for i, pid in enumerate(ids, 1) if pid in expected), None)
    passed = rank is not None if expected else not results
    return {"id": case["id"], "split": case["split"], "query": case["query"],
            "expected_ids": case["expected_ids"], "product_ids": ids,
            "scores": [p.get("score") for p in results[:5]],
            "passed": passed, "reciprocal_rank": 1 / rank if rank else 0}


def summarize(rows: list[dict]) -> dict:
    positives = [r for r in rows if r["expected_ids"]]
    negatives = [r for r in rows if not r["expected_ids"]]
    return {"passed": sum(r["passed"] for r in rows), "total": len(rows),
            "positive_count": len(positives), "negative_count": len(negatives),
            "hit_at_5": sum(r["passed"] for r in positives) / len(positives) if positives else None,
            "mrr_at_5": sum(r["reciprocal_rank"] for r in positives) / len(positives) if positives else None,
            "no_match_accuracy": sum(r["passed"] for r in negatives) / len(negatives) if negatives else None}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--thresholds", nargs="+", type=float, default=[MIN_SCORE])
    parser.add_argument("--index-dir", type=Path)
    parser.add_argument("--output", type=Path, default=ROOT / "data/acceptance/retrieval.json")
    args = parser.parse_args()
    index = CatalogIndex.load(args.index_dir)
    cases = load_cases()
    missing = {pid for case in cases for pid in case["expected_ids"] if index.get(pid) is None}
    if missing:
        raise ValueError(f"Retrieval labels need review for this catalog; missing IDs: {sorted(missing)}")
    runs = []
    for threshold in args.thresholds:
        rows = []
        for case in cases:
            result = search_products(case["query"], limit=5, index=index, min_score=threshold)
            row = score_case(case, result["results"])
            rows.append(row)
            print(json.dumps({"threshold": threshold, **row}, ensure_ascii=False), flush=True)
        summary = {split: summarize([r for r in rows if r["split"] == split])
                   for split in ("calibration", "validation")}
        runs.append({"threshold": threshold, "summary": summary, "cases": rows})
        print(json.dumps({"threshold": threshold, "summary": summary}), flush=True)
    report = {"at": datetime.now(timezone.utc).isoformat(), "snapshot": index.metadata,
              "labels_sha256": hashlib.sha256(CASES_PATH.read_bytes()).hexdigest(), "runs": runs}
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    # A sweep is diagnostic; the default single-threshold run is a regression gate.
    if len(runs) == 1 and not all(row["passed"] for row in runs[0]["cases"]):
        raise SystemExit(1)


if __name__ == "__main__":
    main()
