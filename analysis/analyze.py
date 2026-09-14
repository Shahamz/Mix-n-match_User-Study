#!/usr/bin/env python3
"""
Paired analysis of the Mix-n-match user study.

    python analysis/analyze.py --responses answers.csv
    python analysis/analyze.py --simulate 40            # synthetic data, exercises every test

Reads the responses alongside data/manifest.json (what each question was) and
data/legend.json (which code was which method), and writes analysis/out/.

The pre-registered primary family is stated in analysis/README.md. Everything
outside it is exploratory and reported under Benjamini-Hochberg, not Holm.
"""

from __future__ import annotations

import argparse
import csv
import json
import math
import random
import sys
from collections import Counter, defaultdict
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

import numpy as np

import stats as S

REFERENCE = "mix_n_match"
PRETTY = {
    "mix_n_match": "Mix-n-match (ours)",
    "mnm_baseline": "Naive baseline",
    "regional_prompting": "Regional Prompting",
    "tiled_diffusion": "Tiled Diffusion",
}
LEVELS = {"tile": "single region", "comp": "whole image"}


# =========================================================================== #
# loading
# =========================================================================== #

def load_submissions(path):
    """
    Accepts whatever the collection produced: the Apps Script `answers` sheet, the
    `responses` sheet with its raw_json column, a folder of downloaded payloads, or
    a single JSON file.
    """
    path = Path(path)
    if path.is_dir():
        payloads = []
        for file in sorted(path.glob("*.json")):
            payloads.extend(load_submissions(file))
        return payloads

    if path.suffix.lower() == ".json":
        data = json.loads(path.read_text())
        return data if isinstance(data, list) else [data]

    with path.open(newline="", encoding="utf-8-sig") as handle:
        rows = list(csv.DictReader(handle))
    if not rows:
        return []

    if "raw_json" in rows[0]:
        payloads = []
        for row in rows:
            if row.get("raw_json"):
                try:
                    payloads.append(json.loads(row["raw_json"]))
                except json.JSONDecodeError:
                    pass
        return payloads

    # Long form: rebuild one payload per participant.
    grouped = defaultdict(list)
    for row in rows:
        grouped[row.get("participant", "")].append(row)

    def number(value):
        try:
            return int(float(value))
        except (TypeError, ValueError):
            return None

    payloads = []
    for participant, answers in grouped.items():
        payloads.append({
            "participant": participant,
            "responses": [{
                "id": row.get("item_id"),
                "type": row.get("type"),
                "level": row.get("level"),
                "set": row.get("set"),
                "config": row.get("config"),
                "position": number(row.get("position")),
                "choice": number(row.get("choice")),
                "choice_position": number(row.get("choice_position")),
                "best": number(row.get("best")),
                "best_position": number(row.get("best_position")),
                "worst": number(row.get("worst")),
                "worst_position": number(row.get("worst_position")),
                "ms": number(row.get("ms")),
                "at": row.get("answered_at"),
            } for row in answers],
            "background": {},
        })
    return payloads


def tidy(payloads, manifest, legend):
    """One record per judgement, joined to what the question actually was."""
    items = {item["id"]: item for item in manifest["items"]}
    methods = legend["methods"]
    records, orphans = [], 0

    for payload in payloads:
        participant = payload.get("participant", "")
        background = payload.get("background") or {}
        for answer in payload.get("responses", []):
            item = items.get(answer.get("id"))
            if item is None:
                orphans += 1
                continue

            def method_at(index):
                if index is None or not (0 <= index < len(item["options"])):
                    return None
                return methods.get(item["options"][index]["m"])

            record = {
                "participant": participant,
                "item": item["id"],
                "type": item["type"],
                "level": item["level"],
                "set": item["set"],
                "config": item["config"],
                "num_crops": item.get("num_crops"),
                "tiles_per_crop": item.get("tiles_per_crop"),
                "crop_index": item.get("crop_index"),
                "tile_index": item.get("tile_index"),
                "position": answer.get("position"),
                "ms": answer.get("ms"),
                "familiarity": background.get("familiarity", ""),
                "device": background.get("device", ""),
                "methods": [methods.get(option["m"]) for option in item["options"]],
                "chosen": method_at(answer.get("choice")),
                "chosen_side": answer.get("choice_position"),
                "best": method_at(answer.get("best")),
                "worst": method_at(answer.get("worst")),
                "raw_choice": answer.get("choice"),
                "flags": item.get("flags", {}),
            }

            # The content a tile item is about, so the same content judged against
            # two different baselines can be paired.
            record["content"] = "|".join(str(part) for part in (
                item["config"], item["level"], item.get("crop_index"), item.get("tile_index"),
                tuple(item.get("combination", []))))

            if len(item["options"]) == 2 and REFERENCE in record["methods"]:
                opponents = [m for m in record["methods"] if m != REFERENCE]
                record["opponent"] = opponents[0] if opponents else None
                if record["chosen"] is not None:
                    record["reference_won"] = int(record["chosen"] == REFERENCE)
            records.append(record)

    return records, orphans


# =========================================================================== #
# quality control
# =========================================================================== #

def quality_control(records, legend, fast_ms=800, slow_ms=180_000):
    expected = legend.get("attention_expected", {})
    by_participant = defaultdict(list)
    for record in records:
        by_participant[record["participant"]].append(record)

    report = {}
    for participant, own in by_participant.items():
        checks = [r for r in own if r["type"] == "attention" and r["item"] in expected]
        passed = sum(1 for r in checks if r["raw_choice"] == expected[r["item"]])
        scored = [r for r in own if r["type"] != "attention"]
        times = [r["ms"] for r in scored if isinstance(r["ms"], (int, float))]
        sides = [r["chosen_side"] for r in scored if r["chosen_side"] is not None]

        side_run = max(Counter(sides).values()) / len(sides) if sides else 0.0
        report[participant] = {
            "answers": len(scored),
            "attention_checks": len(checks),
            "attention_passed": passed,
            "median_ms": float(np.median(times)) if times else float("nan"),
            "too_fast": sum(1 for t in times if t < fast_ms),
            "too_slow": sum(1 for t in times if t > slow_ms),
            "one_side_share": side_run,
        }
        reasons = []
        if checks and passed < len(checks):
            reasons.append("failed an attention check")
        if times and np.median(times) < fast_ms:
            reasons.append("median answer under %d ms" % fast_ms)
        if sides and side_run > 0.9 and len(sides) >= 10:
            reasons.append("picked the same side %.0f%% of the time" % (100 * side_run))
        report[participant]["exclude"] = bool(reasons)
        report[participant]["reasons"] = reasons

    return report


# =========================================================================== #
# analyses
# =========================================================================== #

def head_to_head(records):
    """Win rate of the reference method against each baseline, per question level."""
    buckets = defaultdict(lambda: [0, 0])
    for record in records:
        if "reference_won" not in record or not record.get("opponent"):
            continue
        key = (record["level"], record["opponent"])
        buckets[key][0] += record["reference_won"]
        buckets[key][1] += 1
    rows = []
    for (level, opponent), (wins, total) in sorted(buckets.items()):
        summary = S.proportion_summary(wins, total,
                                       "%s vs %s (%s)" % (PRETTY[REFERENCE], PRETTY[opponent],
                                                          LEVELS.get(level, level)))
        summary.update(level=level, opponent=opponent)
        odds, low, high = S.odds_ratio_ci(wins, total)
        summary.update(odds_ratio=odds, or_low=low, or_high=high,
                       cohens_g=S.cohens_g(summary["rate"]),
                       power=S.achieved_power(summary["rate"], total))
        rows.append(summary)
    return rows


def by_question(records):
    """The same win rates, broken out by the exact question that was asked."""
    buckets = defaultdict(lambda: [0, 0])
    for record in records:
        if "reference_won" not in record or not record.get("opponent"):
            continue
        buckets[(record["type"], record["opponent"])][0] += record["reference_won"]
        buckets[(record["type"], record["opponent"])][1] += 1
    return [dict(S.proportion_summary(w, n, "%s / %s" % (kind, PRETTY[opponent])),
                 question=kind, opponent=opponent)
            for (kind, opponent), (w, n) in sorted(buckets.items())]


def paired_mcnemar(records, unit_key):
    """
    Genuine paired contrasts: the same unit judged against two different baselines.
    Each unit contributes one binary per baseline (did the reference win the majority
    of its judgements), and the 2x2 of those binaries is McNemar's table.

    unit_key "content"     -> unit is the picture content, pooled over participants
    unit_key "participant" -> unit is the person, pooled over their own judgements
    """
    tally = defaultdict(lambda: [0, 0])
    for record in records:
        if "reference_won" not in record or not record.get("opponent"):
            continue
        key = (record[unit_key], record["opponent"])
        tally[key][0] += record["reference_won"]
        tally[key][1] += 1

    outcome = {key: (wins / total > 0.5) for key, (wins, total) in tally.items() if total}
    baselines = sorted({key[1] for key in outcome})
    results = []
    for i, first in enumerate(baselines):
        for second in baselines[i + 1:]:
            units = {key[0] for key in outcome if key[1] == first} & \
                    {key[0] for key in outcome if key[1] == second}
            b = sum(1 for u in units if outcome[(u, first)] and not outcome[(u, second)])
            c = sum(1 for u in units if not outcome[(u, first)] and outcome[(u, second)])
            both = sum(1 for u in units if outcome[(u, first)] and outcome[(u, second)])
            neither = len(units) - b - c - both
            result = S.mcnemar(b, c)
            result.update(unit=unit_key, n_units=len(units), both=both, neither=neither,
                          first=first, second=second,
                          label="wins over %s vs wins over %s, paired by %s"
                                % (PRETTY[first], PRETTY[second], unit_key))
            results.append(result)
    return results


def mcnemar_across_questions(records):
    """
    The same content, same baseline, judged for two different things: whether the
    whole image holds together, and whether it shows what was described.
    """
    tally = defaultdict(lambda: [0, 0])
    for record in records:
        if "reference_won" not in record or not record.get("opponent"):
            continue
        if record["type"] not in ("comp_ab_coherence", "comp_ab_adherence"):
            continue
        key = (record["config"], record["opponent"], record["type"])
        tally[key][0] += record["reference_won"]
        tally[key][1] += 1
    outcome = {key: (wins / total > 0.5) for key, (wins, total) in tally.items() if total}
    keys = {(config, opponent) for config, opponent, _ in outcome}
    b = sum(1 for k in keys
            if outcome.get(k + ("comp_ab_coherence",)) and not outcome.get(k + ("comp_ab_adherence",))
            and k + ("comp_ab_adherence",) in outcome)
    c = sum(1 for k in keys
            if not outcome.get(k + ("comp_ab_coherence",)) and outcome.get(k + ("comp_ab_adherence",))
            and k + ("comp_ab_coherence",) in outcome)
    result = S.mcnemar(b, c)
    result.update(label="whole-image wins on coherence vs on prompt match, paired by config",
                  unit="config", n_units=len(keys))
    return result


def pairwise_comparisons(records):
    """Every judgement flattened into (winner, loser) pairs, four-way items included."""
    pairs = []
    for record in records:
        if record["type"] == "attention":
            continue
        if record.get("chosen") and len(record["methods"]) == 2:
            loser = [m for m in record["methods"] if m != record["chosen"]]
            if loser:
                pairs.append((record["chosen"], loser[0]))
        elif record.get("best") and record.get("worst"):
            middle = [m for m in record["methods"]
                      if m not in (record["best"], record["worst"])]
            for other in record["methods"]:
                if other != record["best"]:
                    pairs.append((record["best"], other))
            for other in middle:
                pairs.append((other, record["worst"]))
    return pairs


def best_worst_scores(records):
    """Counting score: (times best - times worst) / times shown."""
    shown, best, worst = Counter(), Counter(), Counter()
    for record in records:
        if not record.get("best") or not record.get("worst"):
            continue
        for method in record["methods"]:
            shown[method] += 1
        best[record["best"]] += 1
        worst[record["worst"]] += 1
    return [{"method": method, "shown": shown[method], "best": best[method],
             "worst": worst[method],
             "score": (best[method] - worst[method]) / shown[method] if shown[method] else float("nan")}
            for method in sorted(shown, key=lambda m: -(best[m] - worst[m]) / max(1, shown[m]))]


def side_bias(records):
    first, total = 0, 0
    for record in records:
        if record["chosen_side"] is None or len(record["methods"]) != 2:
            continue
        total += 1
        first += int(record["chosen_side"] == 0)
    return S.proportion_summary(first, total, "first-shown picture chosen")


def order_effect(records):
    """Does the reference's advantage drift as a session wears on?"""
    scored = [r for r in records if "reference_won" in r and r["position"] is not None]
    if not scored:
        return []
    cut = max(r["position"] for r in scored) + 1
    thirds = []
    for index, (low, high) in enumerate([(0, cut / 3), (cut / 3, 2 * cut / 3), (2 * cut / 3, cut)]):
        part = [r for r in scored if low <= r["position"] < high]
        wins = sum(r["reference_won"] for r in part)
        thirds.append(dict(S.proportion_summary(wins, len(part),
                                                "questions %d-%d" % (int(low) + 1, int(high))),
                           third=index + 1))
    return thirds


def moderators(records):
    """Exploratory breakdowns. Reported under Benjamini-Hochberg."""
    out = []
    def bucket(name, keyfn):
        tally = defaultdict(lambda: [0, 0])
        for record in records:
            if "reference_won" not in record:
                continue
            key = keyfn(record)
            if key is None or key == "":
                continue
            tally[key][0] += record["reference_won"]
            tally[key][1] += 1
        for key, (wins, total) in sorted(tally.items(), key=lambda kv: str(kv[0])):
            out.append(dict(S.proportion_summary(wins, total, "%s = %s" % (name, key)),
                            moderator=name, value=str(key)))

    bucket("cropping set", lambda r: r["set"])
    bucket("regions in the image", lambda r: r["num_crops"])
    bucket("options per region", lambda r: r["tiles_per_crop"])
    bucket("familiarity", lambda r: r["familiarity"])
    bucket("device", lambda r: r["device"])
    bucket("baseline x set", lambda r: "%s / %s" % (PRETTY.get(r.get("opponent"), "?"), r["set"]))
    return out


def agreement(records):
    """How much raters agree, on the items several people saw."""
    by_item = defaultdict(list)
    for record in records:
        if record["type"] == "attention":
            continue
        if record.get("chosen"):
            by_item[record["item"]].append(record["chosen"])
        elif record.get("best"):
            by_item[record["item"]].append(record["best"])

    units = [values for values in by_item.values() if len(values) >= 2]
    categories = sorted({value for values in units for value in values})
    counts = [[values.count(category) for category in categories] for values in units]
    majority = [max(Counter(values).values()) / len(values) for values in units]
    return {
        "items_with_two_or_more_raters": len(units),
        "krippendorff_alpha": S.krippendorff_alpha(units),
        "fleiss_kappa": S.fleiss_kappa(counts) if counts else float("nan"),
        "mean_majority_share": float(np.mean(majority)) if majority else float("nan"),
    }


def regression(records):
    """
    A GEE logistic model clustered by participant, so repeated judgements by the
    same person are not treated as independent. statsmodels has no frequentist
    binomial GLMM, which is why this is GEE and not a mixed model.
    """
    try:
        import pandas as pd
        import statsmodels.api as sm
        import statsmodels.formula.api as smf
    except ImportError:
        return {"available": False,
                "note": "install pandas and statsmodels to fit the clustered model "
                        "(pip install -r analysis/requirements.txt)"}

    rows = [r for r in records if "reference_won" in r and r.get("opponent")]
    if len(rows) < 40:
        return {"available": False, "note": "too few paired judgements to fit a model"}

    frame = pd.DataFrame([{
        "won": r["reference_won"],
        "baseline": r["opponent"],
        "level": r["level"],
        "cropping": r["set"],
        "regions": r["num_crops"] or 0,
        "options": r["tiles_per_crop"] or 0,
        "side": r["chosen_side"] if r["chosen_side"] is not None else 0,
        "position": r["position"] or 0,
        "participant": r["participant"],
    } for r in rows])

    formula = "won ~ C(baseline) + C(level) + C(cropping) + regions + options + position"
    try:
        model = smf.gee(formula, groups="participant", data=frame,
                        family=sm.families.Binomial(),
                        cov_struct=sm.cov_struct.Exchangeable()).fit()
    except Exception as error:                                # noqa: BLE001 - report, don't crash
        return {"available": False, "note": "model did not converge: %s" % error}

    return {
        "available": True,
        "formula": formula,
        "n": int(len(frame)),
        "participants": int(frame["participant"].nunique()),
        "terms": [{"term": term,
                   "coef": float(model.params[term]),
                   "odds_ratio": float(math.exp(model.params[term])),
                   "p_value": float(model.pvalues[term])}
                  for term in model.params.index],
    }


# =========================================================================== #
# figures
# =========================================================================== #

# Validated categorical slots 1-3 (blue, orange, aqua): the only three-hue set that
# clears the all-pairs colour-vision floors. One hue per baseline, fixed order.
SERIES = {"mnm_baseline": "#2a78d6", "regional_prompting": "#eb6834",
          "tiled_diffusion": "#1baf7a", "mix_n_match": "#52514e"}
SURFACE, INK, MUTED, GRID = "#fcfcfb", "#0b0b0b", "#52514e", "#e3e2de"


def figures(head_rows, bt_scores, bt_ci, condition_rows, out):
    try:
        import matplotlib
        matplotlib.use("Agg")
        import matplotlib.pyplot as plt
    except ImportError:
        return ["matplotlib not installed; figures skipped"]

    plt.rcParams.update({
        "font.size": 9, "axes.edgecolor": GRID, "axes.labelcolor": MUTED,
        "xtick.color": MUTED, "ytick.color": MUTED, "text.color": INK,
        "figure.facecolor": SURFACE, "axes.facecolor": SURFACE, "savefig.facecolor": SURFACE,
    })
    written = []

    def dot_plot(rows, labels, centres, lows, highs, colours, title, xlabel, reference, path,
                 xlim=None):
        height = max(2.0, 0.42 * len(rows) + 1.4)
        figure, axes = plt.subplots(figsize=(7.2, height))
        y = np.arange(len(rows))[::-1]
        axes.axvline(reference, color=MUTED, lw=1, ls=(0, (4, 3)), zorder=1)
        for index in range(len(rows)):
            axes.plot([lows[index], highs[index]], [y[index], y[index]],
                      color=colours[index], lw=2, solid_capstyle="round", zorder=2)
            axes.plot([centres[index]], [y[index]], "o", ms=8, color=colours[index],
                      markeredgecolor=SURFACE, markeredgewidth=2, zorder=3)
            axes.annotate("%.2f" % centres[index], (centres[index], y[index]),
                          textcoords="offset points", xytext=(0, 9), ha="center",
                          fontsize=8, color=INK)
        axes.set_yticks(y, labels)
        axes.set_xlabel(xlabel)
        axes.set_title(title, loc="left", color=INK, fontsize=11, pad=12)
        if xlim:
            axes.set_xlim(*xlim)
        axes.grid(axis="x", color=GRID, lw=0.8)
        axes.set_axisbelow(True)
        for side in ("top", "right", "left"):
            axes.spines[side].set_visible(False)
        figure.tight_layout()
        figure.savefig(path, dpi=200)
        plt.close(figure)
        written.append(str(path))

    if head_rows:
        dot_plot(head_rows,
                 ["%s\n%s" % (PRETTY[r["opponent"]], LEVELS.get(r["level"], r["level"]))
                  for r in head_rows],
                 [r["rate"] for r in head_rows],
                 [r["ci_low"] for r in head_rows], [r["ci_high"] for r in head_rows],
                 [SERIES[r["opponent"]] for r in head_rows],
                 "How often Mix-n-match was preferred", "share of judgements (0.5 = no preference)",
                 0.5, out / "win_rates.png", xlim=(0, 1))

    if bt_scores:
        ordered = sorted(bt_scores.items(), key=lambda kv: kv[1])
        dot_plot(ordered, [PRETTY[name] for name, _ in ordered],
                 [value for _, value in ordered],
                 [bt_ci.get(name, (v, v))[0] for name, v in ordered],
                 [bt_ci.get(name, (v, v))[1] for name, v in ordered],
                 [SERIES[name] for name, _ in ordered],
                 "Overall ranking (Bradley-Terry)", "log strength (0 = average method)",
                 0.0, out / "ranking.png")

    if condition_rows:
        dot_plot(condition_rows, [r["label"] for r in condition_rows],
                 [r["rate"] for r in condition_rows],
                 [r["ci_low"] for r in condition_rows], [r["ci_high"] for r in condition_rows],
                 [SERIES.get(r.get("opponent"), MUTED) for r in condition_rows],
                 "Preference by condition", "share of judgements (0.5 = no preference)",
                 0.5, out / "by_condition.png", xlim=(0, 1))

    return written


# =========================================================================== #
# simulation
# =========================================================================== #

def simulate(manifest, legend, participants, seed=11):
    """
    Synthetic responses with a known answer, so every test can be exercised before
    a single real participant arrives. The reference method is given a genuine but
    modest edge, plus a small bias toward whichever picture is shown first.
    """
    rng = random.Random(seed)
    methods = legend["methods"]
    truth = {"mnm_baseline": 0.72, "regional_prompting": 0.60, "tiled_diffusion": 0.64}
    side_bias_strength = 0.04
    expected = legend.get("attention_expected", {})
    session = manifest.get("session", {})
    per_person = min(session.get("items_per_participant", 30), len(manifest["items"]))

    payloads = []
    for person in range(participants):
        pid = "sim%03d" % person
        careless = rng.random() < 0.12
        drawn = rng.sample(manifest["items"], per_person)
        responses = []
        for position, item in enumerate(drawn):
            names = [methods[option["m"]] for option in item["options"]]
            order = list(range(len(names)))
            rng.shuffle(order)
            answer = {"id": item["id"], "type": item["type"], "level": item["level"],
                      "set": item["set"], "config": item["config"], "position": position,
                      "shown_order": order,
                      "ms": rng.randint(300, 900) if careless else rng.randint(1500, 9000)}

            if item["type"] == "attention":
                want = expected.get(item["id"], 0)
                answer["choice"] = want if not careless else rng.randrange(len(names))
            elif len(names) == 2 and REFERENCE in names:
                opponent = next(n for n in names if n != REFERENCE)
                probability = 0.5 if careless else truth.get(opponent, 0.6)
                reference_index = names.index(REFERENCE)
                if order.index(reference_index) == 0:
                    probability = min(0.98, probability + side_bias_strength)
                answer["choice"] = reference_index if rng.random() < probability \
                    else names.index(opponent)
            elif len(names) == 4:
                weights = [3.0 if n == REFERENCE else truth.get(n, 0.5) for n in names]
                answer["best"] = rng.choices(range(len(names)), weights=weights)[0]
                rest = [i for i in range(len(names)) if i != answer["best"]]
                answer["worst"] = rng.choices(
                    rest, weights=[1.0 / max(0.05, weights[i]) for i in rest])[0]
            else:
                answer["choice"] = rng.randrange(len(names))

            if "choice" in answer:
                answer["choice_position"] = order.index(answer["choice"])
            if "best" in answer:
                answer["best_position"] = order.index(answer["best"])
                answer["worst_position"] = order.index(answer["worst"])
            responses.append(answer)

        payloads.append({
            "participant": pid, "study": "mix-n-match-simulated",
            "responses": responses,
            "background": {"familiarity": rng.choice(["never", "tried", "regular", "professional"]),
                           "device": rng.choice(["phone", "laptop", "monitor"])},
        })
    return payloads


# =========================================================================== #
# reporting
# =========================================================================== #

def fmt_p(value):
    if value != value:
        return "n/a"
    return "<0.001" if value < 0.001 else "%.3f" % value


def row_line(row, extra=""):
    return "| %s | %d/%d | %.3f | %.3f-%.3f | %s |%s" % (
        row["label"], row["wins"], row["n"], row["rate"], row["ci_low"], row["ci_high"],
        fmt_p(row["p_value"]), extra)


def write_report(out, blocks):
    (out / "summary.md").write_text("\n".join(blocks))


def write_csv(out, head_rows, question_rows, moderator_rows):
    with (out / "results.csv").open("w", newline="", encoding="utf-8") as handle:
        writer = csv.writer(handle)
        writer.writerow(["family", "label", "level", "opponent", "wins", "n", "rate",
                         "ci_low", "ci_high", "p_value", "p_adjusted"])
        for family, rows in (("primary", head_rows), ("by_question", question_rows),
                             ("moderator", moderator_rows)):
            for row in rows:
                writer.writerow([family, row["label"], row.get("level", ""),
                                 row.get("opponent", ""), row["wins"], row["n"],
                                 "%.4f" % row["rate"], "%.4f" % row["ci_low"],
                                 "%.4f" % row["ci_high"], "%.5f" % row["p_value"],
                                 "%.5f" % row.get("p_adjusted", float("nan"))])


def main():
    parser = argparse.ArgumentParser(description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--responses", type=Path,
                        help="CSV export, JSON payload, or a folder of JSON payloads")
    parser.add_argument("--simulate", type=int, metavar="N",
                        help="generate N synthetic participants instead")
    parser.add_argument("--manifest", type=Path, default=Path("data/manifest.json"))
    parser.add_argument("--legend", type=Path, default=Path("analysis/legend.json"))
    parser.add_argument("--out", type=Path, default=Path("analysis/out"))
    parser.add_argument("--keep-excluded", action="store_true",
                        help="run the primary tests on everyone, ignoring quality control")
    parser.add_argument("--bootstrap", type=int, default=1000,
                        help="bootstrap draws for the ranking intervals (0 to skip)")
    args = parser.parse_args()

    if not args.responses and not args.simulate:
        parser.error("pass --responses or --simulate")

    manifest = json.loads(args.manifest.read_text())
    legend = json.loads(args.legend.read_text())
    payloads = simulate(manifest, legend, args.simulate) if args.simulate \
        else load_submissions(args.responses)
    if not payloads:
        print("No submissions found.")
        return 1

    records, orphans = tidy(payloads, manifest, legend)
    checks = quality_control(records, legend)
    excluded = {p for p, info in checks.items() if info["exclude"]}
    kept = records if args.keep_excluded else \
        [r for r in records if r["participant"] not in excluded]
    scored = [r for r in kept if r["type"] != "attention"]

    args.out.mkdir(parents=True, exist_ok=True)
    lines = []
    add = lines.append

    add("# Mix-n-match user study results\n")
    add("Source: %s. Item bank built %s.\n"
        % ("simulated data" if args.simulate else str(args.responses),
           manifest.get("built_at", "?")))

    # -- participants ------------------------------------------------------ #
    add("## Who is in the analysis\n")
    add("| | count |")
    add("|---|---|")
    add("| Submissions received | %d |" % len(payloads))
    add("| Excluded by quality control | %d |" % len(excluded))
    add("| Kept | %d |" % (len(checks) - len(excluded)))
    add("| Judgements kept | %d |" % len(scored))
    if orphans:
        add("| Answers to questions not in this bank (ignored) | %d |" % orphans)
    add("")
    if excluded:
        add("Excluded participants and why:\n")
        for participant in sorted(excluded):
            add("- `%s`: %s" % (participant, "; ".join(checks[participant]["reasons"])))
        add("")

    # -- primary family ---------------------------------------------------- #
    head_rows = head_to_head(scored)
    adjusted = S.holm([row["p_value"] for row in head_rows]) if head_rows else []
    for row, value in zip(head_rows, adjusted):
        row["p_adjusted"] = value

    add("## Primary: how often Mix-n-match was preferred\n")
    add("Pre-registered family of %d tests, Holm-adjusted. An exact two-sided binomial "
        "test against 0.5, with Wilson intervals.\n" % len(head_rows))
    add("| comparison | wins | rate | 95% CI | p | Holm p | odds ratio | power |")
    add("|---|---|---|---|---|---|---|---|")
    for row in head_rows:
        add("| %s | %d/%d | %.3f | %.3f-%.3f | %s | %s | %.2f | %.2f |"
            % (row["label"], row["wins"], row["n"], row["rate"], row["ci_low"], row["ci_high"],
               fmt_p(row["p_value"]), fmt_p(row["p_adjusted"]), row["odds_ratio"], row["power"]))
    add("")

    # -- by question ------------------------------------------------------- #
    question_rows = by_question(scored)
    for row, value in zip(question_rows,
                          S.benjamini_hochberg([r["p_value"] for r in question_rows])):
        row["p_adjusted"] = value
    add("## By the exact question asked\n")
    add("| question / baseline | wins | rate | 95% CI | p | BH q |")
    add("|---|---|---|---|---|---|")
    for row in question_rows:
        add("| %s | %d/%d | %.3f | %.3f-%.3f | %s | %s |"
            % (row["label"], row["wins"], row["n"], row["rate"], row["ci_low"],
               row["ci_high"], fmt_p(row["p_value"]), fmt_p(row["p_adjusted"])))
    add("")

    # -- McNemar ----------------------------------------------------------- #
    add("## Paired contrasts (McNemar)\n")
    add("Does the advantage over one baseline differ from the advantage over another? "
        "Each unit contributes one binary per baseline, and the disagreements between "
        "those binaries are the test.\n")
    add("| contrast | units | b | c | discordant | statistic | p | method |")
    add("|---|---|---|---|---|---|---|---|")
    mcnemar_rows = paired_mcnemar(scored, "content") + paired_mcnemar(scored, "participant")
    across = mcnemar_across_questions(scored)
    if across["n_discordant"] or across["n_units"]:
        mcnemar_rows.append(across)
    for row in mcnemar_rows:
        add("| %s | %d | %d | %d | %d | %s | %s | %s |"
            % (row["label"], row.get("n_units", 0), row["b"], row["c"], row["n_discordant"],
               "%.3f" % row["statistic"] if row["statistic"] == row["statistic"] else "n/a",
               fmt_p(row["p_value"]), row["method"]))
    add("")

    # -- ranking ----------------------------------------------------------- #
    pairs = pairwise_comparisons(scored)
    methods_present = sorted({m for pair in pairs for m in pair})
    bt_scores = S.bradley_terry(Counter(pairs), methods_present) if pairs else {}
    bt_ci = S.bradley_terry_bootstrap(pairs, methods_present, draws=args.bootstrap) \
        if pairs and args.bootstrap else {}

    add("## Overall ranking\n")
    add("Bradley-Terry over every pairwise judgement, with four-way answers expanded "
        "into the pairs they imply. Higher is better; 0 is the average method.\n")
    add("| method | log strength | 95% CI |")
    add("|---|---|---|")
    for method, value in sorted(bt_scores.items(), key=lambda kv: -kv[1]):
        low, high = bt_ci.get(method, (float("nan"), float("nan")))
        add("| %s | %+.3f | %s |" % (PRETTY[method], value,
                                     "%+.3f to %+.3f" % (low, high) if low == low else "n/a"))
    add("\n%d pairwise judgements in total.\n" % len(pairs))

    bws = best_worst_scores(scored)
    if bws:
        add("### Best-worst counting scores\n")
        add("| method | shown | best | worst | score |")
        add("|---|---|---|---|---|")
        for row in bws:
            add("| %s | %d | %d | %d | %+.3f |" % (PRETTY[row["method"]], row["shown"],
                                                   row["best"], row["worst"], row["score"]))
        add("")

    # -- bias -------------------------------------------------------------- #
    add("## Bias checks\n")
    side = side_bias(scored)
    add("**Side bias.** The picture shown first was chosen %d/%d times (%.3f, "
        "95%% CI %.3f-%.3f, p %s). Sides are randomised per participant, so a rate "
        "away from 0.5 is a display effect, not a result.\n"
        % (side["wins"], side["n"], side["rate"], side["ci_low"], side["ci_high"],
           fmt_p(side["p_value"])))
    thirds = order_effect(scored)
    if thirds:
        add("**Fatigue.** Mix-n-match win rate through the session:\n")
        add("| part of session | wins | rate | 95% CI |")
        add("|---|---|---|---|")
        for row in thirds:
            add("| %s | %d/%d | %.3f | %.3f-%.3f |" % (row["label"], row["wins"], row["n"],
                                                       row["rate"], row["ci_low"], row["ci_high"]))
        add("")

    # -- agreement --------------------------------------------------------- #
    agree = agreement(scored)
    add("## Agreement between raters\n")
    add("| | value |")
    add("|---|---|")
    add("| Questions answered by two or more people | %d |"
        % agree["items_with_two_or_more_raters"])
    add("| Krippendorff's alpha (nominal) | %.3f |" % agree["krippendorff_alpha"])
    add("| Fleiss' kappa | %.3f |" % agree["fleiss_kappa"])
    add("| Mean majority share | %.3f |" % agree["mean_majority_share"])
    add("\nLow agreement is not a failure here: it means the methods are close enough "
        "that people genuinely differ, which is itself a result.\n")

    # -- moderators -------------------------------------------------------- #
    moderator_rows = moderators(scored)
    for row, value in zip(moderator_rows,
                          S.benjamini_hochberg([r["p_value"] for r in moderator_rows])):
        row["p_adjusted"] = value
    add("## Exploratory breakdowns\n")
    add("Not pre-registered. Benjamini-Hochberg across this family.\n")
    add("| subgroup | wins | rate | 95% CI | p | BH q |")
    add("|---|---|---|---|---|---|")
    for row in moderator_rows:
        add("| %s | %d/%d | %.3f | %.3f-%.3f | %s | %s |"
            % (row["label"], row["wins"], row["n"], row["rate"], row["ci_low"],
               row["ci_high"], fmt_p(row["p_value"]), fmt_p(row["p_adjusted"])))
    add("")

    # -- model ------------------------------------------------------------- #
    model = regression(scored)
    add("## Clustered logistic model\n")
    if not model["available"]:
        add("Not run: %s\n" % model["note"])
    else:
        add("GEE, exchangeable working correlation, clustered by participant "
            "(%d judgements from %d people). Outcome: Mix-n-match won.\n"
            % (model["n"], model["participants"]))
        add("`%s`\n" % model["formula"])
        add("| term | coefficient | odds ratio | p |")
        add("|---|---|---|---|")
        for term in model["terms"]:
            add("| `%s` | %+.3f | %.3f | %s |" % (term["term"], term["coef"],
                                                  term["odds_ratio"], fmt_p(term["p_value"])))
        add("")

    # -- power ------------------------------------------------------------- #
    add("## How many more judgements would help\n")
    add("| true win rate | judgements needed per comparison (80% power, alpha 0.05) |")
    add("|---|---|")
    for rate in (0.55, 0.58, 0.60, 0.65, 0.70):
        add("| %.2f | %d |" % (rate, S.required_n(rate)))
    add("")

    # Write the results before drawing anything: a plotting problem must never
    # cost the analysis that produced it.
    write_report(args.out, lines)
    write_csv(args.out, head_rows, question_rows, moderator_rows)
    (args.out / "quality_control.json").write_text(json.dumps(checks, indent=2))

    condition_rows = [r for r in moderator_rows if r.get("moderator") == "baseline x set"]
    try:
        made = figures(head_rows, bt_scores, bt_ci, condition_rows, args.out)
    except Exception as error:                                # noqa: BLE001 - report, don't crash
        made = ["figures failed (%s); summary.md and results.csv are still complete" % error]

    print("participants kept: %d of %d   judgements: %d"
          % (len(checks) - len(excluded), len(checks), len(scored)))
    for row in head_rows:
        print("  %-52s %.3f  (n=%d, Holm p %s)"
              % (row["label"], row["rate"], row["n"], fmt_p(row["p_adjusted"])))
    print("\nwrote %s/summary.md, results.csv, quality_control.json" % args.out)
    for path in made:
        print("      %s" % path)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
