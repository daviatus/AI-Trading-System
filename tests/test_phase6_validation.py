from trading.research.phase6.validation import assess

CONFIG = {"profit_factor_target": 1.6, "drawdown_ceiling": 0.1, "minimum_full_year_trades": 100}


def test_zero_trades_are_valid_but_cannot_pass_acceptance():
    result = assess({"net_return": 0, "completed_trades": 0}, CONFIG)
    assert not result["accepted"]
    assert result["no_trades_is_valid"]
    assert "low_trade_count_uncertainty" in result["rejection_reasons"]


def test_strong_development_period_cannot_be_final_acceptance():
    metrics = {
        "profit_factor": 2,
        "net_return": 0.1,
        "maximum_mark_to_market_drawdown": 0.05,
        "completed_trades": 200,
        "by_year": {"2025": {"full_evaluation_year": True, "trades": 200}},
    }
    result = assess(metrics, CONFIG)
    assert result["rejection_reasons"] == [
        "development_only_requires_untouched_or_prospective_validation"
    ]


def test_full_year_activity_is_not_annualized_from_partial_year():
    metrics = {
        "profit_factor": 2,
        "net_return": 0.1,
        "maximum_mark_to_market_drawdown": 0.05,
        "completed_trades": 200,
        "by_year": {"2025": {"full_evaluation_year": False, "trades": 200}},
    }
    result = assess(metrics, CONFIG)
    assert "no_full_evaluation_year_activity_evidence" in result["rejection_reasons"]
