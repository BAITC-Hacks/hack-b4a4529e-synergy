from scripts.retrieval_eval import load_cases, score_case, summarize


def test_irrelevant_nonempty_results_fail_positive_and_negative_checks():
    case = {"id": "positive", "split": "calibration", "query": "автомат", "expected_ids": [1]}
    assert not score_case(case, [{"id": 99}])["passed"]
    assert not score_case(case, [])["passed"]
    row = score_case(case, [{"id": 99}, {"id": 1}])
    assert row["passed"] and row["reciprocal_rank"] == 0.5
    negative = {**case, "id": "negative", "expected_ids": []}
    assert not score_case(negative, [{"id": 99}])["passed"]
    empty = score_case(negative, [])
    assert empty["passed"]
    summary = summarize([row, empty])
    assert summary["hit_at_5"] == 1 and summary["no_match_accuracy"] == 1
    assert summary["mrr_at_5"] == 0.5


def test_labels_cover_both_positive_and_negative_queries_in_each_split():
    cases = load_cases()
    assert len({case["id"] for case in cases}) == len(cases)
    for split in ("calibration", "validation"):
        rows = [c for c in cases if c["split"] == split]
        assert any(c["expected_ids"] for c in rows)
        assert any(not c["expected_ids"] for c in rows)
