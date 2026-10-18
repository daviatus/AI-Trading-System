# Risk Management

## Default modes
`SIMULATION` is default. `CTRADER_DEMO` requires deliberate configuration and authenticated demo account verification. `LIVE` is intentionally unimplemented in v1 and must never be activated by changing a single boolean.

## Controls
- Per-trade risk budget configurable; example starting point 0.25–0.5% of equity, not a recommendation of returns.
- Daily realized + unrealized loss ceiling; total exposure ceiling; max concurrent positions; max symbol/correlated exposure.
- Broker-reported minimum volume, step size, tick value and currency conversion required before order sizing.
- If minimum volume exceeds allowed stop-risk, skip the trade.
- Require protective stop for all positions where supported; verify broker acceptance and handle partial fills.
- Reject trades on stale prices, unavailable contract specs, excessive spread, uncertain account mode, low margin headroom, market closed or unresolved broker state.
- Kill switch blocks new orders, cancels eligible pending orders and handles open positions according to an explicit tested policy.
- No martingale, averaging down or automatic leverage escalation.
- No auto-deployment of retrained models.

## Safety tests
Test duplicated signals, reconnect after timeout, partial fills, rejected stops, stale data, unexpected spread spikes, symbol mapping errors, clock skew, weekend sessions and insufficient margin.
