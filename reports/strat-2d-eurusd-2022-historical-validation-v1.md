# STRAT-2D EURUSD January-February 2022 historical validation v1

Engineering validation: **PASS**. Statistical performance remains unproven.

## Resource configuration

Explicit opt-in config: `config/strat2d-eurusd-2022-offline-130-validation-v1.json`. Profile `strat2d-eurusd-2022-offline-130-v1`: minimum physical available RAM 1.30 GiB, maximum worker peak RSS 256 MiB, minimum free disk 15 GiB, single-threaded offline execution, 1024-row batches. Ordinary workflows retain 1.5 GiB. The profile rejects other sources, dates, models, thread counts and limits. Child guards run between bounded operations; the parent polls physical RAM, disk and worker RSS every 0.1 seconds and kills only its validation child on a guard failure. This is sampled monitoring, not an OS-enforced memory reservation. All published transactions and failed staging are preserved.

## Data coverage and exclusions

Only the sealed 58,683-row EURUSD M1 preparation for 2022-01-01 UTC through 2022-03-01 UTC exclusive is read. Training ends January 15, calibration ends February 1, and test ends March 1. Calibration/test have a 60-minute embargo; future-label paths and split-end labels are purged. No source candles are filled or altered. Finite predictors and observed target-contiguous paths are required.

Historical calendar uncertainty remains explicit: America/New_York current-contract weekly hours are unverified for 2022; holiday evidence is absent. Calendar-based gap categories are hypotheses. Unexpected missing/out-of-session observations exclude affected label paths. An open position encountering unobservable data is censored, with exposure retained and later execution blocked. No invented gap fill is used.

## Training, simulation, recovery and replay

Model/scientific parameters are unchanged: online_softmax_v1, fixed scales, seed, learning rate and regularization; calibration freezes before test. Portfolio retains next-bar execution, costs and sizing. Ledger scans check sequential events/fills/trades, cash + unrealized = equity, cost totals and trade-history hashes. Historical zero fills do not validate real-data trade execution; forced-signal synthetic tests cover that path.

The driver kills itself with os._exit(86) before training generation 65 publication, resumes and crashes after test generation 220 publication, then resumes to completion. A repeated completed resume must change no committed bytes. An independent uninterrupted run compares every manifest/state/event/receipt SHA-256 and final canonical state. Parent logs, memory metadata and orphan staging are intentionally outside committed comparisons.

## PASS/FAIL checks

| Requirement | Status |
| --- | --- |
| resource_admission | PASS |
| source_integrity | PASS |
| hard_crash_resume_and_immutable_prefix | PASS |
| repeated_resume | PASS |
| independent_replay_all_committed_bytes | PASS |
| model_portfolio_ledger_invariants | PASS |
| strat2c_preservation | PASS |

## Measurements and complete evidence

```json
{
  "status": "PASS",
  "checks": {
    "resource_admission": "PASS",
    "source_integrity": "PASS",
    "hard_crash_resume_and_immutable_prefix": "PASS",
    "repeated_resume": "PASS",
    "independent_replay_all_committed_bytes": "PASS",
    "model_portfolio_ledger_invariants": "PASS",
    "strat2c_preservation": "PASS"
  },
  "config": {
    "schema_version": 1,
    "version": "strat2d-eurusd-2022-offline-130-validation-v1",
    "mode": "offline_simulation",
    "threads": 1,
    "asset": "EURUSD",
    "prepared": "data/research/phase6-strat2c-eurusd-2022-jan-feb-validation-v1/prepared-streams/1d06fff895ee69a12b38/EURUSD",
    "output": "data/research/strat2d-eurusd-2022-offline-130-resumed-v1",
    "start": "2022-01-01T00:00:00Z",
    "train_end": "2022-01-15T00:00:00Z",
    "calibration_end": "2022-02-01T00:00:00Z",
    "test_end": "2022-03-01T00:00:00Z",
    "embargo_minutes": 60,
    "batch_rows": 1024,
    "max_quality_intervals": 4096,
    "minimum_available_ram_gib": 1.3,
    "free_floor_gib": 15,
    "max_worker_ram_gib": 0.25,
    "timeout_seconds": 1800,
    "quality_policy": "observed_contiguous_engineering",
    "calendar": {
      "version": "eurusd-current-contract-hypothesis-v1",
      "timezone": "America/New_York",
      "weekly_open_seconds": [
        [
          61260,
          147540
        ],
        [
          147660,
          233940
        ],
        [
          234060,
          320340
        ],
        [
          320460,
          406740
        ],
        [
          406860,
          492900
        ]
      ],
      "holidays": [],
      "verified": false,
      "evidence_fingerprint": "7c66edc0ac9ff95ad02c57e7fe3862ec9f482b2b716bdbf6b7524c7f605a562e",
      "uncertainties": [
        "Weekly rules are current exposed metadata, not verified for 2022.",
        "No verified 2022 holiday schedule supplied; later dated holidays are not applied retroactively.",
        "Observed continuous data supports engineering checks only, not historical session claims."
      ]
    },
    "model": {
      "kind": "online_softmax_v1",
      "version": "strat2d-online-softmax-v1",
      "seed": 20261027,
      "features": [
        "return_1",
        "return_5",
        "volatility_20",
        "atr_fraction_14",
        "ema_trend_spread",
        "rsi_14",
        "body_fraction",
        "range_fraction"
      ],
      "scales": [
        0.001,
        0.001,
        0.001,
        0.001,
        0.001,
        100.0,
        0.001,
        0.001
      ],
      "learning_rate": 0.005,
      "l2": 0.001
    },
    "portfolio": {
      "version": "strat2d-cfd-reference-v1",
      "initial_equity": 10000.0,
      "risk_fraction": 0.0025,
      "leverage": 1.0,
      "max_positions": 1,
      "minimum_quantity": 1000.0,
      "quantity_step": 1000.0,
      "maximum_quantity": 10000000.0,
      "spread_bps": 1.0,
      "slippage_bps": 0.25,
      "commission_bps_roundtrip": 0.6,
      "stop_multiple": 2.0,
      "reward_risk": 3.0,
      "horizon_minutes": 15,
      "threshold": 0.55,
      "cost_evidence": "Explicit unverified reference-price scenario; no financing/FX conversion inferred."
    },
    "resource_profile": "strat2d-eurusd-2022-offline-130-v1"
  },
  "primary": "C:\\Users\\jager\\ai_trader\\AI-Trading-STRAT-2C\\data\\research\\strat2d-eurusd-2022-offline-130-resumed-v1",
  "reference": "data/research/strat2d-eurusd-2022-offline-130-reference-v1",
  "initial_resources": {
    "config": {
      "schema_version": 1,
      "version": "strat2d-eurusd-2022-offline-130-validation-v1",
      "mode": "offline_simulation",
      "threads": 1,
      "asset": "EURUSD",
      "prepared": "data/research/phase6-strat2c-eurusd-2022-jan-feb-validation-v1/prepared-streams/1d06fff895ee69a12b38/EURUSD",
      "output": "data/research/strat2d-eurusd-2022-offline-130-resumed-v1",
      "start": "2022-01-01T00:00:00Z",
      "train_end": "2022-01-15T00:00:00Z",
      "calibration_end": "2022-02-01T00:00:00Z",
      "test_end": "2022-03-01T00:00:00Z",
      "embargo_minutes": 60,
      "batch_rows": 1024,
      "max_quality_intervals": 4096,
      "minimum_available_ram_gib": 1.3,
      "free_floor_gib": 15,
      "max_worker_ram_gib": 0.25,
      "timeout_seconds": 1800,
      "quality_policy": "observed_contiguous_engineering",
      "calendar": {
        "version": "eurusd-current-contract-hypothesis-v1",
        "timezone": "America/New_York",
        "weekly_open_seconds": [
          [
            61260,
            147540
          ],
          [
            147660,
            233940
          ],
          [
            234060,
            320340
          ],
          [
            320460,
            406740
          ],
          [
            406860,
            492900
          ]
        ],
        "holidays": [],
        "verified": false,
        "evidence_fingerprint": "7c66edc0ac9ff95ad02c57e7fe3862ec9f482b2b716bdbf6b7524c7f605a562e",
        "uncertainties": [
          "Weekly rules are current exposed metadata, not verified for 2022.",
          "No verified 2022 holiday schedule supplied; later dated holidays are not applied retroactively.",
          "Observed continuous data supports engineering checks only, not historical session claims."
        ]
      },
      "model": {
        "kind": "online_softmax_v1",
        "version": "strat2d-online-softmax-v1",
        "seed": 20261027,
        "features": [
          "return_1",
          "return_5",
          "volatility_20",
          "atr_fraction_14",
          "ema_trend_spread",
          "rsi_14",
          "body_fraction",
          "range_fraction"
        ],
        "scales": [
          0.001,
          0.001,
          0.001,
          0.001,
          0.001,
          100.0,
          0.001,
          0.001
        ],
        "learning_rate": 0.005,
        "l2": 0.001
      },
      "portfolio": {
        "version": "strat2d-cfd-reference-v1",
        "initial_equity": 10000.0,
        "risk_fraction": 0.0025,
        "leverage": 1.0,
        "max_positions": 1,
        "minimum_quantity": 1000.0,
        "quantity_step": 1000.0,
        "maximum_quantity": 10000000.0,
        "spread_bps": 1.0,
        "slippage_bps": 0.25,
        "commission_bps_roundtrip": 0.6,
        "stop_multiple": 2.0,
        "reward_risk": 3.0,
        "horizon_minutes": 15,
        "threshold": 0.55,
        "cost_evidence": "Explicit unverified reference-price scenario; no financing/FX conversion inferred."
      },
      "resource_profile": "strat2d-eurusd-2022-offline-130-v1"
    },
    "started": 387704.984,
    "peak_rss_bytes": 24702976,
    "minimum_available_ram_gib": 1.5350189208984375,
    "minimum_free_disk_gib": 29.081623077392578,
    "calls": 10
  },
  "strat2c_preserved_before": 47,
  "source": {
    "files": {
      "stream-manifest.json": "e5bfcc830fc436a8a4c5fa45495c9b15bf180d97fe174c87ac9df0ead9c1f5a5",
      "stream-checkpoint.json": "75527435eff283f4551b2e3c7b6bd6986b7e283e4cefbb1896d1b5b9bcfccfa3",
      "chunks/00000000/bars.parquet": "52d0528ee37077f85546cb71db6d31c2f761b442880f20daa019bc4d81504ca5",
      "chunks/00000000/features.parquet": "28e832e22fbb3ede10ab9ad314d5c467269b6631b645da5a2a3c4df48ffab90a",
      "chunks/00000000/labels.parquet": "137a5ce819898215a419218cc8b9b2f92bb39c114ed9221e360ef07fe7e7d22a",
      "chunks/00000000/state.json": "f4bd8c878974634fc1b6734332f6a19664307e6ca0219bd7008a60740f22bfa5",
      "chunks/00000000/receipt.json": "bf3cc14ab18b67d3af3cd755ad430e950caa48eff0c25fbf8cf53c9628f4d4a4",
      "chunks/00000001/bars.parquet": "d8d4e312a61a0ac98f0712fccab35b6fe8e488b4b0cb7e9923442a515abafcb9",
      "chunks/00000001/features.parquet": "96a567eaba0c613b8289817bf2d486f30e8fdc0264dc30a4ed186c30fcfe5b65",
      "chunks/00000001/labels.parquet": "63336ba7d864641469d7028b7b3cea0e9c8c57f643094a66afc8a6037e5c625f",
      "chunks/00000001/state.json": "08a7e8702ee859f19ad6a1ff588a2bbb0e9ed26fbe95cfb957068a7c78138f6d",
      "chunks/00000001/receipt.json": "296bb28e4cccb3a664d36afdd8f329293447ec92e55c9f610d331a9737b79e7e",
      "chunks/00000002/bars.parquet": "862426e779f45165a707daeeccf51abcf4e1ad5861ea45df98c5b2cb134f8b62",
      "chunks/00000002/features.parquet": "aed2c6cf55a4c122be56217ab0a13126ca512aeb52a1a5882bc4bb9742bb8102",
      "chunks/00000002/labels.parquet": "f5dbb22137deaab1563838eea98a39cbb5fe96e2fdeecaf8afea5465424d6b0e",
      "chunks/00000002/state.json": "3e109d52c976a51e9c8de8bd11bef453b61706ff568113275ab8050bc79f8beb",
      "chunks/00000002/receipt.json": "1ee22ce713837bf7fc20dcc8cbac3ff1fca45e2b11d05532eca73ecd8ce16660",
      "chunks/00000003/bars.parquet": "97a95080c5d515f29cf622ace7c7e5ddeb6c1235b22d0716a6e614e79105a4cc",
      "chunks/00000003/features.parquet": "4d2725c9ac88174e9d94e4dc270fd4798b5e48a680bcf3c6d8500d2205ea9543",
      "chunks/00000003/labels.parquet": "fbeb3cbdef814a7c3fd2a7716de8d84c00aaa67f3e255e9843fe703d1545b8d6",
      "chunks/00000003/state.json": "46ac88dcfba60cff52eb7fc8fb5739d1b01ede2d33c05d216969989c1d2e28cc",
      "chunks/00000003/receipt.json": "c0882816da6dce749efd8e4fdb97b326c7b390e5a60c9bd4cc5f92b59411c261",
      "chunks/00000004/bars.parquet": "26902f9188bc62be925384f6dee0d44c1d6385563c3d67007e6790b3e2c4897d",
      "chunks/00000004/features.parquet": "d63745de4718bfaa04b38011406db957be4c70cbbdf230ad1264e33c9864b43f",
      "chunks/00000004/labels.parquet": "895465d8a8bf7346e1f85ee3711fc4b17a3eee223c6aa8708dfb396d19e58150",
      "chunks/00000004/state.json": "e500de28b95db5ebcce12591748114276d97af4236cf3f60b6aa7b106d8bea17",
      "chunks/00000004/receipt.json": "a74ffd6e4818ac033a88eaf5b1f83c0a4409bae213a2a4fb96390e3289e3a172",
      "chunks/00000005/bars.parquet": "ee13512b0802bf4525a2d24beedc707d11415ac1b92473c60f9d7a1d3c391512",
      "chunks/00000005/features.parquet": "21b6c2a1713699e6aa1398c35e472fcad471bff85ee7943df3f7bf5805991f4d",
      "chunks/00000005/labels.parquet": "971cf689a6c28e4a37cdec74d8888eac6ba6ce02e6d7eb996ebbbdad8185e8bd",
      "chunks/00000005/state.json": "a170bab8e2882f4a28d42c0f76eb1ca5620142d52357bdc9ad2522e56d9a25f6",
      "chunks/00000005/receipt.json": "179c11d49dc2e5cc52f56022676bfb1fa97bf9e390d0f0c2691464f8196e2afb",
      "chunks/00000006/bars.parquet": "8568be56ecf92a81a0888f1560a0715d51d9349a84d7dda98b65511f4dcdfa57",
      "chunks/00000006/features.parquet": "e84cbecc2edd5208412e19d00b6d6155854fbfc886802fafa0362ba2e23c4660",
      "chunks/00000006/labels.parquet": "c90a20ff8d67cde9666de30b2a6c168e06820f3546dd1f3a4f4ffa2b9678dcf1",
      "chunks/00000006/state.json": "5dee7876900bbda2d334b662288947d19cf8ecd8bbbf9052a12711575e179d77",
      "chunks/00000006/receipt.json": "b859f696b9cbb62c37d1915d542d0a7a0e2cf505d7b0a04c4d622584f975d3fd",
      "chunks/00000007/bars.parquet": "b433ef430ecde919d107179a38234142cfa1abc8c306b06108c355c1aff40760",
      "chunks/00000007/features.parquet": "76934d26cce472357ec482691eb1b8b2d7f132cd847b8fcb2065baa4ef30d656",
      "chunks/00000007/labels.parquet": "bf681bc4f8d165b513b6031fd3cd59fa6f028e8d35860ea42638f078e48f6cf5",
      "chunks/00000007/state.json": "ea3b22f858923a85d233d25b96c4320425ea440e7d2ed6fc92ceaa44f38a4277",
      "chunks/00000007/receipt.json": "162920967f1ceef050e4fb6bf462ef458f06c912ee7f92aa315ebab79623ecdb",
      "chunks/00000008/bars.parquet": "932801e02886dfcfb8073b171962635df10bc532dcfe7e0789ad805156d04316",
      "chunks/00000008/features.parquet": "a9a35b36b0adca9fae36087307a951daa38e209d1b3a974a4e68ffed7a62f274",
      "chunks/00000008/labels.parquet": "05974766b1b947b9aba1c80b191a6755dafea5a0a7712bb64f33427beb269a8d",
      "chunks/00000008/state.json": "12274cb665e5a30a65b364cc3e0f695aed3472aa692e9d35c27d421ad0aa772a",
      "chunks/00000008/receipt.json": "a2691640dba62d60bf4bed0f97de08b010467d05e4f93819769eb9c126d131c8"
    },
    "generation": 8,
    "rows": 58683,
    "identity": "11e14091cfbb185a1834ec719c96b54ee09eaf4dece9842cded08f6be700c586",
    "start": "2022-01-01T00:00:00+00:00",
    "end": "2022-03-01T00:00:00+00:00"
  },
  "training_crash": {
    "committed_generation": 64,
    "phase": "train"
  },
  "simulation_crash": {
    "committed_generation": 220,
    "phase": "test"
  },
  "committed_generation": 239,
  "state": {
    "chunk": 0,
    "complete": true,
    "counts": {
      "calibration": 58683,
      "quality": 58683,
      "test": 58683,
      "train": 58683
    },
    "event_seq": 54769,
    "frozen_model_hash": "cdc8c126e9dcd41fd825b269f73cbb36351449b1f1117fb85a944a6c5362cdcd",
    "metrics": {
      "brier_sum": 16094.13824830182,
      "log_loss_sum": 26728.29770431671,
      "test_labeled_rows": 25446
    },
    "model": {
      "calibration_class_counts": [
        7577,
        2981,
        3357
      ],
      "calibration_rows": 13915,
      "class_counts": [
        6557,
        2832,
        2823
      ],
      "last_calibration_decision": "2022-01-31T21:02:00+00:00",
      "last_train_decision": "2022-01-14T21:40:00+00:00",
      "log_temperature": 0.5595455223034815,
      "maximum_calibration_label_end": "2022-01-31T21:17:00+00:00",
      "maximum_training_label_end": "2022-01-14T21:55:00+00:00",
      "seed": 20261027,
      "train_rows": 12212,
      "weights": [
        [
          0.6564103363467445,
          -0.3748601590455712,
          -0.2815501773011794
        ],
        [
          0.024554496343037024,
          -0.04962279321370057,
          0.025068296870663812
        ],
        [
          -0.01609083008458457,
          -0.06946697560799804,
          0.08555780569258278
        ],
        [
          -0.20642685540781008,
          0.1379293733744851,
          0.06849748203332474
        ],
        [
          -0.3044781314057477,
          0.2043734821278589,
          0.10010464927788809
        ],
        [
          -0.0722999345503311,
          -0.008286163471658945,
          0.08058609802199045
        ],
        [
          0.28632242631611154,
          -0.26772384163362994,
          -0.01859858468248273
        ],
        [
          0.022552129124276055,
          -0.04691313629729039,
          0.024361007173014267
        ],
        [
          -0.28681383526216375,
          0.19753342050627104,
          0.08928041475589289
        ]
      ]
    },
    "offset": 0,
    "phase": "done",
    "portfolio": {
      "blocked_reason": null,
      "cash": 10000.0,
      "censored": false,
      "equity": 10000.0,
      "fills": 0,
      "last_close": 1.12142,
      "last_fill_id": null,
      "last_open": "2022-02-28T23:59:00+00:00",
      "last_trade_id": null,
      "maximum_drawdown": 0.0,
      "peak_equity": 10000.0,
      "pending": null,
      "position": null,
      "realized_net": 0.0,
      "rejections": {},
      "reserved_margin": 0.0,
      "trade_history_hash": null,
      "trades": 0,
      "transaction_costs": 0.0,
      "unrealized": 0.0
    },
    "quality": {
      "blocks": [
        {
          "end": "2022-01-02T22:01:00+00:00",
          "reason": "missing_weekend",
          "start": "2022-01-01T00:00:00+00:00"
        },
        {
          "end": "2022-01-02T22:06:00+00:00",
          "reason": "missing_open",
          "start": "2022-01-02T22:02:00+00:00"
        },
        {
          "end": "2022-01-02T22:17:00+00:00",
          "reason": "missing_open",
          "start": "2022-01-02T22:16:00+00:00"
        },
        {
          "end": "2022-01-02T22:21:00+00:00",
          "reason": "missing_open",
          "start": "2022-01-02T22:20:00+00:00"
        },
        {
          "end": "2022-01-02T22:40:00+00:00",
          "reason": "missing_open",
          "start": "2022-01-02T22:39:00+00:00"
        },
        {
          "end": "2022-01-02T22:43:00+00:00",
          "reason": "missing_open",
          "start": "2022-01-02T22:42:00+00:00"
        },
        {
          "end": "2022-01-02T22:58:00+00:00",
          "reason": "missing_open",
          "start": "2022-01-02T22:57:00+00:00"
        },
        {
          "end": "2022-01-03T04:42:00+00:00",
          "reason": "missing_open",
          "start": "2022-01-03T04:41:00+00:00"
        },
        {
          "end": "2022-01-03T21:22:00+00:00",
          "reason": "missing_open",
          "start": "2022-01-03T21:20:00+00:00"
        },
        {
          "end": "2022-01-03T21:27:00+00:00",
          "reason": "missing_open",
          "start": "2022-01-03T21:23:00+00:00"
        },
        {
          "end": "2022-01-03T21:36:00+00:00",
          "reason": "missing_open",
          "start": "2022-01-03T21:34:00+00:00"
        },
        {
          "end": "2022-01-03T22:01:00+00:00",
          "reason": "missing_session_break",
          "start": "2022-01-03T21:59:00+00:00"
        },
        {
          "end": "2022-01-03T22:04:00+00:00",
          "reason": "missing_open",
          "start": "2022-01-03T22:03:00+00:00"
        },
        {
          "end": "2022-01-04T00:27:00+00:00",
          "reason": "missing_open",
          "start": "2022-01-04T00:26:00+00:00"
        },
        {
          "end": "2022-01-04T21:22:00+00:00",
          "reason": "missing_open",
          "start": "2022-01-04T21:21:00+00:00"
        },
        {
          "end": "2022-01-04T21:27:00+00:00",
          "reason": "missing_open",
          "start": "2022-01-04T21:26:00+00:00"
        },
        {
          "end": "2022-01-04T21:55:00+00:00",
          "reason": "missing_open",
          "start": "2022-01-04T21:53:00+00:00"
        },
        {
          "end": "2022-01-04T22:01:00+00:00",
          "reason": "missing_session_break",
          "start": "2022-01-04T21:59:00+00:00"
        },
        {
          "end": "2022-01-04T22:05:00+00:00",
          "reason": "missing_open",
          "start": "2022-01-04T22:03:00+00:00"
        },
        {
          "end": "2022-01-04T22:12:00+00:00",
          "reason": "missing_open",
          "start": "2022-01-04T22:08:00+00:00"
        },
        {
          "end": "2022-01-04T22:15:00+00:00",
          "reason": "missing_open",
          "start": "2022-01-04T22:13:00+00:00"
        },
        {
          "end": "2022-01-04T22:18:00+00:00",
          "reason": "missing_open",
          "start": "2022-01-04T22:16:00+00:00"
        },
        {
          "end": "2022-01-04T22:39:00+00:00",
          "reason": "missing_open",
          "start": "2022-01-04T22:37:00+00:00"
        },
        {
          "end": "2022-01-04T22:58:00+00:00",
          "reason": "missing_open",
          "start": "2022-01-04T22:57:00+00:00"
        },
        {
          "end": "2022-01-04T23:13:00+00:00",
          "reason": "missing_open",
          "start": "2022-01-04T23:12:00+00:00"
        },
        {
          "end": "2022-01-04T23:43:00+00:00",
          "reason": "missing_open",
          "start": "2022-01-04T23:42:00+00:00"
        },
        {
          "end": "2022-01-04T23:57:00+00:00",
          "reason": "missing_open",
          "start": "2022-01-04T23:56:00+00:00"
        },
        {
          "end": "2022-01-05T05:41:00+00:00",
          "reason": "missing_open",
          "start": "2022-01-05T05:40:00+00:00"
        },
        {
          "end": "2022-01-05T21:38:00+00:00",
          "reason": "missing_open",
          "start": "2022-01-05T21:37:00+00:00"
        },
        {
          "end": "2022-01-05T22:01:00+00:00",
          "reason": "missing_session_break",
          "start": "2022-01-05T21:59:00+00:00"
        },
        {
          "end": "2022-01-05T22:09:00+00:00",
          "reason": "missing_open",
          "start": "2022-01-05T22:08:00+00:00"
        },
        {
          "end": "2022-01-05T22:14:00+00:00",
          "reason": "missing_open",
          "start": "2022-01-05T22:10:00+00:00"
        },
        {
          "end": "2022-01-05T22:19:00+00:00",
          "reason": "missing_open",
          "start": "2022-01-05T22:16:00+00:00"
        },
        {
          "end": "2022-01-05T22:21:00+00:00",
          "reason": "missing_open",
          "start": "2022-01-05T22:20:00+00:00"
        },
        {
          "end": "2022-01-05T22:23:00+00:00",
          "reason": "missing_open",
          "start": "2022-01-05T22:22:00+00:00"
        },
        {
          "end": "2022-01-06T22:01:00+00:00",
          "reason": "missing_session_break",
          "start": "2022-01-06T21:59:00+00:00"
        },
        {
          "end": "2022-01-06T22:05:00+00:00",
          "reason": "missing_open",
          "start": "2022-01-06T22:02:00+00:00"
        },
        {
          "end": "2022-01-06T22:10:00+00:00",
          "reason": "missing_open",
          "start": "2022-01-06T22:09:00+00:00"
        },
        {
          "end": "2022-01-06T22:14:00+00:00",
          "reason": "missing_open",
          "start": "2022-01-06T22:13:00+00:00"
        },
        {
          "end": "2022-01-06T22:17:00+00:00",
          "reason": "missing_open",
          "start": "2022-01-06T22:16:00+00:00"
        },
        {
          "end": "2022-01-06T22:25:00+00:00",
          "reason": "missing_open",
          "start": "2022-01-06T22:24:00+00:00"
        },
        {
          "end": "2022-01-06T23:20:00+00:00",
          "reason": "missing_open",
          "start": "2022-01-06T23:19:00+00:00"
        },
        {
          "end": "2022-01-07T00:37:00+00:00",
          "reason": "missing_open",
          "start": "2022-01-07T00:36:00+00:00"
        },
        {
          "end": "2022-01-07T00:40:00+00:00",
          "reason": "missing_open",
          "start": "2022-01-07T00:39:00+00:00"
        },
        {
          "end": "2022-01-07T03:05:00+00:00",
          "reason": "missing_open",
          "start": "2022-01-07T03:04:00+00:00"
        },
        {
          "end": "2022-01-07T04:12:00+00:00",
          "reason": "missing_open",
          "start": "2022-01-07T04:11:00+00:00"
        },
        {
          "end": "2022-01-09T22:01:00+00:00",
          "reason": "missing_weekend",
          "start": "2022-01-07T21:55:00+00:00"
        },
        {
          "end": "2022-01-09T22:03:00+00:00",
          "reason": "missing_open",
          "start": "2022-01-09T22:02:00+00:00"
        },
        {
          "end": "2022-01-09T22:05:00+00:00",
          "reason": "missing_open",
          "start": "2022-01-09T22:04:00+00:00"
        },
        {
          "end": "2022-01-09T22:15:00+00:00",
          "reason": "missing_open",
          "start": "2022-01-09T22:14:00+00:00"
        },
        {
          "end": "2022-01-09T22:19:00+00:00",
          "reason": "missing_open",
          "start": "2022-01-09T22:18:00+00:00"
        },
        {
          "end": "2022-01-09T22:25:00+00:00",
          "reason": "missing_open",
          "start": "2022-01-09T22:21:00+00:00"
        },
        {
          "end": "2022-01-10T05:06:00+00:00",
          "reason": "missing_open",
          "start": "2022-01-10T05:05:00+00:00"
        },
        {
          "end": "2022-01-10T22:01:00+00:00",
          "reason": "missing_session_break",
          "start": "2022-01-10T21:59:00+00:00"
        },
        {
          "end": "2022-01-10T22:04:00+00:00",
          "reason": "missing_open",
          "start": "2022-01-10T22:02:00+00:00"
        },
        {
          "end": "2022-01-10T22:09:00+00:00",
          "reason": "missing_open",
          "start": "2022-01-10T22:08:00+00:00"
        },
        {
          "end": "2022-01-10T22:11:00+00:00",
          "reason": "missing_open",
          "start": "2022-01-10T22:10:00+00:00"
        },
        {
          "end": "2022-01-10T22:15:00+00:00",
          "reason": "missing_open",
          "start": "2022-01-10T22:14:00+00:00"
        },
        {
          "end": "2022-01-10T22:17:00+00:00",
          "reason": "missing_open",
          "start": "2022-01-10T22:16:00+00:00"
        },
        {
          "end": "2022-01-10T22:23:00+00:00",
          "reason": "missing_open",
          "start": "2022-01-10T22:19:00+00:00"
        },
        {
          "end": "2022-01-10T22:25:00+00:00",
          "reason": "missing_open",
          "start": "2022-01-10T22:24:00+00:00"
        },
        {
          "end": "2022-01-10T22:30:00+00:00",
          "reason": "missing_open",
          "start": "2022-01-10T22:29:00+00:00"
        },
        {
          "end": "2022-01-10T22:54:00+00:00",
          "reason": "missing_open",
          "start": "2022-01-10T22:53:00+00:00"
        },
        {
          "end": "2022-01-10T23:36:00+00:00",
          "reason": "missing_open",
          "start": "2022-01-10T23:35:00+00:00"
        },
        {
          "end": "2022-01-11T05:44:00+00:00",
          "reason": "missing_open",
          "start": "2022-01-11T05:43:00+00:00"
        },
        {
          "end": "2022-01-11T22:01:00+00:00",
          "reason": "missing_session_break",
          "start": "2022-01-11T21:59:00+00:00"
        },
        {
          "end": "2022-01-11T22:05:00+00:00",
          "reason": "missing_open",
          "start": "2022-01-11T22:02:00+00:00"
        },
        {
          "end": "2022-01-11T22:14:00+00:00",
          "reason": "missing_open",
          "start": "2022-01-11T22:10:00+00:00"
        },
        {
          "end": "2022-01-11T22:20:00+00:00",
          "reason": "missing_open",
          "start": "2022-01-11T22:19:00+00:00"
        },
        {
          "end": "2022-01-11T22:44:00+00:00",
          "reason": "missing_open",
          "start": "2022-01-11T22:41:00+00:00"
        },
        {
          "end": "2022-01-12T00:50:00+00:00",
          "reason": "missing_open",
          "start": "2022-01-12T00:49:00+00:00"
        },
        {
          "end": "2022-01-12T05:27:00+00:00",
          "reason": "missing_open",
          "start": "2022-01-12T05:26:00+00:00"
        },
        {
          "end": "2022-01-12T21:24:00+00:00",
          "reason": "missing_open",
          "start": "2022-01-12T21:23:00+00:00"
        },
        {
          "end": "2022-01-12T22:01:00+00:00",
          "reason": "missing_session_break",
          "start": "2022-01-12T21:59:00+00:00"
        },
        {
          "end": "2022-01-12T22:24:00+00:00",
          "reason": "missing_open",
          "start": "2022-01-12T22:23:00+00:00"
        },
        {
          "end": "2022-01-12T22:37:00+00:00",
          "reason": "missing_open",
          "start": "2022-01-12T22:35:00+00:00"
        },
        {
          "end": "2022-01-12T22:41:00+00:00",
          "reason": "missing_open",
          "start": "2022-01-12T22:39:00+00:00"
        },
        {
          "end": "2022-01-12T22:43:00+00:00",
          "reason": "missing_open",
          "start": "2022-01-12T22:42:00+00:00"
        },
        {
          "end": "2022-01-12T22:45:00+00:00",
          "reason": "missing_open",
          "start": "2022-01-12T22:44:00+00:00"
        },
        {
          "end": "2022-01-12T22:48:00+00:00",
          "reason": "missing_open",
          "start": "2022-01-12T22:46:00+00:00"
        },
        {
          "end": "2022-01-12T22:50:00+00:00",
          "reason": "missing_open",
          "start": "2022-01-12T22:49:00+00:00"
        },
        {
          "end": "2022-01-12T22:52:00+00:00",
          "reason": "missing_open",
          "start": "2022-01-12T22:51:00+00:00"
        },
        {
          "end": "2022-01-13T22:01:00+00:00",
          "reason": "missing_session_break",
          "start": "2022-01-13T21:59:00+00:00"
        },
        {
          "end": "2022-01-13T22:09:00+00:00",
          "reason": "missing_open",
          "start": "2022-01-13T22:08:00+00:00"
        },
        {
          "end": "2022-01-16T22:01:00+00:00",
          "reason": "missing_weekend",
          "start": "2022-01-14T21:55:00+00:00"
        },
        {
          "end": "2022-01-16T22:05:00+00:00",
          "reason": "missing_open",
          "start": "2022-01-16T22:02:00+00:00"
        },
        {
          "end": "2022-01-16T22:11:00+00:00",
          "reason": "missing_open",
          "start": "2022-01-16T22:10:00+00:00"
        },
        {
          "end": "2022-01-16T22:13:00+00:00",
          "reason": "missing_open",
          "start": "2022-01-16T22:12:00+00:00"
        },
        {
          "end": "2022-01-16T22:18:00+00:00",
          "reason": "missing_open",
          "start": "2022-01-16T22:17:00+00:00"
        },
        {
          "end": "2022-01-16T22:23:00+00:00",
          "reason": "missing_open",
          "start": "2022-01-16T22:19:00+00:00"
        },
        {
          "end": "2022-01-16T22:34:00+00:00",
          "reason": "missing_open",
          "start": "2022-01-16T22:32:00+00:00"
        },
        {
          "end": "2022-01-16T22:42:00+00:00",
          "reason": "missing_open",
          "start": "2022-01-16T22:41:00+00:00"
        },
        {
          "end": "2022-01-16T22:46:00+00:00",
          "reason": "missing_open",
          "start": "2022-01-16T22:45:00+00:00"
        },
        {
          "end": "2022-01-16T22:57:00+00:00",
          "reason": "missing_open",
          "start": "2022-01-16T22:56:00+00:00"
        },
        {
          "end": "2022-01-16T23:00:00+00:00",
          "reason": "missing_open",
          "start": "2022-01-16T22:59:00+00:00"
        },
        {
          "end": "2022-01-17T18:37:00+00:00",
          "reason": "missing_open",
          "start": "2022-01-17T18:36:00+00:00"
        },
        {
          "end": "2022-01-17T19:05:00+00:00",
          "reason": "missing_open",
          "start": "2022-01-17T19:04:00+00:00"
        },
        {
          "end": "2022-01-17T19:15:00+00:00",
          "reason": "missing_open",
          "start": "2022-01-17T19:14:00+00:00"
        },
        {
          "end": "2022-01-17T20:07:00+00:00",
          "reason": "missing_open",
          "start": "2022-01-17T20:06:00+00:00"
        },
        {
          "end": "2022-01-17T20:18:00+00:00",
          "reason": "missing_open",
          "start": "2022-01-17T20:17:00+00:00"
        },
        {
          "end": "2022-01-17T20:20:00+00:00",
          "reason": "missing_open",
          "start": "2022-01-17T20:19:00+00:00"
        },
        {
          "end": "2022-01-17T20:27:00+00:00",
          "reason": "missing_open",
          "start": "2022-01-17T20:26:00+00:00"
        },
        {
          "end": "2022-01-17T20:37:00+00:00",
          "reason": "missing_open",
          "start": "2022-01-17T20:36:00+00:00"
        },
        {
          "end": "2022-01-17T20:44:00+00:00",
          "reason": "missing_open",
          "start": "2022-01-17T20:42:00+00:00"
        },
        {
          "end": "2022-01-17T20:48:00+00:00",
          "reason": "missing_open",
          "start": "2022-01-17T20:47:00+00:00"
        },
        {
          "end": "2022-01-17T20:53:00+00:00",
          "reason": "missing_open",
          "start": "2022-01-17T20:52:00+00:00"
        },
        {
          "end": "2022-01-17T21:09:00+00:00",
          "reason": "missing_open",
          "start": "2022-01-17T21:08:00+00:00"
        },
        {
          "end": "2022-01-17T21:19:00+00:00",
          "reason": "missing_open",
          "start": "2022-01-17T21:18:00+00:00"
        },
        {
          "end": "2022-01-17T21:22:00+00:00",
          "reason": "missing_open",
          "start": "2022-01-17T21:21:00+00:00"
        },
        {
          "end": "2022-01-17T21:38:00+00:00",
          "reason": "missing_open",
          "start": "2022-01-17T21:37:00+00:00"
        },
        {
          "end": "2022-01-17T21:40:00+00:00",
          "reason": "missing_open",
          "start": "2022-01-17T21:39:00+00:00"
        },
        {
          "end": "2022-01-17T21:43:00+00:00",
          "reason": "missing_open",
          "start": "2022-01-17T21:42:00+00:00"
        },
        {
          "end": "2022-01-17T21:45:00+00:00",
          "reason": "missing_open",
          "start": "2022-01-17T21:44:00+00:00"
        },
        {
          "end": "2022-01-17T22:01:00+00:00",
          "reason": "missing_session_break",
          "start": "2022-01-17T21:59:00+00:00"
        },
        {
          "end": "2022-01-17T22:05:00+00:00",
          "reason": "missing_open",
          "start": "2022-01-17T22:04:00+00:00"
        },
        {
          "end": "2022-01-17T22:09:00+00:00",
          "reason": "missing_open",
          "start": "2022-01-17T22:08:00+00:00"
        },
        {
          "end": "2022-01-17T22:19:00+00:00",
          "reason": "missing_open",
          "start": "2022-01-17T22:18:00+00:00"
        },
        {
          "end": "2022-01-17T22:52:00+00:00",
          "reason": "missing_open",
          "start": "2022-01-17T22:51:00+00:00"
        },
        {
          "end": "2022-01-17T22:54:00+00:00",
          "reason": "missing_open",
          "start": "2022-01-17T22:53:00+00:00"
        },
        {
          "end": "2022-01-18T21:39:00+00:00",
          "reason": "missing_open",
          "start": "2022-01-18T21:38:00+00:00"
        },
        {
          "end": "2022-01-18T22:01:00+00:00",
          "reason": "missing_session_break",
          "start": "2022-01-18T21:59:00+00:00"
        },
        {
          "end": "2022-01-18T22:04:00+00:00",
          "reason": "missing_open",
          "start": "2022-01-18T22:02:00+00:00"
        },
        {
          "end": "2022-01-19T22:01:00+00:00",
          "reason": "missing_session_break",
          "start": "2022-01-19T21:59:00+00:00"
        },
        {
          "end": "2022-01-19T22:10:00+00:00",
          "reason": "missing_open",
          "start": "2022-01-19T22:09:00+00:00"
        },
        {
          "end": "2022-01-19T22:35:00+00:00",
          "reason": "missing_open",
          "start": "2022-01-19T22:34:00+00:00"
        },
        {
          "end": "2022-01-20T22:01:00+00:00",
          "reason": "missing_session_break",
          "start": "2022-01-20T21:59:00+00:00"
        },
        {
          "end": "2022-01-20T22:05:00+00:00",
          "reason": "missing_open",
          "start": "2022-01-20T22:02:00+00:00"
        },
        {
          "end": "2022-01-20T22:13:00+00:00",
          "reason": "missing_open",
          "start": "2022-01-20T22:11:00+00:00"
        },
        {
          "end": "2022-01-20T22:15:00+00:00",
          "reason": "missing_open",
          "start": "2022-01-20T22:14:00+00:00"
        },
        {
          "end": "2022-01-20T22:25:00+00:00",
          "reason": "missing_open",
          "start": "2022-01-20T22:24:00+00:00"
        },
        {
          "end": "2022-01-20T22:40:00+00:00",
          "reason": "missing_open",
          "start": "2022-01-20T22:39:00+00:00"
        },
        {
          "end": "2022-01-20T22:44:00+00:00",
          "reason": "missing_open",
          "start": "2022-01-20T22:43:00+00:00"
        },
        {
          "end": "2022-01-23T22:01:00+00:00",
          "reason": "missing_weekend",
          "start": "2022-01-21T21:55:00+00:00"
        },
        {
          "end": "2022-01-23T22:05:00+00:00",
          "reason": "missing_open",
          "start": "2022-01-23T22:04:00+00:00"
        },
        {
          "end": "2022-01-23T22:10:00+00:00",
          "reason": "missing_open",
          "start": "2022-01-23T22:09:00+00:00"
        },
        {
          "end": "2022-01-23T22:14:00+00:00",
          "reason": "missing_open",
          "start": "2022-01-23T22:13:00+00:00"
        },
        {
          "end": "2022-01-23T22:30:00+00:00",
          "reason": "missing_open",
          "start": "2022-01-23T22:29:00+00:00"
        },
        {
          "end": "2022-01-23T22:34:00+00:00",
          "reason": "missing_open",
          "start": "2022-01-23T22:33:00+00:00"
        },
        {
          "end": "2022-01-24T22:01:00+00:00",
          "reason": "missing_session_break",
          "start": "2022-01-24T21:59:00+00:00"
        },
        {
          "end": "2022-01-24T22:05:00+00:00",
          "reason": "missing_open",
          "start": "2022-01-24T22:03:00+00:00"
        },
        {
          "end": "2022-01-24T22:15:00+00:00",
          "reason": "missing_open",
          "start": "2022-01-24T22:12:00+00:00"
        },
        {
          "end": "2022-01-24T22:17:00+00:00",
          "reason": "missing_open",
          "start": "2022-01-24T22:16:00+00:00"
        },
        {
          "end": "2022-01-24T22:20:00+00:00",
          "reason": "missing_open",
          "start": "2022-01-24T22:18:00+00:00"
        },
        {
          "end": "2022-01-24T22:24:00+00:00",
          "reason": "missing_open",
          "start": "2022-01-24T22:22:00+00:00"
        },
        {
          "end": "2022-01-24T23:39:00+00:00",
          "reason": "missing_open",
          "start": "2022-01-24T23:38:00+00:00"
        },
        {
          "end": "2022-01-24T23:59:00+00:00",
          "reason": "missing_open",
          "start": "2022-01-24T23:58:00+00:00"
        },
        {
          "end": "2022-01-25T22:01:00+00:00",
          "reason": "missing_session_break",
          "start": "2022-01-25T21:59:00+00:00"
        },
        {
          "end": "2022-01-25T22:25:00+00:00",
          "reason": "missing_open",
          "start": "2022-01-25T22:23:00+00:00"
        },
        {
          "end": "2022-01-25T22:30:00+00:00",
          "reason": "missing_open",
          "start": "2022-01-25T22:29:00+00:00"
        },
        {
          "end": "2022-01-25T22:39:00+00:00",
          "reason": "missing_open",
          "start": "2022-01-25T22:38:00+00:00"
        },
        {
          "end": "2022-01-25T22:54:00+00:00",
          "reason": "missing_open",
          "start": "2022-01-25T22:53:00+00:00"
        },
        {
          "end": "2022-01-26T03:15:00+00:00",
          "reason": "missing_open",
          "start": "2022-01-26T03:14:00+00:00"
        },
        {
          "end": "2022-01-26T03:21:00+00:00",
          "reason": "missing_open",
          "start": "2022-01-26T03:19:00+00:00"
        },
        {
          "end": "2022-01-26T05:34:00+00:00",
          "reason": "missing_open",
          "start": "2022-01-26T05:33:00+00:00"
        },
        {
          "end": "2022-01-26T22:01:00+00:00",
          "reason": "missing_session_break",
          "start": "2022-01-26T21:59:00+00:00"
        },
        {
          "end": "2022-01-26T22:06:00+00:00",
          "reason": "missing_open",
          "start": "2022-01-26T22:04:00+00:00"
        },
        {
          "end": "2022-01-26T22:09:00+00:00",
          "reason": "missing_open",
          "start": "2022-01-26T22:07:00+00:00"
        },
        {
          "end": "2022-01-26T22:54:00+00:00",
          "reason": "missing_open",
          "start": "2022-01-26T22:53:00+00:00"
        },
        {
          "end": "2022-01-27T03:18:00+00:00",
          "reason": "missing_open",
          "start": "2022-01-27T03:17:00+00:00"
        },
        {
          "end": "2022-01-27T22:00:00+00:00",
          "reason": "observed_session_break",
          "start": "2022-01-27T21:59:00+00:00"
        },
        {
          "end": "2022-01-27T22:01:00+00:00",
          "reason": "missing_session_break",
          "start": "2022-01-27T22:00:00+00:00"
        },
        {
          "end": "2022-01-27T22:08:00+00:00",
          "reason": "missing_open",
          "start": "2022-01-27T22:07:00+00:00"
        },
        {
          "end": "2022-01-27T22:13:00+00:00",
          "reason": "missing_open",
          "start": "2022-01-27T22:11:00+00:00"
        },
        {
          "end": "2022-01-28T21:27:00+00:00",
          "reason": "missing_open",
          "start": "2022-01-28T21:24:00+00:00"
        },
        {
          "end": "2022-01-30T22:01:00+00:00",
          "reason": "missing_weekend",
          "start": "2022-01-28T21:55:00+00:00"
        },
        {
          "end": "2022-01-30T22:14:00+00:00",
          "reason": "missing_open",
          "start": "2022-01-30T22:11:00+00:00"
        },
        {
          "end": "2022-01-30T22:17:00+00:00",
          "reason": "missing_open",
          "start": "2022-01-30T22:15:00+00:00"
        },
        {
          "end": "2022-01-30T22:22:00+00:00",
          "reason": "missing_open",
          "start": "2022-01-30T22:18:00+00:00"
        },
        {
          "end": "2022-01-30T22:25:00+00:00",
          "reason": "missing_open",
          "start": "2022-01-30T22:24:00+00:00"
        },
        {
          "end": "2022-01-30T22:37:00+00:00",
          "reason": "missing_open",
          "start": "2022-01-30T22:36:00+00:00"
        },
        {
          "end": "2022-01-30T22:41:00+00:00",
          "reason": "missing_open",
          "start": "2022-01-30T22:40:00+00:00"
        },
        {
          "end": "2022-01-30T22:43:00+00:00",
          "reason": "missing_open",
          "start": "2022-01-30T22:42:00+00:00"
        },
        {
          "end": "2022-01-30T22:45:00+00:00",
          "reason": "missing_open",
          "start": "2022-01-30T22:44:00+00:00"
        },
        {
          "end": "2022-01-30T22:49:00+00:00",
          "reason": "missing_open",
          "start": "2022-01-30T22:48:00+00:00"
        },
        {
          "end": "2022-01-30T22:53:00+00:00",
          "reason": "missing_open",
          "start": "2022-01-30T22:52:00+00:00"
        },
        {
          "end": "2022-01-31T21:18:00+00:00",
          "reason": "missing_open",
          "start": "2022-01-31T21:17:00+00:00"
        },
        {
          "end": "2022-01-31T22:00:00+00:00",
          "reason": "observed_session_break",
          "start": "2022-01-31T21:59:00+00:00"
        },
        {
          "end": "2022-01-31T22:01:00+00:00",
          "reason": "missing_session_break",
          "start": "2022-01-31T22:00:00+00:00"
        },
        {
          "end": "2022-01-31T22:10:00+00:00",
          "reason": "missing_open",
          "start": "2022-01-31T22:07:00+00:00"
        },
        {
          "end": "2022-01-31T22:17:00+00:00",
          "reason": "missing_open",
          "start": "2022-01-31T22:16:00+00:00"
        },
        {
          "end": "2022-01-31T22:20:00+00:00",
          "reason": "missing_open",
          "start": "2022-01-31T22:18:00+00:00"
        },
        {
          "end": "2022-01-31T22:34:00+00:00",
          "reason": "missing_open",
          "start": "2022-01-31T22:30:00+00:00"
        },
        {
          "end": "2022-01-31T22:40:00+00:00",
          "reason": "missing_open",
          "start": "2022-01-31T22:38:00+00:00"
        },
        {
          "end": "2022-01-31T22:42:00+00:00",
          "reason": "missing_open",
          "start": "2022-01-31T22:41:00+00:00"
        },
        {
          "end": "2022-01-31T22:57:00+00:00",
          "reason": "missing_open",
          "start": "2022-01-31T22:56:00+00:00"
        },
        {
          "end": "2022-02-01T03:17:00+00:00",
          "reason": "missing_open",
          "start": "2022-02-01T03:16:00+00:00"
        },
        {
          "end": "2022-02-01T22:00:00+00:00",
          "reason": "observed_session_break",
          "start": "2022-02-01T21:59:00+00:00"
        },
        {
          "end": "2022-02-01T22:01:00+00:00",
          "reason": "missing_session_break",
          "start": "2022-02-01T22:00:00+00:00"
        },
        {
          "end": "2022-02-01T22:08:00+00:00",
          "reason": "missing_open",
          "start": "2022-02-01T22:06:00+00:00"
        },
        {
          "end": "2022-02-01T22:10:00+00:00",
          "reason": "missing_open",
          "start": "2022-02-01T22:09:00+00:00"
        },
        {
          "end": "2022-02-01T22:13:00+00:00",
          "reason": "missing_open",
          "start": "2022-02-01T22:12:00+00:00"
        },
        {
          "end": "2022-02-01T22:16:00+00:00",
          "reason": "missing_open",
          "start": "2022-02-01T22:15:00+00:00"
        },
        {
          "end": "2022-02-01T22:21:00+00:00",
          "reason": "missing_open",
          "start": "2022-02-01T22:20:00+00:00"
        },
        {
          "end": "2022-02-01T22:40:00+00:00",
          "reason": "missing_open",
          "start": "2022-02-01T22:36:00+00:00"
        },
        {
          "end": "2022-02-01T22:47:00+00:00",
          "reason": "missing_open",
          "start": "2022-02-01T22:46:00+00:00"
        },
        {
          "end": "2022-02-01T23:58:00+00:00",
          "reason": "missing_open",
          "start": "2022-02-01T23:57:00+00:00"
        },
        {
          "end": "2022-02-02T21:27:00+00:00",
          "reason": "missing_open",
          "start": "2022-02-02T21:26:00+00:00"
        },
        {
          "end": "2022-02-02T22:00:00+00:00",
          "reason": "observed_session_break",
          "start": "2022-02-02T21:59:00+00:00"
        },
        {
          "end": "2022-02-02T22:01:00+00:00",
          "reason": "missing_session_break",
          "start": "2022-02-02T22:00:00+00:00"
        },
        {
          "end": "2022-02-02T22:05:00+00:00",
          "reason": "missing_open",
          "start": "2022-02-02T22:04:00+00:00"
        },
        {
          "end": "2022-02-02T22:10:00+00:00",
          "reason": "missing_open",
          "start": "2022-02-02T22:07:00+00:00"
        },
        {
          "end": "2022-02-02T22:15:00+00:00",
          "reason": "missing_open",
          "start": "2022-02-02T22:14:00+00:00"
        },
        {
          "end": "2022-02-02T22:38:00+00:00",
          "reason": "missing_open",
          "start": "2022-02-02T22:37:00+00:00"
        },
        {
          "end": "2022-02-02T22:41:00+00:00",
          "reason": "missing_open",
          "start": "2022-02-02T22:40:00+00:00"
        },
        {
          "end": "2022-02-02T22:51:00+00:00",
          "reason": "missing_open",
          "start": "2022-02-02T22:50:00+00:00"
        },
        {
          "end": "2022-02-02T23:05:00+00:00",
          "reason": "missing_open",
          "start": "2022-02-02T23:04:00+00:00"
        },
        {
          "end": "2022-02-03T22:00:00+00:00",
          "reason": "observed_session_break",
          "start": "2022-02-03T21:59:00+00:00"
        },
        {
          "end": "2022-02-03T22:01:00+00:00",
          "reason": "missing_session_break",
          "start": "2022-02-03T22:00:00+00:00"
        },
        {
          "end": "2022-02-03T22:50:00+00:00",
          "reason": "missing_open",
          "start": "2022-02-03T22:49:00+00:00"
        },
        {
          "end": "2022-02-03T22:59:00+00:00",
          "reason": "missing_open",
          "start": "2022-02-03T22:57:00+00:00"
        },
        {
          "end": "2022-02-06T22:01:00+00:00",
          "reason": "missing_weekend",
          "start": "2022-02-04T21:55:00+00:00"
        },
        {
          "end": "2022-02-06T22:20:00+00:00",
          "reason": "missing_open",
          "start": "2022-02-06T22:19:00+00:00"
        },
        {
          "end": "2022-02-07T00:36:00+00:00",
          "reason": "missing_open",
          "start": "2022-02-07T00:35:00+00:00"
        },
        {
          "end": "2022-02-07T21:09:00+00:00",
          "reason": "missing_open",
          "start": "2022-02-07T21:08:00+00:00"
        },
        {
          "end": "2022-02-07T21:28:00+00:00",
          "reason": "missing_open",
          "start": "2022-02-07T21:27:00+00:00"
        },
        {
          "end": "2022-02-07T22:01:00+00:00",
          "reason": "missing_session_break",
          "start": "2022-02-07T21:59:00+00:00"
        },
        {
          "end": "2022-02-07T22:04:00+00:00",
          "reason": "missing_open",
          "start": "2022-02-07T22:02:00+00:00"
        },
        {
          "end": "2022-02-07T22:09:00+00:00",
          "reason": "missing_open",
          "start": "2022-02-07T22:08:00+00:00"
        },
        {
          "end": "2022-02-08T04:37:00+00:00",
          "reason": "missing_open",
          "start": "2022-02-08T04:36:00+00:00"
        },
        {
          "end": "2022-02-08T21:14:00+00:00",
          "reason": "missing_open",
          "start": "2022-02-08T21:12:00+00:00"
        },
        {
          "end": "2022-02-08T21:23:00+00:00",
          "reason": "missing_open",
          "start": "2022-02-08T21:22:00+00:00"
        },
        {
          "end": "2022-02-08T21:28:00+00:00",
          "reason": "missing_open",
          "start": "2022-02-08T21:27:00+00:00"
        },
        {
          "end": "2022-02-08T21:47:00+00:00",
          "reason": "missing_open",
          "start": "2022-02-08T21:46:00+00:00"
        },
        {
          "end": "2022-02-08T22:00:00+00:00",
          "reason": "observed_session_break",
          "start": "2022-02-08T21:59:00+00:00"
        },
        {
          "end": "2022-02-08T22:01:00+00:00",
          "reason": "missing_session_break",
          "start": "2022-02-08T22:00:00+00:00"
        },
        {
          "end": "2022-02-08T22:09:00+00:00",
          "reason": "missing_open",
          "start": "2022-02-08T22:08:00+00:00"
        },
        {
          "end": "2022-02-08T22:12:00+00:00",
          "reason": "missing_open",
          "start": "2022-02-08T22:11:00+00:00"
        },
        {
          "end": "2022-02-08T22:28:00+00:00",
          "reason": "missing_open",
          "start": "2022-02-08T22:26:00+00:00"
        },
        {
          "end": "2022-02-08T23:36:00+00:00",
          "reason": "missing_open",
          "start": "2022-02-08T23:35:00+00:00"
        },
        {
          "end": "2022-02-09T00:15:00+00:00",
          "reason": "missing_open",
          "start": "2022-02-09T00:14:00+00:00"
        },
        {
          "end": "2022-02-09T22:00:00+00:00",
          "reason": "observed_session_break",
          "start": "2022-02-09T21:59:00+00:00"
        },
        {
          "end": "2022-02-09T22:01:00+00:00",
          "reason": "missing_session_break",
          "start": "2022-02-09T22:00:00+00:00"
        },
        {
          "end": "2022-02-09T22:03:00+00:00",
          "reason": "missing_open",
          "start": "2022-02-09T22:02:00+00:00"
        },
        {
          "end": "2022-02-09T22:11:00+00:00",
          "reason": "missing_open",
          "start": "2022-02-09T22:08:00+00:00"
        },
        {
          "end": "2022-02-09T22:13:00+00:00",
          "reason": "missing_open",
          "start": "2022-02-09T22:12:00+00:00"
        },
        {
          "end": "2022-02-09T22:18:00+00:00",
          "reason": "missing_open",
          "start": "2022-02-09T22:16:00+00:00"
        },
        {
          "end": "2022-02-09T22:38:00+00:00",
          "reason": "missing_open",
          "start": "2022-02-09T22:37:00+00:00"
        },
        {
          "end": "2022-02-09T22:55:00+00:00",
          "reason": "missing_open",
          "start": "2022-02-09T22:54:00+00:00"
        },
        {
          "end": "2022-02-09T23:33:00+00:00",
          "reason": "missing_open",
          "start": "2022-02-09T23:32:00+00:00"
        },
        {
          "end": "2022-02-10T22:00:00+00:00",
          "reason": "observed_session_break",
          "start": "2022-02-10T21:59:00+00:00"
        },
        {
          "end": "2022-02-10T22:01:00+00:00",
          "reason": "missing_session_break",
          "start": "2022-02-10T22:00:00+00:00"
        },
        {
          "end": "2022-02-10T22:10:00+00:00",
          "reason": "missing_open",
          "start": "2022-02-10T22:07:00+00:00"
        },
        {
          "end": "2022-02-10T22:12:00+00:00",
          "reason": "missing_open",
          "start": "2022-02-10T22:11:00+00:00"
        },
        {
          "end": "2022-02-10T22:28:00+00:00",
          "reason": "missing_open",
          "start": "2022-02-10T22:27:00+00:00"
        },
        {
          "end": "2022-02-10T22:30:00+00:00",
          "reason": "missing_open",
          "start": "2022-02-10T22:29:00+00:00"
        },
        {
          "end": "2022-02-10T22:51:00+00:00",
          "reason": "missing_open",
          "start": "2022-02-10T22:50:00+00:00"
        },
        {
          "end": "2022-02-13T22:01:00+00:00",
          "reason": "missing_weekend",
          "start": "2022-02-11T21:55:00+00:00"
        },
        {
          "end": "2022-02-13T22:11:00+00:00",
          "reason": "missing_open",
          "start": "2022-02-13T22:07:00+00:00"
        },
        {
          "end": "2022-02-13T22:36:00+00:00",
          "reason": "missing_open",
          "start": "2022-02-13T22:34:00+00:00"
        },
        {
          "end": "2022-02-14T22:00:00+00:00",
          "reason": "observed_session_break",
          "start": "2022-02-14T21:59:00+00:00"
        },
        {
          "end": "2022-02-14T22:01:00+00:00",
          "reason": "missing_session_break",
          "start": "2022-02-14T22:00:00+00:00"
        },
        {
          "end": "2022-02-14T22:09:00+00:00",
          "reason": "missing_open",
          "start": "2022-02-14T22:08:00+00:00"
        },
        {
          "end": "2022-02-14T22:24:00+00:00",
          "reason": "missing_open",
          "start": "2022-02-14T22:19:00+00:00"
        },
        {
          "end": "2022-02-14T22:34:00+00:00",
          "reason": "missing_open",
          "start": "2022-02-14T22:33:00+00:00"
        },
        {
          "end": "2022-02-14T22:40:00+00:00",
          "reason": "missing_open",
          "start": "2022-02-14T22:37:00+00:00"
        },
        {
          "end": "2022-02-14T22:48:00+00:00",
          "reason": "missing_open",
          "start": "2022-02-14T22:47:00+00:00"
        },
        {
          "end": "2022-02-14T22:53:00+00:00",
          "reason": "missing_open",
          "start": "2022-02-14T22:52:00+00:00"
        },
        {
          "end": "2022-02-15T22:00:00+00:00",
          "reason": "observed_session_break",
          "start": "2022-02-15T21:59:00+00:00"
        },
        {
          "end": "2022-02-15T22:01:00+00:00",
          "reason": "missing_session_break",
          "start": "2022-02-15T22:00:00+00:00"
        },
        {
          "end": "2022-02-15T22:05:00+00:00",
          "reason": "missing_open",
          "start": "2022-02-15T22:03:00+00:00"
        },
        {
          "end": "2022-02-15T22:13:00+00:00",
          "reason": "missing_open",
          "start": "2022-02-15T22:12:00+00:00"
        },
        {
          "end": "2022-02-15T22:22:00+00:00",
          "reason": "missing_open",
          "start": "2022-02-15T22:20:00+00:00"
        },
        {
          "end": "2022-02-15T22:40:00+00:00",
          "reason": "missing_open",
          "start": "2022-02-15T22:37:00+00:00"
        },
        {
          "end": "2022-02-16T22:00:00+00:00",
          "reason": "observed_session_break",
          "start": "2022-02-16T21:59:00+00:00"
        },
        {
          "end": "2022-02-16T22:01:00+00:00",
          "reason": "missing_session_break",
          "start": "2022-02-16T22:00:00+00:00"
        },
        {
          "end": "2022-02-16T22:03:00+00:00",
          "reason": "missing_open",
          "start": "2022-02-16T22:02:00+00:00"
        },
        {
          "end": "2022-02-16T22:05:00+00:00",
          "reason": "missing_open",
          "start": "2022-02-16T22:04:00+00:00"
        },
        {
          "end": "2022-02-16T22:20:00+00:00",
          "reason": "missing_open",
          "start": "2022-02-16T22:18:00+00:00"
        },
        {
          "end": "2022-02-16T22:47:00+00:00",
          "reason": "missing_open",
          "start": "2022-02-16T22:45:00+00:00"
        },
        {
          "end": "2022-02-16T22:50:00+00:00",
          "reason": "missing_open",
          "start": "2022-02-16T22:49:00+00:00"
        },
        {
          "end": "2022-02-17T21:38:00+00:00",
          "reason": "missing_open",
          "start": "2022-02-17T21:37:00+00:00"
        },
        {
          "end": "2022-02-17T22:01:00+00:00",
          "reason": "missing_session_break",
          "start": "2022-02-17T21:59:00+00:00"
        },
        {
          "end": "2022-02-17T22:03:00+00:00",
          "reason": "missing_open",
          "start": "2022-02-17T22:02:00+00:00"
        },
        {
          "end": "2022-02-17T22:13:00+00:00",
          "reason": "missing_open",
          "start": "2022-02-17T22:11:00+00:00"
        },
        {
          "end": "2022-02-17T22:49:00+00:00",
          "reason": "missing_open",
          "start": "2022-02-17T22:48:00+00:00"
        },
        {
          "end": "2022-02-20T22:01:00+00:00",
          "reason": "missing_weekend",
          "start": "2022-02-18T21:55:00+00:00"
        },
        {
          "end": "2022-02-20T22:13:00+00:00",
          "reason": "missing_open",
          "start": "2022-02-20T22:12:00+00:00"
        },
        {
          "end": "2022-02-20T22:19:00+00:00",
          "reason": "missing_open",
          "start": "2022-02-20T22:18:00+00:00"
        },
        {
          "end": "2022-02-20T22:26:00+00:00",
          "reason": "missing_open",
          "start": "2022-02-20T22:22:00+00:00"
        },
        {
          "end": "2022-02-20T22:55:00+00:00",
          "reason": "missing_open",
          "start": "2022-02-20T22:53:00+00:00"
        },
        {
          "end": "2022-02-21T22:00:00+00:00",
          "reason": "observed_session_break",
          "start": "2022-02-21T21:59:00+00:00"
        },
        {
          "end": "2022-02-21T22:01:00+00:00",
          "reason": "missing_session_break",
          "start": "2022-02-21T22:00:00+00:00"
        },
        {
          "end": "2022-02-21T22:09:00+00:00",
          "reason": "missing_open",
          "start": "2022-02-21T22:08:00+00:00"
        },
        {
          "end": "2022-02-21T22:15:00+00:00",
          "reason": "missing_open",
          "start": "2022-02-21T22:13:00+00:00"
        },
        {
          "end": "2022-02-21T22:41:00+00:00",
          "reason": "missing_open",
          "start": "2022-02-21T22:40:00+00:00"
        },
        {
          "end": "2022-02-22T22:00:00+00:00",
          "reason": "observed_session_break",
          "start": "2022-02-22T21:59:00+00:00"
        },
        {
          "end": "2022-02-22T22:01:00+00:00",
          "reason": "missing_session_break",
          "start": "2022-02-22T22:00:00+00:00"
        },
        {
          "end": "2022-02-22T22:34:00+00:00",
          "reason": "missing_open",
          "start": "2022-02-22T22:33:00+00:00"
        },
        {
          "end": "2022-02-22T22:42:00+00:00",
          "reason": "missing_open",
          "start": "2022-02-22T22:41:00+00:00"
        },
        {
          "end": "2022-02-22T22:44:00+00:00",
          "reason": "missing_open",
          "start": "2022-02-22T22:43:00+00:00"
        },
        {
          "end": "2022-02-22T22:51:00+00:00",
          "reason": "missing_open",
          "start": "2022-02-22T22:50:00+00:00"
        },
        {
          "end": "2022-02-22T23:28:00+00:00",
          "reason": "missing_open",
          "start": "2022-02-22T23:27:00+00:00"
        },
        {
          "end": "2022-02-23T03:20:00+00:00",
          "reason": "missing_open",
          "start": "2022-02-23T03:19:00+00:00"
        },
        {
          "end": "2022-02-23T04:36:00+00:00",
          "reason": "missing_open",
          "start": "2022-02-23T04:35:00+00:00"
        },
        {
          "end": "2022-02-23T22:00:00+00:00",
          "reason": "observed_session_break",
          "start": "2022-02-23T21:59:00+00:00"
        },
        {
          "end": "2022-02-23T22:01:00+00:00",
          "reason": "missing_session_break",
          "start": "2022-02-23T22:00:00+00:00"
        },
        {
          "end": "2022-02-23T22:05:00+00:00",
          "reason": "missing_open",
          "start": "2022-02-23T22:04:00+00:00"
        },
        {
          "end": "2022-02-23T22:47:00+00:00",
          "reason": "missing_open",
          "start": "2022-02-23T22:46:00+00:00"
        },
        {
          "end": "2022-02-24T00:22:00+00:00",
          "reason": "missing_open",
          "start": "2022-02-24T00:21:00+00:00"
        },
        {
          "end": "2022-02-24T22:00:00+00:00",
          "reason": "observed_session_break",
          "start": "2022-02-24T21:59:00+00:00"
        },
        {
          "end": "2022-02-24T22:01:00+00:00",
          "reason": "missing_session_break",
          "start": "2022-02-24T22:00:00+00:00"
        },
        {
          "end": "2022-02-24T22:05:00+00:00",
          "reason": "missing_open",
          "start": "2022-02-24T22:02:00+00:00"
        },
        {
          "end": "2022-02-24T22:07:00+00:00",
          "reason": "missing_open",
          "start": "2022-02-24T22:06:00+00:00"
        },
        {
          "end": "2022-02-24T22:13:00+00:00",
          "reason": "missing_open",
          "start": "2022-02-24T22:10:00+00:00"
        },
        {
          "end": "2022-02-24T22:26:00+00:00",
          "reason": "missing_open",
          "start": "2022-02-24T22:25:00+00:00"
        },
        {
          "end": "2022-02-24T22:29:00+00:00",
          "reason": "missing_open",
          "start": "2022-02-24T22:28:00+00:00"
        },
        {
          "end": "2022-02-27T22:01:00+00:00",
          "reason": "missing_weekend",
          "start": "2022-02-25T21:55:00+00:00"
        },
        {
          "end": "2022-02-28T21:39:00+00:00",
          "reason": "missing_open",
          "start": "2022-02-28T21:38:00+00:00"
        },
        {
          "end": "2022-02-28T22:00:00+00:00",
          "reason": "observed_session_break",
          "start": "2022-02-28T21:59:00+00:00"
        },
        {
          "end": "2022-02-28T22:01:00+00:00",
          "reason": "missing_session_break",
          "start": "2022-02-28T22:00:00+00:00"
        },
        {
          "end": "2022-02-28T22:03:00+00:00",
          "reason": "missing_open",
          "start": "2022-02-28T22:02:00+00:00"
        },
        {
          "end": "2022-02-28T22:05:00+00:00",
          "reason": "missing_open",
          "start": "2022-02-28T22:04:00+00:00"
        },
        {
          "end": "2022-02-28T22:11:00+00:00",
          "reason": "missing_open",
          "start": "2022-02-28T22:10:00+00:00"
        },
        {
          "end": "2022-02-28T22:15:00+00:00",
          "reason": "missing_open",
          "start": "2022-02-28T22:14:00+00:00"
        },
        {
          "end": "2022-02-28T22:21:00+00:00",
          "reason": "missing_open",
          "start": "2022-02-28T22:19:00+00:00"
        },
        {
          "end": "2022-02-28T22:23:00+00:00",
          "reason": "missing_open",
          "start": "2022-02-28T22:22:00+00:00"
        },
        {
          "end": "2022-02-28T22:25:00+00:00",
          "reason": "missing_open",
          "start": "2022-02-28T22:24:00+00:00"
        },
        {
          "end": "2022-02-28T22:28:00+00:00",
          "reason": "missing_open",
          "start": "2022-02-28T22:27:00+00:00"
        }
      ],
      "dst_transitions": 0,
      "first_open": "2022-01-02T22:01:00+00:00",
      "gap_count": 301,
      "internal_missing_minutes": 23516,
      "last_offset": -18000.0,
      "last_open": "2022-02-28T23:59:00+00:00",
      "missing_by_reason": {
        "open": 378,
        "session_break": 50,
        "weekend": 25849
      },
      "out_of_session": 16,
      "rows": 58683
    },
    "schema_version": 1,
    "seed": 20261027,
    "status": "engineering_complete",
    "training_weights_hash": "04c3d83d04cbb5e6b061734915dac52ab70eed1174a839cf464f986c97c0eb9f"
  },
  "matching_committed_files": 721,
  "logical_state_hash": "8c983f4add0b69c32dc82bbf5c4f01cb17bf1c071c9dd901e7b992d229436847",
  "ledger_events": {
    "data_gap": 302,
    "out_of_session": 16,
    "phase_boundary": 4,
    "equity": 28617,
    "prediction": 25830
  },
  "strat2c_preserved_after": 47,
  "measurements": [
    {
      "file": "reference-parent-monitor.json",
      "peak_rss_bytes": 26443776,
      "minimum_available_ram_gib": 1.7557754516601562,
      "minimum_free_disk_gib": 29.032711029052734
    },
    {
      "file": "reference-result.json",
      "peak_rss_bytes": 94584832,
      "minimum_available_ram_gib": 1.7557754516601562,
      "minimum_free_disk_gib": 29.032745361328125
    },
    {
      "file": "repeat-resume-parent-monitor.json",
      "peak_rss_bytes": 26038272,
      "minimum_available_ram_gib": 1.7738418579101562,
      "minimum_free_disk_gib": 29.06078338623047
    },
    {
      "file": "repeat-resume-result.json",
      "peak_rss_bytes": 82046976,
      "minimum_available_ram_gib": 1.7736740112304688,
      "minimum_free_disk_gib": 29.06081771850586
    },
    {
      "file": "resume-parent-monitor.json",
      "peak_rss_bytes": 25534464,
      "minimum_available_ram_gib": 1.7905044555664062,
      "minimum_free_disk_gib": 29.060882568359375
    },
    {
      "file": "resume-result.json",
      "peak_rss_bytes": 90886144,
      "minimum_available_ram_gib": 1.7906074523925781,
      "minimum_free_disk_gib": 29.060928344726562
    },
    {
      "file": "simulation-crash-crash.json",
      "peak_rss_bytes": 93966336,
      "minimum_available_ram_gib": 1.7733078002929688,
      "minimum_free_disk_gib": 29.068359375
    },
    {
      "file": "simulation-crash-parent-monitor.json",
      "peak_rss_bytes": 24702976,
      "minimum_available_ram_gib": 1.7735633850097656,
      "minimum_free_disk_gib": 29.068355560302734
    },
    {
      "file": "training-crash-crash.json",
      "peak_rss_bytes": 91906048,
      "minimum_available_ram_gib": 1.462005615234375,
      "minimum_free_disk_gib": 29.079730987548828
    },
    {
      "file": "training-crash-parent-monitor.json",
      "peak_rss_bytes": 24702976,
      "minimum_available_ram_gib": 1.4560813903808594,
      "minimum_free_disk_gib": 29.079769134521484
    }
  ]
}
```

## Tests, failures and remaining blockers

Synthetic test/static-check results are recorded in the adjacent evidence test log and tests.xml (report supplement below). Any actual failure and last metrics appear above. No guard was weakened during execution; incomplete stages are not claimed as passing. New profile validation changes code fingerprints: prior runs remain preserved and are not silently migrated. No Phase 5/STRAT-2C data, existing reports or checkpoints are modified.

Historical calendar verification, missing data resolution and meaningful trade coverage remain blockers for performance research. This small two-month engineering run is not profitability evidence or authorization for live trading. Windows power-loss durability still depends on filesystem/hardware. No holdout access, annual backtest, broker/live connection, Docker/service startup, source change, commit or push is permitted.

Reproduction (requires unused v1 output paths; preserve existing evidence):

```powershell
.\.venv\Scripts\python.exe -B -m tools.strat2d_historical_130_validation
```

## Consolidated measured results and final assessment

**PASS: the authorized two-month offline engineering validation is complete.**

- Input: **58,683 sealed source rows**, all scanned in each of quality/train/calibration/test passes (234,732 pass-row visits, not distinct observations).
- Eligible fitted rows: **12,212 training**, **13,915 calibration**; **25,830 test predictions**, **25,446 labeled test evaluations**, **28,617 equity observations**.
- Checkpoint: **240 committed transactions, generation 239, complete=true, status=engineering_complete**. Training crash before generation 65 publication left generation 64; test crash after generation 220 publication retained generation 220. Both recovered. Orphan staging remains preserved. Repeated completed resume added no transactions.
- Replay: **721/721 identical committed file hashes** for resumed versus uninterrupted runs, including manifest and all receipts/states/events. The additional monitored reference also matches **721/721**. Canonical final state hash: `8c983f4add0b69c32dc82bbf5c4f01cb17bf1c071c9dd901e7b992d229436847`.
- Earlier pre-optimization historical baseline: **480/480 state/event files identical**. Manifest/receipt differences across code/resource profiles are expected provenance. No scientific parameters were changed to obtain these results.
- Portfolio: **zero fills and trades**, cash/equity **10,000**, realized/unrealized P&L and costs **0**, no open or pending position, no censoring. All ledger, frozen-model, sequence and accounting checks passed. The zero-fill run does not exercise historical trade execution or demonstrate profitability.
- Data quality: **301 internal gaps, 23,516 internal missing minutes**, **16 outside-session candles**. Full-window missing-minute hypotheses: open **378**, session break **50**, weekend **25,849** (includes the leading closure, unlike internal-only totals). The ledger records 302 gap events because it also includes that leading interval. No DST transition is observed in this January-February window. Hours/holidays remain unverified; classifications are not established historical facts. Per-interval exclusions are in `data-quality-intervals.jsonl` and final checkpoint blocks.
- Peak measured worker RSS: **90.20 MiB**, below 256 MiB. Minimum sampled system-available RAM across parent and workers: **1.456 GiB**, above 1.30 GiB. Minimum free disk: **29.01 GiB**, above 15 GiB. These are sampled/high-water measurements, not proof that every instantaneous OS fluctuation was captured.

| Process/run | Worker OS peak RSS MiB | Minimum parent/worker RAM GiB |
| --- | ---: | ---: |
| training-crash | 87.65 | 1.456 |
| simulation-crash | 89.61 | 1.773 |
| resume | 86.68 | 1.791 |
| repeat-resume | 78.25 | 1.774 |
| reference | 90.20 | 1.756 |
| monitor-reference | 89.91 | 1.740 |

The additional corrected external probe measured **89.91 MiB** worker peak, with **1.740 GiB** minimum parent-sampled RAM. The earlier pre-optimization historical peak was 145.43 MiB; background conditions differ, so this is not a controlled timing/system-headroom comparison. Numerical equivalence is exact.

### Profiling defect, correction and all failures

The first parent's subprocess handle referred to the Windows venv launcher (~3.46 MiB), not the worker. Those initial `worker_peak_rss_bytes` fields and printed launcher peaks must **not** be used as worker measurements. They remain preserved. The worker's own OS high-water RSS probes and fail-closed guard were correct throughout the crash/replay sequence. The first run's parent continuously monitored RAM/disk but did not independently enforce actual worker RSS; the worker enforced it between bounded operations.

The driver now publishes the real worker PID atomically, opens that exact process, monitors its RSS every 0.1 seconds, and terminates only its own worker/launcher on a guard exception. Handle/probe failure is fail-closed. An additional complete historical reference validated this correction with external and internal RSS measurements and exact replay hashes. No resource floor or cap was relaxed to fix the diagnostic defect.

Both intended hard exits returned 86. All ordinary children returned 0. **No actual RAM/disk/RSS guard failure, corruption, model/accounting failure or replay mismatch occurred.** Initial lint findings were corrected before final static checks. A read-only WMI process-parent diagnostic was denied by Windows; PID publication resolved the profiling issue without WMI, elevated access, process termination or system changes.

### Automated verification

**71 targeted tests passed, 89 known NumPy timedelta deprecation warnings, 36.83 seconds.** Tests include the existing model/leakage/calendar/recovery/accounting cases, memory regressions and 19 profile-specific cases. The new cases reject missing/unknown opt-in, broader scope, another model, weaker RSS/disk limits, preparation exceptions and altered thread/batch limits. Mocked RAM/RSS stops retain their measurements and fail closed. This was the targeted STRAT-2D suite, not a claim that every repository test was run.

```powershell
$env:PYTHONPATH = "$pwd\src"
..\AI-Trading-System\.venv-research\Scripts\python.exe -B -m pytest tests/test_strat2d.py tests/test_strat2d_memory.py tests/test_strat2d_resource_profile.py -q -p no:cacheprovider --basetemp=.tmp/strat2d-historical-130-tests-v1 --junitxml=.tmp/strat2d-historical-130-tests-v1.xml
```

Ruff check, Ruff format --check and `git diff --check` passed. Additional external-probe integration execution: `.\.venv\Scripts\python.exe -B -m tools.strat2d_historical_130_validation --monitor-check`. All v1 paths are exclusive and preserved; do not rerun the orchestrator against existing evidence as a reset. After an interrupted worker, use the unchanged config/code and the production runner to resume its committed journal; do not remove staging or receipts. Driver and production code fingerprints are retained in `final-assessment.json`.

Files changed for this task: `src/trading/research/strat2d/pipeline.py`, new `config/strat2d-eurusd-2022-offline-130-validation-v1.json`, new `tests/test_strat2d_resource_profile.py`, new `tools/strat2d_historical_130_validation.py`, and this report. Shared safety defaults, original research configs and all 47 published STRAT-2C hashes/mtimes remain unchanged. New outputs are exclusively under the three `strat2d-eurusd-2022-offline-130-*` directories and the separate evidence directory.

**Remaining blockers:** historical session/holiday evidence, data-quality resolution and meaningful trade coverage are still required before performance research. The approved 1.30 GiB policy is limited to this engineering case; measured headroom never reached 1.30 GiB, so reliability exactly at the floor is unproven. Broader windows/models retain the ordinary 1.5 GiB floor. Recommended next phase: STRAT-2E historical calendar verification and gap-safe simulation episodes with deliberate synthetic trade coverage, followed by separately authorized bounded development-period validation. No holdout, annual run or live activity is authorized.
