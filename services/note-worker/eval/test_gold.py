import gold


def test_perfect_agreement_has_kappa_one():
    pairs = [("supported", "supported"), ("unsupported", "unsupported"), ("contradicted", "contradicted")]
    assert gold.cohen_kappa(pairs) == 1.0


def test_kappa_discounts_chance_agreement():
    # Both raters say "supported" 90% of the time; agreeing on 80% is barely better than chance.
    pairs = [("supported", "supported")] * 8 + [("supported", "unsupported"), ("unsupported", "supported")]
    assert gold.cohen_kappa(pairs) < 0
    assert gold.agreement(pairs)["raw_agreement"] == 0.8


def test_agreement_reports_how_many_human_problems_the_judge_flags():
    pairs = [("contradicted", "contradicted"), ("unsupported", "supported"), ("supported", "supported"),
             ("supported", "unsupported")]
    result = gold.agreement(pairs)
    assert result["judge_flags_human_problems"] == 0.5
    assert result["per_label"]["unsupported"] == {"human": 1, "judge": 1, "judge_precision": 0.0, "judge_recall": 0.0}
    assert result["confusion_human_rows_judge_columns"]["unsupported"]["supported"] == 1
