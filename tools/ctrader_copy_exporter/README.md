# cTrader Copy History exporter

This is a **read-only, one-week dry-run implementation**. It never places or
changes orders and never calls cTrader APIs. Use only an account and history
you are authorized to access. Authentication remains manual in a local,
persistent headed Chromium profile; do not provide passwords, cookies, tokens,
or session material to the CLI.

The cTrader DOM has not been inspected here. Selectors are therefore
operator-supplied and must be validated in the real browser before an export.
The weekly schedule, half-open local-time clipping, date-filter confirmation,
internal widget scrolling, row ID deduplication, incremental SQLite/CSV
persistence, checkpoints, verification scan, and daily fallback are generic
framework behavior. The exact date-filter controls, trade row/ID/timestamp
selectors, loading indicator, scroll container, and refresh behavior require
real-browser validation.

## Install on Windows PowerShell

From the repository root:

```powershell
py -3.12 -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install -e ".[dev,ctrader-export]"
python -m playwright install chromium
```

Open an authorized cTrader Copy History page in a normal browser and use the
interactive diagnostic to identify visible controls and internal scroll
containers. It opens a local headed browser profile, lets you sign in
manually, and saves structural metadata only (not row contents, cookies, or
credentials):

```powershell
python -m tools.ctrader_copy_exporter diagnostic `
  --url "https://www.ctrader.com/" `
  --output .\data\ctrader-copy-dom-diagnostic.json
```

Inspect the diagnostic locally, confirm the site's UI timezone, then copy
[selectors.example.json](./selectors.example.json) to a private local file and
replace every placeholder with CSS selectors confirmed in the visible
authorized UI. Do not commit account data or diagnostic screenshots. The
example file intentionally does not claim actual cTrader selectors. If the
visible date controls require another text representation, set `date_format`
to the Python `strftime` format used by the UI. This adapter requires fillable
date inputs and an explicit apply button; other control types require a
validated adapter adjustment. ISO-8601 timestamps are preferred; if visible
timestamps use another format, configure `timestamp_format`. Naive timestamps
in ambiguous or nonexistent DST hours are rejected rather than guessed.

## First run: one local week, at most 20 unique IDs

The date arguments require ISO-8601 offsets; `--timezone` must be the IANA
timezone configured in the cTrader UI. The clipped range must fit within one
local Monday-to-Monday week. Use an interval known to contain no more than 20
trades so the exporter can verify the bottom without hitting its safety cap.

```powershell
$selectors = ".\data\ctrader-copy-selectors.json"
$start = "2026-10-12T00:00:00+01:00"
$end = "2026-10-19T00:00:00+01:00"

python -m tools.ctrader_copy_exporter dry-run `
  --url "https://www.ctrader.com/" `
  --timezone "Europe/London" `
  --start $start `
  --end $end `
  --selectors $selectors `
  --output .\data\ctrader_copy_export `
  --account-label "my-authorized-account"
```

The browser opens headed. Sign in yourself if required, navigate to the
authorized Copy History view, and press Enter in the terminal. The exporter
applies the existing History date filter before extracting rows. Date-only UI
filter ends deliberately overlap; stored timestamps are clipped to the exact
local half-open interval and IDs are globally deduplicated.

Each successful batch commits to SQLite, then atomically rebuilds `trades.csv`.
The SQLite database also stores run manifests, per-week/day checkpoints, and
batch checksums. Re-running the same request resumes its `dry-run-...` run ID;
pass the same explicit `--run-id` if you need a stable custom checkpoint. Dry
run data has a separate output namespace and cannot complete a full-export
checkpoint.

SQLite is the primary store. Common structured fields (symbol, direction,
quantity, open/close prices and times, commission, swap, net profit, and
account balance) are indexed as nullable text columns; all other visible row
fields remain in `raw_fields_json`. Existing SQLite files receive additive
columns and retain their trade rows and checkpoints. To keep only SQLite and
skip the CSV projection, pass `--no-csv`; CSV remains enabled by default for
backward compatibility.

The summary reports interval status, unique/duplicate IDs, refresh resets,
verification, and output paths. Reaching 20 before the bottom is verified
leaves the interval incomplete. A dry run is only validated if the bottom and
verification pass succeed with no unresolved warnings. This CLI intentionally
has no full-export mode; do not start a full export until the dry run has been
reviewed and explicitly approved.

## Tests

```powershell
pytest tests\test_ctrader_copy_exporter.py
ruff check tools\ctrader_copy_exporter tests\test_ctrader_copy_exporter.py
```
