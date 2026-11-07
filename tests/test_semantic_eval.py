"""AI 语义质检评测测试。"""

from __future__ import annotations

from eval.semantic_eval import build_labeled_set, detect_pollution, evaluate


def test_detector_catches_all_fault_types():
    assert detect_pollution("锟斤拷锟斤拷", "问题") == "gibberish"
    assert detect_pollution("今天天气不错。", "问题") == "off_topic"
    assert detect_pollution("复读" * 3, "复读") == "echo"
    assert detect_pollution("这款商品支持 7 天无理由退货。", "这个可以退货吗？") is None


def test_build_labeled_set_deterministic_and_balanced():
    a = build_labeled_set(n_clean=100, polluted_ratio=0.05)
    b = build_labeled_set(n_clean=100, polluted_ratio=0.05)
    assert a == b  # 固定 seed 完全一致
    polluted = [r for r in a if r["label"] == "polluted"]
    assert len(polluted) == 5
    types = {r["fault_type"] for r in polluted}
    assert {"gibberish", "echo", "off_topic"}.issubset(types)
    clean = [r for r in a if r["label"] == "clean"]
    assert all(r["fault_type"] is None for r in clean)


def test_evaluate_perfect_detector_scores_full():
    dataset = build_labeled_set(n_clean=100)
    metrics = evaluate(dataset)
    # 规则基线对共享词表必然全对：Precision=Recall=F1=1.0
    assert metrics["precision"] == 1.0
    assert metrics["recall"] == 1.0
    assert metrics["f1"] == 1.0
    assert metrics["fp"] == 0 and metrics["fn"] == 0
    for v in metrics["per_type"].values():
        assert v["recall"] == 1.0


def test_evaluate_counts_confusion_matrix():
    dataset = build_labeled_set(n_clean=10, polluted_ratio=0.1)
    metrics = evaluate(dataset)
    polluted = [r for r in dataset if r["label"] == "polluted"]
    assert metrics["polluted_total"] == len(polluted)
    assert metrics["tp"] + metrics["fn"] == len(polluted)
    assert metrics["tp"] + metrics["fp"] + metrics["fn"] + metrics["tn"] == len(dataset)
