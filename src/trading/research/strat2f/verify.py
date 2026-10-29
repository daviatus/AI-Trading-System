"""Independent streamed verification of published metrics and labels (stdlib only)."""

import json
import math
from datetime import datetime


def verify_events(output, state, guard):
    cm = {k: [[0] * 3 for _ in range(3)] for k in state["metrics"]["test_2022-02"]}
    loss = dict.fromkeys(cm, 0.0)
    brier = dict.fromkeys(cm, 0.0)
    n, previous, sequence = 0, None, 0
    threshold_counts = {t: 0 for t in state["thresholds"]}
    for generation in range(10000):
        path = output / f"{generation:08d}/events.jsonl"
        if not path.exists():
            break
        with path.open() as handle:
            for line in handle:
                if n % 128 == 0:
                    guard()
                e = json.loads(line)
                decision = datetime.fromisoformat(e["time"])
                opened = datetime.fromisoformat(e["opened_at"])
                end = datetime.fromisoformat(e["label_end"])
                if (
                    (decision - opened).total_seconds() != 60
                    or decision < datetime.fromisoformat("2022-02-01T01:00:00+00:00")
                    or end < decision
                    or end >= datetime.fromisoformat("2022-03-01T00:00:00+00:00")
                ):
                    raise ValueError("Independent label alignment/purge failure")
                if previous is not None and decision <= previous:
                    raise ValueError("Independent event chronology failure")
                previous = decision
                sequence += 1
                if e["sequence"] != sequence:
                    raise ValueError("Duplicated or skipped diagnostic event")
                y = e["label"]
                for name, p in e["probabilities"].items():
                    if (
                        len(p) != 3
                        or not all(math.isfinite(x) and 0 <= x <= 1 for x in p)
                        or abs(sum(p) - 1) > 1e-8
                    ):
                        raise ValueError("Independent probability normalization failure")
                    chosen = p.index(max(p))
                    cm[name][y][chosen] += 1
                    loss[name] += -math.log(p[y] if p[y] > 1e-12 else 1e-12)
                    brier[name] += sum((float(i == y) - p[i]) ** 2 for i in range(3))
                p = e["probabilities"]["softmax_cal"]
                for threshold in threshold_counts:
                    threshold_counts[threshold] += int(
                        e["allowed"] and max(p[1:]) >= max(p[0], float(threshold))
                    )
                n += 1
    for name, m in state["metrics"]["test_2022-02"].items():
        if (
            m["n"] != n
            or cm[name] != m["cm"]
            or abs(loss[name] - m["loss"]) > 1e-8
            or abs(brier[name] - m["brier"]) > 1e-8
        ):
            raise ValueError("Independent metric totals differ")
    if any(state["thresholds"][t]["n"] != count for t, count in threshold_counts.items()):
        raise ValueError("Independent threshold totals differ")
    return {
        "status": "PASS",
        "verified_rows": n,
        "confusion_matrices": cm,
        "threshold_counts": threshold_counts,
        "log_loss_sums": loss,
        "brier_sums": brier,
    }
