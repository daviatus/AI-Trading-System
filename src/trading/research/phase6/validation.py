"""Explicit research acceptance assessment; development can never authorize trading."""


def assess(metrics, config, *, development=True):
    reasons = []
    if (
        metrics.get("profit_factor") is None
        or metrics["profit_factor"] <= config["profit_factor_target"]
    ):
        reasons.append("profit_factor_not_above_target")
    if metrics.get("net_return", 0) <= 0:
        reasons.append("nonpositive_net_return")
    if metrics.get("maximum_mark_to_market_drawdown", 1) >= config["drawdown_ceiling"]:
        reasons.append("drawdown_ceiling_exceeded")
    years = metrics.get("by_year", {}).values()
    full = [year for year in years if year.get("full_evaluation_year")]
    if not full:
        reasons.append("no_full_evaluation_year_activity_evidence")
    elif any(year["trades"] < config["minimum_full_year_trades"] for year in full):
        reasons.append("insufficient_full_year_activity")
    if metrics.get("completed_trades", 0) < config["minimum_full_year_trades"]:
        reasons.append("low_trade_count_uncertainty")
    if metrics.get("unresolved_terminal_positions", 0):
        reasons.append("unresolved_terminal_position")
    if development:
        reasons.append("development_only_requires_untouched_or_prospective_validation")
    return {
        "accepted": not reasons,
        "rejection_reasons": reasons,
        "frequency_target_is_portfolio_wide": True,
        "no_trades_is_valid": True,
        "profit_factor_alone_is_insufficient": True,
    }
