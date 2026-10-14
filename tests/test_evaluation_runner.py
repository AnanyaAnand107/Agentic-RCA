from evaluation.run_evaluation import evaluate


def test_evaluation_metrics_and_missing_data():
    result = evaluate([
        {"expected_commit": "abc", "actual_commit": "abc", "reproduction_result": "reproduced", "patch_passed": True, "baseline_mttr_minutes": 100, "observed_mttr_minutes": 75},
        {"expected_commit": "def", "actual_commit": "xyz", "reproduction_result": "not_reproduced", "patch_passed": False, "baseline_mttr_minutes": 60, "observed_mttr_minutes": 60},
    ])
    assert result["localization_accuracy"] == 0.5
    assert result["reproduction_reliability"] == 0.5
    assert result["patch_pass_rate"] == 0.5
    assert result["mean_mttr_improvement_pct"] == 12.5


def test_empty_evaluation_does_not_invent_scores():
    result = evaluate([])
    assert result["dataset_size"] == 0
    assert result["localization_accuracy"] is None
