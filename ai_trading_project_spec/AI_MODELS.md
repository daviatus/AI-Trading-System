# AI and Research Policy

## Baselines first
Implement deterministic benchmark strategies and cost-aware baselines before ML. An ML model is optional and must beat a fair out-of-sample benchmark net of costs to qualify for paper deployment.

## Candidate models
XGBoost and LightGBM for classification/regression; calibrated probabilities and explicit no-trade region. Feature sets may include returns, volatility, spread, trend, volume where meaningful, session/calendar and timestamped news. Never use revised future information.

## Validation
Chronological splits, purging/embargo where labels overlap, walk-forward retraining, untouched final holdout, realistic spreads/fees/slippage, and robustness across regimes. Avoid tuning on the final holdout. Store dataset hash, code revision, features, hyperparameters, metrics and artifacts.

## Approval
New candidates enter `CANDIDATE`, then `BACKTESTED`, `PAPER_APPROVED` only after explicit user approval. Live deployment requires a separate future design and manual approval. No model may alter risk policy.

## Metrics
Net PnL, max drawdown, profit factor, expectancy, turnover, costs, Sharpe with appropriate assumptions, trade count, calibration and regime sensitivity. No guaranteed profitability.
