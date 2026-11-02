# pass^k for tau-bench runs that are saved one trial per folder
#   results_dir/trial_1/*.json, results_dir/trial_2/*.json, ...
# A task's row with an API error counts as a fail (same as tau-bench),
# unless a later rerun of that task in the same trial succeeded without error.
import glob
import json
import os
import random
from math import comb


def is_error(row):
    return "error" in (row.get("info") or {})


def load_trial(folder):
    """Best row per task in one trial folder: a row without error beats a row with error."""
    best = {}
    for f in sorted(glob.glob(os.path.join(folder, "*.json")), key=os.path.getmtime):
        try:
            rows = json.load(open(f))
        except json.JSONDecodeError:      # file cut off mid-write by a disconnect
            continue
        for r in rows:
            t = r["task_id"]
            if t not in best or (is_error(best[t]) and not is_error(r)):
                best[t] = r
    return best


def done_tasks(folder, retry_errors=True):
    """Task ids already finished in this trial."""
    best = load_trial(folder)
    return {t for t, r in best.items() if not (retry_errors and is_error(r))}


def table(results_dir, n_tasks=50):
    """{trial: {task_id: row}} for every trial folder found."""
    out = {}
    for d in sorted(glob.glob(os.path.join(results_dir, "trial_*"))):
        out[int(d.rsplit("_", 1)[1])] = load_trial(d)
    return out


def success_counts(tab, tasks):
    """c[task] = number of trials where the task passed."""
    return {t: sum(1 for tr in tab.values() if t in tr and tr[t]["reward"] >= 1 - 1e-6) for t in tasks}


def pass_hat_k(counts, n, k):
    """tau-bench's unbiased pass^k: mean over tasks of C(c, k) / C(n, k)."""
    return sum(comb(c, k) / comb(n, k) for c in counts.values()) / len(counts)


def curve(results_dir, n_tasks=50, boot=2000, seed=0):
    """pass^k for k = 1..n with a 95% bootstrap CI over tasks.
    Uses only trials that have all n_tasks tasks."""
    tab = table(results_dir, n_tasks)
    full = {k: v for k, v in tab.items() if len(v) == n_tasks}
    n = len(full)
    if n == 0:
        return n, []
    tasks = list(range(n_tasks))
    counts = success_counts(full, tasks)
    rng = random.Random(seed)
    rows = []
    for k in range(1, n + 1):
        est = pass_hat_k(counts, n, k)
        samples = []
        for _ in range(boot):
            pick = [counts[rng.choice(tasks)] for _ in tasks]
            samples.append(sum(comb(c, k) / comb(n, k) for c in pick) / len(pick))
        samples.sort()
        rows.append({"k": k, "pass^k": est,
                     "ci_low": samples[int(0.025 * boot)], "ci_high": samples[int(0.975 * boot) - 1]})
    return n, rows


def errors(results_dir):
    """(trial, task_id, error text) for every task whose best row is still an API error."""
    out = []
    for trial, rows in table(results_dir).items():
        for t, r in sorted(rows.items()):
            if is_error(r):
                out.append((trial, t, r["info"]["error"][:150]))
    return out
