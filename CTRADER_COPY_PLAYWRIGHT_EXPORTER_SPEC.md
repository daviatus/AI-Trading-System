# cTrader Copy Playwright Exporter Specification

## Status and scope

This document specifies an authorized, read-only browser workflow for exporting
the signed-in user's cTrader Copy trade history. It is a specification only;
the exporter is not implemented by this document. Do not start a full
historical export until the one-week dry run described below has passed review.

The exporter must use the cTrader website's existing History view and date
filter. It must not place, edit, or cancel orders, change account settings,
access another user's data, call undocumented/private endpoints, or bypass
authentication, access controls, CAPTCHAs, rate limits, or other site
protections. Stop and report when access is denied or the UI cannot be
automated within these limits. Use only an account and history the operator is
authorized to access, and follow the website's terms and applicable law.

## Date and interval semantics

- The cTrader UI's configured timezone is authoritative for interval
  boundaries. Read and record the timezone from the UI/account configuration;
  do not silently assume the machine timezone or UTC.
- The default logical interval is one local calendar week:
  **Monday 00:00 inclusive through the following Monday 00:00 exclusive**.
  Generate intervals using local calendar arithmetic, then convert their
  boundaries to UTC for storage and comparisons. This preserves local week
  boundaries across daylight-saving transitions.
- Treat the user's overall requested range as half-open `[request_start,
  request_end)`. Clip the first and last logical weekly interval to those
  exact requested instants. Reject an empty or reversed range.
- Apply the interval to the website using its existing date filter before
  reading any trade rows. Confirm that the filter was accepted and that the
  visible history corresponds to the requested interval before extraction.
- Some UI date filters have inclusive date-only endpoints. When needed, use
  overlapping UI filter bounds at adjacent interval boundaries so a boundary
  trade is not omitted. After extraction, enforce the exact logical
  half-open UTC interval locally. A trade at a shared boundary may be returned
  by both UI queries; global trade-ID deduplication must retain it only once.
- Preserve the raw UI date filter values and UI timezone in the run metadata.
  If the filter cannot represent a clipped instant precisely, query the
  enclosing date range and apply the exact local timestamp filter. Never
  silently broaden the final exported range.

## Weekly scheduling and adaptive subdivision

1. Enumerate clipped weekly intervals in chronological order.
2. Process one interval at a time. A week is eligible for completion only after
   its extraction and verification pass succeed.
3. Split a week into local calendar-day intervals if either:
   - it exceeds the configurable weekly trade threshold (default: 5,000
     distinct visible trade rows); or
   - it experiences three or more automatic History refresh/scroll-reset
     events during that interval.
4. Daily subdivisions use local midnight-to-midnight boundaries, clipped to
   the parent week and overall requested range. Apply the same website date
   filter, exact local half-open filtering, persistence, deduplication, and
   completion verification to each day.
5. Keep the parent weekly checkpoint incomplete until all of its required
   daily children are completed and reconciled. A failure in a child leaves
   that day incomplete and the parent incomplete; resume from the child
   checkpoint.
6. Thresholds must be configurable, recorded in the run manifest, and applied
   deterministically. Do not silently discard or cap records in a normal
   export.

## History refresh and scroll-reset handling

The History widget may refresh asynchronously and return its scroll position
to the top. Detect this rather than interpreting the resulting repeated rows
as new data or as evidence that the bottom was reached.

- Observe the supported UI signals available to Playwright, including changes
  to the History content/loading state, scroll-container position, visible
  row identities, and the last visible row before and after scrolling.
- Treat a refresh as a reset when the history content is reloaded or replaced
  and the scroll position returns toward the top, or when the visible row
  window jumps backwards unexpectedly. Record each reset and the evidence.
- Persist every previously extracted batch before continuing. After a reset,
  wait for the widget to settle, confirm that the date filter is still active,
  and resume scanning from the top or another safely re-established position.
  Global trade-ID deduplication makes replay safe.
- If the filter has been cleared, the widget fails to settle, or row continuity
  cannot be re-established, stop that interval as incomplete. Do not infer
  completion from a timeout or an empty transient view.
- Repeated resets trigger the weekly-to-daily subdivision rule. If a daily
  child also repeatedly resets, stop it as incomplete and report the condition
  for operator review; do not subdivide into smaller units indefinitely.

## Extraction, identity, deduplication, and persistence

- Extract only rows rendered by the authorized History UI after the date filter
  has been applied. Use stable, UI-supported selectors; do not depend on
  undocumented application internals.
- Require a stable trade ID visible or otherwise explicitly exposed by the
  authorized UI. Use it as the global deduplication key across weekly and
  daily intervals, including overlapping inclusive UI boundaries and refresh
  replays. If a stable ID is unavailable, do not claim exactly-once export:
  mark the affected interval incomplete and report that identity could not be
  verified. Do not merge trades solely because their displayed fields happen
  to match.
- Validate parsed timestamps, required fields, and exact requested-range
  membership. Preserve original displayed values and timezone context as
  provenance where useful; normalize timestamps to UTC for canonical storage.
- Commit each successfully parsed extraction batch incrementally to durable
  output before scrolling further. Use idempotent upserts or append-plus-ID
  deduplication so retries and refresh replays cannot create duplicate trades.
- Keep output records and checkpoints durable independently of the browser
  session. A crash after a committed batch must not lose that batch or advance
  a checkpoint past data that was not committed.
- Record per-batch counts, newly inserted IDs, duplicate IDs, source interval,
  extraction time, and a content checksum. Avoid logging credentials, session
  cookies, access tokens, or other secrets.

## Persistent checkpoints and resume

Maintain durable checkpoints for every logical weekly interval and any daily
children. A checkpoint must include:

- run ID, account identifier in a non-secret form, requested overall range,
  UI timezone, logical interval bounds, and actual UI filter values;
- interval granularity (week or day), parent interval if applicable, attempt
  count, start/update timestamps, and latest known scroll/reset state;
- status: `pending`, `in_progress`, `completed`, or `incomplete`;
- committed row and unique-ID counts, duplicate count, last committed batch
  identifier/checksum, and verification outcome/reason.

On restart, resume `in_progress` and `incomplete` intervals by safely
reapplying the date filter and replaying extraction as necessary. Already
committed trades remain protected by global ID deduplication. Never advance a
checkpoint based only on browser scroll state; the batch data must be durable
first.

## Completion verification

An interval may be marked `completed` only when all of the following hold:

1. The intended date filter is still applied and the History widget is stable.
2. Scrolling has demonstrably reached the bottom of the filtered result set
   (for example, the scroll position is at the maximum and no further rows
   load after the normal UI settle period).
3. A verification pass re-scans the filtered visible rows from the top through
   the bottom, accounting for refreshes and overlapping windows, and confirms
   there are no missing visible trade IDs or rows not present in durable
   output.
4. The verification pass finds no new unique IDs; all extracted rows pass
   validation and range clipping; persisted counts and checksums agree.

If any condition cannot be established, status must be `incomplete` with a
specific reason (such as `bottom_not_reached`, `verification_found_missing`,
`history_refresh_unstable`, `filter_not_confirmed`, `missing_trade_id`, or
`persistence_error`). Report incomplete intervals and their counts/reasons in
the run summary. Do not turn a timeout, browser close, row cap, or transient
empty state into success.

## Mandatory first run: one-week dry run

Before any full historical export:

- Require an explicit overall date range that clips to exactly one logical
  local calendar week, and apply that week's date filter in the UI.
- Run in dry-run mode with a hard ceiling of **20 globally unique trades**.
  The limit must never be exceeded, including across refreshes or overlapping
  filter results.
- Use a week with no more than 20 trades to validate the complete workflow.
  The dry run passes only if the exporter reaches the bottom, the verification
  pass confirms all visible rows, all expected rows are durably saved, and the
  interval/report shows no unresolved gaps or identity conflicts.
- If the twentieth unique trade is reached before bottom verification, stop
  immediately, report `dry_run_limit_reached` and leave the interval
  incomplete. That bounded sample can validate parsing and persistence but
  does **not** authorize a full export; choose a less active one-week range or
  obtain operator review before another bounded test.
- Clearly label dry-run outputs and checkpoints. A dry run must not mark a
  production weekly checkpoint completed or silently merge sample output into
  a full-export dataset.
- Present the operator with the selected range/timezone, unique and duplicate
  counts, completion/verification outcome, refresh/reset count, saved output
  location, and any warnings. Require explicit operator validation before
  enabling a full historical run.

## Run summary and acceptance criteria

Each run must report requested and actual clipped ranges, UI timezone, weekly
and daily interval counts, completed/incomplete interval counts, committed
unique trades, duplicate IDs, rejected rows, refresh/reset events, verification
results, and output/checkpoint locations. Never report a full export as
complete while any interval or child interval is incomplete.

The exporter specification is satisfied when tests or an authorized
one-week dry run demonstrate:

- correct local Monday-to-Monday scheduling and clipped first/last intervals,
  including a daylight-saving transition;
- safe inclusive UI boundary overlap with exact local filtering and global ID
  deduplication;
- date-filter application before row extraction;
- refresh/scroll-reset detection and safe replay without duplicate output;
- automatic weekly-to-daily subdivision at configured thresholds;
- incremental durable batch writes and restart from persistent checkpoints;
- completion withheld when bottom reach or verification fails;
- a dry-run ceiling of 20 unique trades and no full export before operator
  validation.
