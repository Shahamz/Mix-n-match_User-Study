#!/usr/bin/env python3
"""
Paired analysis of the Mix-n-match user study.

    python analysis/analyze.py --responses answers.csv
    python analysis/analyze.py --simulate 40            # synthetic data, exercises every test

Every question in the study is the same: our composite against one baseline's, judged
on three criteria (overall quality, seamlessness, prompt alignment) with A / Tie / B.
Because all three criteria are answered on the *same* pair of images by the same
person, the criteria are exactly paired, which is what the McNemar section uses.

Reads the responses alongside data/manifest.json (what each question was) and
analysis/legend.json (which code was which method), and writes analysis/out/.

The pre-registered primary family is stated in analysis/README.md.
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
CRITERION_LABEL = {"overall": "Overall quality", "seamless": "Seamlessness",
                   "alignment": "Prompt alignment"}


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

    def number(value):
        try:
            return int(float(value))
        except (TypeError, ValueError):
            return None

    grouped = defaultdict(list)
    for row in rows:
        grouped[row.get("participant", "")].append(row)

    payloads = []
    for participant, rows_for in grouped.items():
        payloads.append({
            "participant": participant,
            "responses": [{
                "id": row.get("item_id"),
                "set": row.get("set"),
                "config": row.get("config"),
                "seed": number(row.get("seed")),
                "num_crops": number(row.get("num_crops")),
                "tiles_per_crop": number(row.get("tiles_per_crop")),
                "a_method": row.get("a_method"),
                "b_method": row.get("b_method"),
                "answers": {key: row.get(key, "") for key in CRITERION_LABEL},
                "winners": {key: row.get("win_" + key, "") for key in CRITERION_LABEL},
                "position": number(row.get("position")),
                "ms": number(row.get("ms")),
                "at": row.get("answered_at"),
            } for row in rows_for],
            "background": {},
        })
    return payloads


def tidy(payloads, manifest, legend):
    """
    One record per (answer x criterion). `outcome` is from our side: win, loss or tie.
    `response` groups the three criteria that were answered on one pair of images.
    """
    items = {item["id"]: item for item in manifest["items"]}
    methods = legend["methods"]
    criteria = [c["id"] for c in manifest.get("criteria", [])] or list(CRITERION_LABEL)
    records, orphans = [], 0

    for payload in payloads:
        participant = payload.get("participant", "")
        background = payload.get("background") or {}
        for index, answer in enumerate(payload.get("responses", [])):
            item = items.get(answer.get("id"))
            if item is None:
                orphans += 1
                continue

            code_a = answer.get("a_method")
            code_b = answer.get("b_method")
            if not code_a or not code_b:
                order = [answer.get("a"), answer.get("b")]
                if None in order:
                    continue
                code_a = item["options"][order[0]]["m"]
                code_b = item["options"][order[1]]["m"]

            method_a, method_b = methods.get(code_a), methods.get(code_b)
            if REFERENCE not in (method_a, method_b):
                continue
            opponent = method_b if method_a == REFERENCE else method_a
            reference_side = 0 if method_a == REFERENCE else 1

            picks = answer.get("answers") or {}
            for criterion in criteria:
                pick = (picks.get(criterion) or "").strip().lower()
                if pick in ("a", "b"):
                    chose = method_a if pick == "a" else method_b
                    outcome = "win" if chose == REFERENCE else "loss"
                elif pick == "tie":
                    outcome = "tie"
                else:
                    continue
                records.append({
                    "participant": participant,
                    "response": participant + "|" + str(answer.get("id")) + "|" + str(index),
                    "item": item["id"],
                    "config": item["config"],
                    "seed": item.get("seed"),
                    "set": item["set"],
                    "num_crops": item.get("num_crops"),
                    "tiles_per_crop": item.get("tiles_per_crop"),
                    "criterion": criterion,
                    "opponent": opponent,
                    "outcome": outcome,
                    "reference_side": reference_side,
                    "position": answer.get("position", index),
                    "ms": answer.get("ms"),
                    "familiarity": background.get("familiarity", ""),
                    "device": background.get("device", ""),
                })
    return records, orphans


# =========================================================================== #
# quality control
# =========================================================================== #

def quality_control(records, fast_ms=1500, slow_ms=300_000):
    """
    With one question format there are no attention checks. What remains is speed
    and whether someone simply pressed the same button every time.
    """
    by_participant = defaultdict(list)
    for record in records:
        by_participant[record["participant"]].append(record)

    report = {}
    for participant, own in by_participant.items():
        responses = {}
        for record in own:
            responses.setdefault(record["response"], record)
        times = [r["ms"] for r in responses.values() if isinstance(r["ms"], (int, float))]
        outcomes = [r["outcome"] for r in own]
        sides = [(r["reference_side"] == 0) == (r["outcome"] == "win")
                 for r in own if r["outcome"] != "tie"]

        same_answer = max(Counter(outcomes).values()) / len(outcomes) if outcomes else 0.0
        same_side = max(Counter(sides).values()) / len(sides) if sides else 0.0
        median_ms = float(np.median(times)) if times else float("nan")

        reasons = []
        if times and median_ms < fast_ms:
            reasons.append("median %d ms per pair, under %d" % (median_ms, fast_ms))
        if sides and same_side > 0.95 and len(sides) >= 15:
            reasons.append("picked the same side %.0f%% of the time" % (100 * same_side))
        if outcomes and same_answer == 1.0 and len(outcomes) >= 30:
            reasons.append("gave the identical answer to every single criterion")

        report[participant] = {
            "pairs": len(responses), "judgements": len(own),
            "median_ms": median_ms, "same_answer_share": same_answer,
            "same_side_share": same_side,
            "exclude": bool(reasons), "reasons": reasons,
        }
    return report


# =========================================================================== #
# analyses
# =========================================================================== #

def tally(records, keyfn):
    counts = defaultdict(lambda: Counter())
    for record in records:
        counts[keyfn(record)][record["outcome"]] += 1
    return counts


def summarise_cell(counter, label, **extra):
    """
    Win rate among decided judgements: ties carry no information about direction, so
    the sign test drops them and the tie rate is reported alongside.
    """
    wins, losses, ties = counter["win"], counter["loss"], counter["tie"]
    decided = wins + losses
    row = S.proportion_summary(wins, decided, label)
    row.update(extra)
    row.update(losses=losses, ties=ties, shown=decided + ties,
               tie_rate=ties / (decided + ties) if (decided + ties) else float("nan"))
    if decided:
        odds, low, high = S.odds_ratio_ci(wins, decided)
        row.update(odds_ratio=odds, or_low=low, or_high=high,
                   cohens_g=S.cohens_g(row["rate"]),
                   power=S.achieved_power(row["rate"], decided))
    else:
        row.update(odds_ratio=float("nan"), or_low=float("nan"), or_high=float("nan"),
                   cohens_g=float("nan"), power=float("nan"))
    return row


def primary(records):
    """The pre-registered family: each baseline x each criterion."""
    counts = tally(records, lambda r: (r["criterion"], r["opponent"]))
    rows = []
    for (criterion, opponent), counter in sorted(counts.items()):
        rows.append(summarise_cell(
            counter,
            "%s vs %s" % (CRITERION_LABEL.get(criterion, criterion), PRETTY[opponent]),
            criterion=criterion, opponent=opponent))
    return rows


def by_criterion(records):
    counts = tally(records, lambda r: r["criterion"])
    return [summarise_cell(counter, CRITERION_LABEL.get(key, key), criterion=key)
            for key, counter in sorted(counts.items())]


def mcnemar_across_criteria(records):
    """
    The strongest paired contrast this design offers: the same person judged the same
    two images on all three criteria, so the criteria are paired exactly. Asks whether
    our advantage differs between one criterion and another.
    """
    by_response = defaultdict(dict)
    for record in records:
        by_response[record["response"]][record["criterion"]] = record

    criteria = sorted({r["criterion"] for r in records})
    results = []
    for i, first in enumerate(criteria):
        for second in criteria[i + 1:]:
            b = c = both = neither = 0
            for answers in by_response.values():
                if first not in answers or second not in answers:
                    continue
                one = answers[first]["outcome"]
                two = answers[second]["outcome"]
                if one == "tie" or two == "tie":
                    continue                      # undecided on one side, no direction
                win_one, win_two = one == "win", two == "win"
                if win_one and not win_two: b += 1
                elif win_two and not win_one: c += 1
                elif win_one and win_two: both += 1
                else: neither += 1
            result = S.mcnemar(b, c)
            result.update(unit="one answered pair", n_units=b + c + both + neither,
                          both=both, neither=neither, first=first, second=second,
                          label="wins on %s vs on %s" % (CRITERION_LABEL.get(first, first),
                                                         CRITERION_LABEL.get(second, second)))
            results.append(result)
    return results


def mcnemar_across_baselines(records, unit_key):
    """
    Does the advantage over one baseline differ from the advantage over another?
    Each unit contributes one binary per baseline: did we win the majority of its
    decided judgements.
    """
    counts = defaultdict(lambda: Counter())
    for record in records:
        counts[(record[unit_key], record["opponent"])][record["outcome"]] += 1

    outcome = {}
    for key, counter in counts.items():
        decided = counter["win"] + counter["loss"]
        if decided:
            outcome[key] = counter["win"] / decided > 0.5

    baselines = sorted({key[1] for key in outcome})
    results = []
    for i, first in enumerate(baselines):
        for second in baselines[i + 1:]:
            units = ({key[0] for key in outcome if key[1] == first} &
                     {key[0] for key in outcome if key[1] == second})
            b = sum(1 for u in units if outcome[(u, first)] and not outcome[(u, second)])
            c = sum(1 for u in units if not outcome[(u, first)] and outcome[(u, second)])
            both = sum(1 for u in units if outcome[(u, first)] and outcome[(u, second)])
            result = S.mcnemar(b, c)
            result.update(unit=unit_key, n_units=len(units), both=both,
                          neither=len(units) - b - c - both,
                          label="wins over %s vs over %s, paired by %s"
                                % (PRETTY[first], PRETTY[second], unit_key))
            results.append(result)
    return results


def ranking(records):
    """
    Bradley-Terry over the decided judgements. Ties are dropped rather than split:
    they are reported separately, and splitting them invents comparisons that were
    never made.
    """
    pairs = []
    for record in records:
        if record["outcome"] == "win":
            pairs.append((REFERENCE, record["opponent"]))
        elif record["outcome"] == "loss":
            pairs.append((record["opponent"], REFERENCE))
    return pairs


def side_bias(records):
    first, total = 0, 0
    for record in records:
        if record["outcome"] == "tie":
            continue
        total += 1
        chose_first = (record["reference_side"] == 0) == (record["outcome"] == "win")
        first += int(chose_first)
    return S.proportion_summary(first, total, "the picture shown as A was chosen")


def order_effect(records):
    decided = [r for r in records if r["outcome"] != "tie" and r["position"] is not None]
    if not decided:
        return []
    cut = max(r["position"] for r in decided) + 1
    thirds = []
    for index, (low, high) in enumerate([(0, cut / 3), (cut / 3, 2 * cut / 3), (2 * cut / 3, cut)]):
        part = [r for r in decided if low <= r["position"] < high]
        wins = sum(1 for r in part if r["outcome"] == "win")
        thirds.append(dict(S.proportion_summary(wins, len(part),
                                                "questions %d-%d" % (int(low) + 1, int(high))),
                           third=index + 1))
    return thirds


def moderators(records):
    out = []

    def bucket(name, keyfn):
        counts = tally([r for r in records if keyfn(r) not in (None, "")], keyfn)
        for key, counter in sorted(counts.items(), key=lambda kv: str(kv[0])):
            out.append(summarise_cell(counter, "%s = %s" % (name, key),
                                      moderator=name, value=str(key)))

    bucket("cropping set", lambda r: r["set"])
    bucket("regions in the image", lambda r: r["num_crops"])
    bucket("options per region", lambda r: r["tiles_per_crop"])
    bucket("familiarity", lambda r: r["familiarity"])
    bucket("device", lambda r: r["device"])
    bucket("baseline x set", lambda r: "%s / %s" % (PRETTY.get(r["opponent"], "?"), r["set"]))
    return out


def agreement(records):
    """Agreement between people who judged the same question on the same criterion."""
    by_key = defaultdict(list)
    for record in records:
        by_key[(record["item"], record["criterion"])].append(record["outcome"])
    units = [values for values in by_key.values() if len(values) >= 2]
    categories = sorted({value for values in units for value in values})
    counts = [[values.count(category) for category in categories] for values in units]
    majority = [max(Counter(values).values()) / len(values) for values in units]
    return {
        "questions_with_two_or_more_raters": len(units),
        "krippendorff_alpha": S.krippendorff_alpha(units),
        "fleiss_kappa": S.fleiss_kappa(counts) if counts else float("nan"),
        "mean_majority_share": float(np.mean(majority)) if majority else float("nan"),
    }


def regression(records):
    """GEE logistic model clustered by participant, on the decided judgements."""
    try:
        import pandas as pd
        import statsmodels.api as sm
        import statsmodels.formula.api as smf
    except ImportError:
        return {"available": False,
                "note": "install pandas and statsmodels to fit the clustered model "
                        "(pip install -r analysis/requirements.txt)"}

    rows = [r for r in records if r["outcome"] != "tie"]
    if len(rows) < 60:
        return {"available": False, "note": "too few decided judgements to fit a model"}

    frame = pd.DataFrame([{
        "won": int(r["outcome"] == "win"),
        "baseline": r["opponent"],
        "criterion": r["criterion"],
        "cropping": r["set"],
        "regions": r["num_crops"] or 0,
        "options": r["tiles_per_crop"] or 0,
        "side": r["reference_side"],
        "position": r["position"] or 0,
        "participant": r["participant"],
    } for r in rows])

    formula = ("won ~ C(baseline) + C(criterion) + C(cropping) + regions + options "
               "+ side + position")
    try:
        model = smf.gee(formula, groups="participant", data=frame,
                        family=sm.families.Binomial(),
                        cov_struct=sm.cov_struct.Exchangeable()).fit()
    except Exception as error:                                # noqa: BLE001 - report, don't crash
        return {"available": False, "note": "model did not converge: %s" % error}

    return {
        "available": True, "formula": formula, "n": int(len(frame)),
        "participants": int(frame["participant"].nunique()),
        "terms": [{"term": term, "coef": float(model.params[term]),
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


def figures(primary_rows, bt_scores, bt_ci, condition_rows, out):
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

    def dot_plot(labels, centres, lows, highs, colours, title, xlabel, reference, path, xlim=None):
        height = max(2.0, 0.42 * len(labels) + 1.4)
        figure, axes = plt.subplots(figsize=(7.2, height))
        y = np.arange(len(labels))[::-1]
        axes.axvline(reference, color=MUTED, lw=1, ls=(0, (4, 3)), zorder=1)
        for index in range(len(labels)):
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

    usable = [r for r in primary_rows if r["n"]]
    if usable:
        dot_plot(["%s\n%s" % (CRITERION_LABEL.get(r["criterion"], r["criterion"]),
                              PRETTY[r["opponent"]]) for r in usable],
                 [r["rate"] for r in usable], [r["ci_low"] for r in usable],
                 [r["ci_high"] for r in usable], [SERIES[r["opponent"]] for r in usable],
                 "How often Mix-n-match was preferred",
                 "share of decided judgements (0.5 = no preference)",
                 0.5, out / "win_rates.png", xlim=(0, 1))

    if bt_scores:
        ordered = sorted(bt_scores.items(), key=lambda kv: kv[1])
        dot_plot([PRETTY[name] for name, _ in ordered], [v for _, v in ordered],
                 [bt_ci.get(n, (v, v))[0] for n, v in ordered],
                 [bt_ci.get(n, (v, v))[1] for n, v in ordered],
                 [SERIES[n] for n, _ in ordered],
                 "Overall ranking (Bradley-Terry)", "log strength (0 = average method)",
                 0.0, out / "ranking.png")

    usable = [r for r in condition_rows if r["n"]]
    if usable:
        dot_plot([r["label"] for r in usable], [r["rate"] for r in usable],
                 [r["ci_low"] for r in usable], [r["ci_high"] for r in usable],
                 [MUTED] * len(usable), "Preference by condition",
                 "share of decided judgements (0.5 = no preference)",
                 0.5, out / "by_condition.png", xlim=(0, 1))

    return written


# =========================================================================== #
# simulation
# =========================================================================== #

def simulate(manifest, legend, participants, seed=11):
    """
    Synthetic responses with a known answer, so every test can be exercised before a
    single real participant arrives. Our method gets a genuine but modest edge that
    differs by criterion, plus a tie rate, a small bias toward whichever picture is
    shown first, and a fraction of careless raters.
    """
    rng = random.Random(seed)
    methods = legend["methods"]
    reference_code = manifest.get("reference_code", "M1")
    criteria = [c["id"] for c in manifest["criteria"]]

    # true P(we win | decided), per baseline per criterion
    truth = {
        "mnm_baseline":       {"overall": 0.70, "seamless": 0.86, "alignment": 0.58},
        "regional_prompting": {"overall": 0.60, "seamless": 0.64, "alignment": 0.57},
        "tiled_diffusion":    {"overall": 0.66, "seamless": 0.78, "alignment": 0.55},
    }
    tie_rate = {"overall": 0.12, "seamless": 0.09, "alignment": 0.20}
    side_bias_strength = 0.04
    per_person = min(manifest.get("session", {}).get("items_per_participant", 30),
                     len(manifest["items"]))

    payloads = []
    for person in range(participants):
        careless = rng.random() < 0.10
        drawn = rng.sample(manifest["items"], per_person)
        responses = []
        for position, item in enumerate(drawn):
            order = [0, 1]
            rng.shuffle(order)
            code_a = item["options"][order[0]]["m"]
            code_b = item["options"][order[1]]["m"]
            opponent = methods[code_b if code_a == reference_code else code_a]
            reference_is_a = code_a == reference_code

            picks, winners = {}, {}
            for criterion in criteria:
                if not careless and rng.random() < tie_rate[criterion]:
                    picks[criterion] = "tie"
                    winners[criterion] = "tie"
                    continue
                probability = 0.5 if careless else truth[opponent][criterion]
                if reference_is_a:
                    probability = min(0.98, probability + side_bias_strength)
                else:
                    probability = max(0.02, probability - side_bias_strength)
                we_win = rng.random() < probability
                pick = ("A" if reference_is_a else "B") if we_win else ("B" if reference_is_a else "A")
                picks[criterion] = pick
                winners[criterion] = code_a if pick == "A" else code_b

            responses.append({
                "id": item["id"], "set": item["set"], "config": item["config"],
                "seed": item.get("seed"), "num_crops": item.get("num_crops"),
                "tiles_per_crop": item.get("tiles_per_crop"),
                "combination": item.get("combination"),
                "a": order[0], "b": order[1], "a_method": code_a, "b_method": code_b,
                "answers": picks, "winners": winners, "position": position,
                "ms": rng.randint(400, 1200) if careless else rng.randint(4000, 22000),
            })

        payloads.append({
            "participant": "sim%03d" % person, "study": "mix-n-match-simulated",
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


def rate_row(row, extra=""):
    if not row["n"]:
        return "| %s | - | - | - | - |%s" % (row["label"], extra)
    return "| %s | %d/%d | %.3f | %.3f-%.3f | %s |%s" % (
        row["label"], row["wins"], row["n"], row["rate"], row["ci_low"], row["ci_high"],
        fmt_p(row["p_value"]), extra)


def write_csv(out, families):
    with (out / "results.csv").open("w", newline="", encoding="utf-8") as handle:
        writer = csv.writer(handle)
        writer.writerow(["family", "label", "criterion", "opponent", "wins", "losses",
                         "ties", "decided", "rate", "ci_low", "ci_high", "tie_rate",
                         "p_value", "p_adjusted"])
        for family, rows in families:
            for row in rows:
                writer.writerow([family, row["label"], row.get("criterion", ""),
                                 row.get("opponent", ""), row["wins"], row["losses"],
                                 row["ties"], row["n"],
                                 "%.4f" % row["rate"] if row["n"] else "",
                                 "%.4f" % row["ci_low"] if row["n"] else "",
                                 "%.4f" % row["ci_high"] if row["n"] else "",
                                 "%.4f" % row["tie_rate"] if row["shown"] else "",
                                 "%.5f" % row["p_value"] if row["n"] else "",
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
    if not records:
        print("No usable judgements. Check that the manifest matches the collected data.")
        return 1

    checks = quality_control(records)
    excluded = {p for p, info in checks.items() if info["exclude"]}
    kept = records if args.keep_excluded else \
        [r for r in records if r["participant"] not in excluded]

    args.out.mkdir(parents=True, exist_ok=True)
    lines = []
    add = lines.append

    add("# Mix-n-match user study results\n")
    add("Source: %s. Item bank built %s.\n"
        % ("simulated data" if args.simulate else str(args.responses),
           manifest.get("built_at", "?")))
    add("Every question compares our assembled image against one baseline's, judged on "
        "three criteria with A / Tie / B. Win rates below are among **decided** "
        "judgements; ties are reported separately rather than split.\n")

    pairs_kept = len({r["response"] for r in kept})
    add("## Who is in the analysis\n")
    add("| | count |")
    add("|---|---|")
    add("| Submissions received | %d |" % len(payloads))
    add("| Excluded by quality control | %d |" % len(excluded))
    add("| Kept | %d |" % (len(checks) - len(excluded)))
    add("| Image pairs judged | %d |" % pairs_kept)
    add("| Individual judgements (pairs x criteria) | %d |" % len(kept))
    if orphans:
        add("| Answers to questions not in this bank (ignored) | %d |" % orphans)
    add("")
    if excluded:
        add("Excluded participants and why:\n")
        for participant in sorted(excluded):
            add("- `%s`: %s" % (participant, "; ".join(checks[participant]["reasons"])))
        add("")

    # -- primary ----------------------------------------------------------- #
    primary_rows = primary(kept)
    for row, value in zip(primary_rows, S.holm([r["p_value"] for r in primary_rows])):
        row["p_adjusted"] = value

    add("## Primary: how often Mix-n-match was preferred\n")
    add("Pre-registered family of %d tests (each baseline x each criterion), "
        "Holm-adjusted. Exact two-sided binomial test against 0.5 on the decided "
        "judgements, with Wilson intervals.\n" % len(primary_rows))
    add("| comparison | wins | rate | 95% CI | p | Holm p | ties | odds ratio | power |")
    add("|---|---|---|---|---|---|---|---|---|")
    for row in primary_rows:
        if not row["n"]:
            add("| %s | no decided judgements | | | | | %d | | |" % (row["label"], row["ties"]))
            continue
        add("| %s | %d/%d | %.3f | %.3f-%.3f | %s | %s | %.0f%% | %.2f | %.2f |"
            % (row["label"], row["wins"], row["n"], row["rate"], row["ci_low"], row["ci_high"],
               fmt_p(row["p_value"]), fmt_p(row["p_adjusted"]), 100 * row["tie_rate"],
               row["odds_ratio"], row["power"]))
    add("")

    # -- pooled by criterion ------------------------------------------------ #
    criterion_rows = by_criterion(kept)
    add("## Pooled over the three baselines\n")
    add("| criterion | wins | rate | 95% CI | p | ties |")
    add("|---|---|---|---|---|---|")
    for row in criterion_rows:
        add("| %s | %d/%d | %.3f | %.3f-%.3f | %s | %.0f%% |"
            % (row["label"], row["wins"], row["n"], row["rate"], row["ci_low"],
               row["ci_high"], fmt_p(row["p_value"]), 100 * row["tie_rate"]))
    add("")

    # -- McNemar ------------------------------------------------------------ #
    add("## Paired contrasts (McNemar)\n")
    add("All three criteria are answered on the same pair of images by the same person, "
        "so they are exactly paired. Judgements where either side was a tie carry no "
        "direction and are dropped from the pair.\n")
    add("| contrast | units | b | c | discordant | statistic | p | method |")
    add("|---|---|---|---|---|---|---|---|")
    mcnemar_rows = (mcnemar_across_criteria(kept)
                    + mcnemar_across_baselines(kept, "config")
                    + mcnemar_across_baselines(kept, "participant"))
    for row in mcnemar_rows:
        add("| %s | %d | %d | %d | %d | %s | %s | %s |"
            % (row["label"], row.get("n_units", 0), row["b"], row["c"], row["n_discordant"],
               "%.3f" % row["statistic"] if row["statistic"] == row["statistic"] else "n/a",
               fmt_p(row["p_value"]), row["method"]))
    add("")

    # -- ranking ------------------------------------------------------------ #
    pairs = ranking(kept)
    present = sorted({m for pair in pairs for m in pair})
    bt_scores = S.bradley_terry(Counter(pairs), present) if pairs else {}
    bt_ci = S.bradley_terry_bootstrap(pairs, present, draws=args.bootstrap) \
        if pairs and args.bootstrap else {}

    add("## Overall ranking\n")
    add("Bradley-Terry over every decided judgement. Ties are dropped rather than split: "
        "splitting them would invent comparisons nobody made.\n")
    add("| method | log strength | 95% CI |")
    add("|---|---|---|")
    for method, value in sorted(bt_scores.items(), key=lambda kv: -kv[1]):
        low, high = bt_ci.get(method, (float("nan"), float("nan")))
        add("| %s | %+.3f | %s |" % (PRETTY[method], value,
                                     "%+.3f to %+.3f" % (low, high) if low == low else "n/a"))
    add("\n%d decided judgements in total.\n" % len(pairs))

    # -- bias --------------------------------------------------------------- #
    add("## Bias checks\n")
    side = side_bias(kept)
    add("**Side bias.** The image shown as A was chosen %d/%d times (%.3f, 95%% CI "
        "%.3f-%.3f, p %s). Which method is shown as A is randomised per participant, so "
        "a rate away from 0.5 is a display effect, not a result.\n"
        % (side["wins"], side["n"], side["rate"], side["ci_low"], side["ci_high"],
           fmt_p(side["p_value"])))
    thirds = order_effect(kept)
    if thirds:
        add("**Fatigue.** Our win rate through the session:\n")
        add("| part of session | wins | rate | 95% CI |")
        add("|---|---|---|---|")
        for row in thirds:
            add("| %s | %d/%d | %.3f | %.3f-%.3f |" % (row["label"], row["wins"], row["n"],
                                                       row["rate"], row["ci_low"], row["ci_high"]))
        add("")

    # -- agreement ----------------------------------------------------------- #
    agree = agreement(kept)
    add("## Agreement between raters\n")
    add("| | value |")
    add("|---|---|")
    add("| Questions answered by two or more people | %d |"
        % agree["questions_with_two_or_more_raters"])
    add("| Krippendorff's alpha (nominal, over win/loss/tie) | %.3f |" % agree["krippendorff_alpha"])
    add("| Fleiss' kappa | %.3f |" % agree["fleiss_kappa"])
    add("| Mean majority share | %.3f |" % agree["mean_majority_share"])
    add("\nLow agreement is not a failure here: it means the methods are close enough "
        "that people genuinely differ, which is itself a result.\n")

    # -- moderators ---------------------------------------------------------- #
    moderator_rows = moderators(kept)
    for row, value in zip(moderator_rows,
                          S.benjamini_hochberg([r["p_value"] if r["n"] else 1.0
                                                for r in moderator_rows])):
        row["p_adjusted"] = value
    add("## Exploratory breakdowns\n")
    add("Not pre-registered. Benjamini-Hochberg across this family.\n")
    add("| subgroup | wins | rate | 95% CI | p | BH q |")
    add("|---|---|---|---|---|---|")
    for row in moderator_rows:
        add(rate_row(row, " %s |" % fmt_p(row.get("p_adjusted", float("nan")))))
    add("")

    # -- model ---------------------------------------------------------------- #
    model = regression(kept)
    add("## Clustered logistic model\n")
    if not model["available"]:
        add("Not run: %s\n" % model["note"])
    else:
        add("GEE, exchangeable working correlation, clustered by participant "
            "(%d decided judgements from %d people). Outcome: Mix-n-match won.\n"
            % (model["n"], model["participants"]))
        add("`%s`\n" % model["formula"])
        add("| term | coefficient | odds ratio | p |")
        add("|---|---|---|---|")
        for term in model["terms"]:
            add("| `%s` | %+.3f | %.3f | %s |" % (term["term"], term["coef"],
                                                  term["odds_ratio"], fmt_p(term["p_value"])))
        add("")

    # -- power ----------------------------------------------------------------- #
    add("## How many more judgements would help\n")
    add("| true win rate | decided judgements needed per cell (80% power, alpha 0.05) |")
    add("|---|---|")
    for rate in (0.55, 0.58, 0.60, 0.65, 0.70):
        add("| %.2f | %d |" % (rate, S.required_n(rate)))
    add("")

    condition_rows = [r for r in moderator_rows if r.get("moderator") == "baseline x set"]
    (args.out / "summary.md").write_text("\n".join(lines))
    write_csv(args.out, [("primary", primary_rows), ("criterion", criterion_rows),
                         ("moderator", moderator_rows)])
    (args.out / "quality_control.json").write_text(json.dumps(checks, indent=2))

    try:
        made = figures(primary_rows, bt_scores, bt_ci, condition_rows, args.out)
    except Exception as error:                                # noqa: BLE001 - report, don't crash
        made = ["figures failed (%s); summary.md and results.csv are still complete" % error]

    print("participants kept: %d of %d   pairs: %d   judgements: %d"
          % (len(checks) - len(excluded), len(checks), pairs_kept, len(kept)))
    for row in primary_rows:
        if not row["n"]:
            continue
        print("  %-46s %.3f  (n=%d, ties %2.0f%%, Holm p %s)"
              % (row["label"], row["rate"], row["n"], 100 * row["tie_rate"],
                 fmt_p(row["p_adjusted"])))
    print("\nwrote %s/summary.md, results.csv, quality_control.json" % args.out)
    for path in made:
        print("      %s" % path)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
