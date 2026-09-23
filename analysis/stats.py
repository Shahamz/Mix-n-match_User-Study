"""
The statistical machinery for the user study, implemented against numpy alone.

Everything the pre-registered analysis needs runs without scipy or statsmodels, so
results can be produced on any machine. The regression models in analyze.py use
statsmodels when it is installed and are reported as unavailable when it is not.
"""

from __future__ import annotations

import math
from collections import defaultdict

import numpy as np

# --------------------------------------------------------------------------- #
# normal distribution
# --------------------------------------------------------------------------- #

def norm_cdf(z):
    return 0.5 * (1.0 + math.erf(z / math.sqrt(2.0)))


def norm_ppf(p):
    """Acklam's rational approximation to the inverse normal CDF (|error| < 1.15e-9)."""
    if not 0.0 < p < 1.0:
        raise ValueError("norm_ppf needs 0 < p < 1")
    a = [-3.969683028665376e+01, 2.209460984245205e+02, -2.759285104469687e+02,
         1.383577518672690e+02, -3.066479806614716e+01, 2.506628277459239e+00]
    b = [-5.447609879822406e+01, 1.615858368580409e+02, -1.556989798598866e+02,
         6.680131188771972e+01, -1.328068155288572e+01]
    c = [-7.784894002430293e-03, -3.223964580411365e-01, -2.400758277161838e+00,
         -2.549732539343734e+00, 4.374664141464968e+00, 2.938163982698783e+00]
    d = [7.784695709041462e-03, 3.224671290700398e-01, 2.445134137142996e+00,
         3.754408661907416e+00]
    low, high = 0.02425, 1 - 0.02425
    if p < low:
        q = math.sqrt(-2 * math.log(p))
        return (((((c[0] * q + c[1]) * q + c[2]) * q + c[3]) * q + c[4]) * q + c[5]) / \
               ((((d[0] * q + d[1]) * q + d[2]) * q + d[3]) * q + 1)
    if p > high:
        q = math.sqrt(-2 * math.log(1 - p))
        return -(((((c[0] * q + c[1]) * q + c[2]) * q + c[3]) * q + c[4]) * q + c[5]) / \
                ((((d[0] * q + d[1]) * q + d[2]) * q + d[3]) * q + 1)
    q = p - 0.5
    r = q * q
    return (((((a[0] * r + a[1]) * r + a[2]) * r + a[3]) * r + a[4]) * r + a[5]) * q / \
           (((((b[0] * r + b[1]) * r + b[2]) * r + b[3]) * r + b[4]) * r + 1)


# --------------------------------------------------------------------------- #
# binomial
# --------------------------------------------------------------------------- #

def _log_binom_pmf(k, n, p):
    if p <= 0.0:
        return 0.0 if k == 0 else -math.inf
    if p >= 1.0:
        return 0.0 if k == n else -math.inf
    return (math.lgamma(n + 1) - math.lgamma(k + 1) - math.lgamma(n - k + 1)
            + k * math.log(p) + (n - k) * math.log(1 - p))


def binom_test(successes, trials, p=0.5):
    """
    Exact two-sided binomial test, by the method of small p-values: sum the
    probability of every outcome no more likely than the one observed.
    """
    if trials == 0:
        return float("nan")
    observed = _log_binom_pmf(successes, trials, p)
    tolerance = 1e-7
    total = 0.0
    for k in range(trials + 1):
        if _log_binom_pmf(k, trials, p) <= observed + tolerance:
            total += math.exp(_log_binom_pmf(k, trials, p))
    return min(1.0, total)


def wilson_interval(successes, trials, confidence=0.95):
    """Wilson score interval: behaves sensibly at proportions near 0 and 1."""
    if trials == 0:
        return (float("nan"), float("nan"))
    z = norm_ppf(1 - (1 - confidence) / 2)
    phat = successes / trials
    denominator = 1 + z * z / trials
    centre = (phat + z * z / (2 * trials)) / denominator
    spread = z * math.sqrt(phat * (1 - phat) / trials + z * z / (4 * trials * trials)) / denominator
    return (max(0.0, centre - spread), min(1.0, centre + spread))


def proportion_summary(successes, trials, label=""):
    low, high = wilson_interval(successes, trials)
    return {
        "label": label,
        "wins": int(successes),
        "n": int(trials),
        "rate": successes / trials if trials else float("nan"),
        "ci_low": low,
        "ci_high": high,
        "p_value": binom_test(successes, trials, 0.5),
    }


# --------------------------------------------------------------------------- #
# McNemar
# --------------------------------------------------------------------------- #

def mcnemar(b, c, exact=None):
    """
    McNemar's test on the discordant cells of a paired 2x2 table.

    b, c are the two disagreement counts. Exact (binomial) below 25 discordant
    pairs, chi-square with Edwards' continuity correction above, which is the
    usual convention; pass `exact` to force one or the other.
    """
    discordant = b + c
    if discordant == 0:
        return {"b": b, "c": c, "n_discordant": 0, "statistic": float("nan"),
                "p_value": float("nan"), "method": "undefined (no disagreements)",
                "odds_ratio": float("nan")}
    use_exact = discordant < 25 if exact is None else exact
    odds = (b / c) if c else float("inf")
    if use_exact:
        return {"b": b, "c": c, "n_discordant": discordant,
                "statistic": float(min(b, c)),
                "p_value": binom_test(min(b, c), discordant, 0.5),
                "method": "exact binomial", "odds_ratio": odds}
    statistic = (abs(b - c) - 1) ** 2 / discordant
    return {"b": b, "c": c, "n_discordant": discordant, "statistic": statistic,
            "p_value": chi2_sf(statistic, 1),
            "method": "chi-square, continuity corrected", "odds_ratio": odds}


def clustered_mcnemar(clusters):
    """
    McNemar's test when the discordant pairs arrive in clusters, one cluster per participant.

    A judgement here IS a discordant pair: the person saw both methods on the same images and said which
    won, so a decided judgement is one disagreement between the two. Plain McNemar would then treat a
    participant's thirty judgements as thirty independent pairs, and the p value would be far too small.

    This is Durkalski et al. (2003): the cluster totals are summed, and the variance is estimated from how
    much the clusters disagree with each other rather than assumed binomial, which is what makes it robust
    to whatever the within-participant correlation happens to be.

        Z = sum_i (b_i - c_i) / sqrt( sum_i (b_i - c_i)^2 )

    With one judgement per cluster it reduces to the ordinary sign test, and the more a participant's
    judgements repeat each other the wider the variance it estimates.

    Args:
        clusters: iterable of (b_i, c_i) - the wins and the losses of one participant in this cell.

    Returns:
        Dict with the totals, the number of clusters, the statistic, its two-sided p value and the method.
        The p value is nan when no cluster disagrees at all, which is when the statistic is undefined.
    """
    clusters = [(int(b), int(c)) for b, c in clusters if (b + c) > 0]
    b_total = sum(b for b, _ in clusters)
    c_total = sum(c for _, c in clusters)
    differences = [b - c for b, c in clusters]
    spread = sum(difference ** 2 for difference in differences)
    summary = {"b": b_total, "c": c_total, "n_discordant": b_total + c_total,
               "clusters": len(clusters), "method": "clustered McNemar (Durkalski), by participant"}
    if not clusters or spread == 0:
        summary.update(statistic=float("nan"), p_value=float("nan"),
                       method="undefined (no cluster disagrees)")
        return summary
    z = sum(differences) / math.sqrt(spread)
    summary.update(statistic=z, p_value=2.0 * (1.0 - norm_cdf(abs(z))))
    return summary


def cluster_bootstrap_rate(clusters, draws=2000, confidence=0.95, seed=11):
    """
    Confidence interval for a win rate when the judgements come in clusters: participants are resampled
    with replacement, each bringing all of its judgements, so the interval widens with the correlation
    between one person's answers instead of ignoring it.

    Args:
        clusters: iterable of (wins_i, decided_i) per participant.
        draws: bootstrap draws.
        confidence: interval mass.
        seed: seed of the resampling.

    Returns:
        (low, high), or (nan, nan) when there is nothing to resample.
    """
    clusters = [(int(wins), int(decided)) for wins, decided in clusters if decided > 0]
    if not clusters or draws <= 0:
        return float("nan"), float("nan")
    wins = np.array([pair[0] for pair in clusters], dtype=float)
    decided = np.array([pair[1] for pair in clusters], dtype=float)
    rng = np.random.default_rng(seed)
    estimates = []
    for _ in range(draws):
        picks = rng.integers(0, len(clusters), len(clusters))
        total = decided[picks].sum()
        if total:
            estimates.append(wins[picks].sum() / total)
    if not estimates:
        return float("nan"), float("nan")
    tail = (1.0 - confidence) / 2.0
    return (float(np.percentile(estimates, 100 * tail)),
            float(np.percentile(estimates, 100 * (1.0 - tail))))


def chi2_sf(statistic, degrees):
    """Upper tail of the chi-square distribution. Only df 1 and 2 are needed here."""
    if statistic <= 0:
        return 1.0
    if degrees == 1:
        return 2 * (1 - norm_cdf(math.sqrt(statistic)))
    if degrees == 2:
        return math.exp(-statistic / 2)
    # Wilson-Hilferty for anything else.
    z = ((statistic / degrees) ** (1 / 3) - (1 - 2 / (9 * degrees))) / math.sqrt(2 / (9 * degrees))
    return 1 - norm_cdf(z)


# --------------------------------------------------------------------------- #
# multiplicity
# --------------------------------------------------------------------------- #

def holm(p_values):
    """Holm-Bonferroni step-down adjusted p-values, in the input order."""
    indexed = sorted((p, i) for i, p in enumerate(p_values))
    total = len(p_values)
    adjusted = [0.0] * total
    running = 0.0
    for rank, (p, index) in enumerate(indexed):
        running = max(running, min(1.0, (total - rank) * p))
        adjusted[index] = running
    return adjusted


def benjamini_hochberg(p_values):
    """BH adjusted p-values (q-values), in the input order."""
    indexed = sorted(((p, i) for i, p in enumerate(p_values)), reverse=True)
    total = len(p_values)
    adjusted = [0.0] * total
    running = 1.0
    for position, (p, index) in enumerate(indexed):
        rank = total - position
        running = min(running, min(1.0, p * total / rank))
        adjusted[index] = running
    return adjusted


# --------------------------------------------------------------------------- #
# Bradley-Terry
# --------------------------------------------------------------------------- #

def bradley_terry(wins, items, iterations=1000, tolerance=1e-10):
    """
    Fit Bradley-Terry strengths by Hunter's MM algorithm.

    wins[(a, b)] is the number of times a was preferred to b. Returns log-strengths
    centred on zero, so 0 is the average competitor and +1 means e times the odds.
    """
    index = {name: i for i, name in enumerate(items)}
    size = len(items)
    matrix = np.zeros((size, size))
    for (winner, loser), count in wins.items():
        matrix[index[winner], index[loser]] += count

    totals = matrix.sum(axis=1)
    played = matrix + matrix.T
    strength = np.ones(size)
    for _ in range(iterations):
        updated = np.empty(size)
        for i in range(size):
            denominator = 0.0
            for j in range(size):
                if i == j or played[i, j] == 0:
                    continue
                denominator += played[i, j] / (strength[i] + strength[j])
            updated[i] = totals[i] / denominator if denominator > 0 else strength[i]
        if updated.sum() <= 0 or not np.isfinite(updated).all():
            break
        updated /= updated.sum()
        if np.max(np.abs(updated - strength / strength.sum())) < tolerance:
            strength = updated
            break
        strength = updated

    strength = np.clip(strength / strength.sum(), 1e-12, None)
    log_strength = np.log(strength)
    return {name: float(log_strength[index[name]] - log_strength.mean()) for name in items}


def bradley_terry_bootstrap(comparisons, items, draws=2000, seed=7):
    """
    Percentile bootstrap over the comparisons themselves, so the interval reflects
    how many judgements there were rather than assuming a likelihood shape.
    """
    rng = np.random.default_rng(seed)
    comparisons = list(comparisons)
    if not comparisons:
        return {name: (float("nan"), float("nan")) for name in items}
    samples = defaultdict(list)
    for _ in range(draws):
        picks = rng.integers(0, len(comparisons), len(comparisons))
        wins = defaultdict(int)
        for pick in picks:
            winner, loser = comparisons[pick]
            wins[(winner, loser)] += 1
        fit = bradley_terry(wins, items, iterations=120)
        for name, value in fit.items():
            samples[name].append(value)
    return {name: (float(np.percentile(values, 2.5)), float(np.percentile(values, 97.5)))
            for name, values in samples.items()}


# --------------------------------------------------------------------------- #
# agreement
# --------------------------------------------------------------------------- #

def fleiss_kappa(counts):
    """
    counts: rows are items, columns are categories, cells are how many raters chose
    that category. Rows with fewer than two raters are dropped.
    """
    counts = np.asarray([row for row in counts if sum(row) >= 2], dtype=float)
    if counts.size == 0:
        return float("nan")
    raters = counts.sum(axis=1)
    if len(set(raters.tolist())) != 1:
        # Unequal rater counts: use the per-item agreement form, which tolerates them.
        agreement = ((counts * (counts - 1)).sum(axis=1)) / (raters * (raters - 1))
        observed = agreement.mean()
        proportions = counts.sum(axis=0) / counts.sum()
        expected = float((proportions ** 2).sum())
    else:
        n = raters[0]
        observed = float((((counts ** 2).sum(axis=1) - n) / (n * (n - 1))).mean())
        proportions = counts.sum(axis=0) / counts.sum()
        expected = float((proportions ** 2).sum())
    if expected >= 1:
        return float("nan")
    return (observed - expected) / (1 - expected)


def krippendorff_alpha(units):
    """
    Nominal Krippendorff's alpha.

    units: an iterable of lists, one per unit, holding the values each rater gave
    that unit. Units rated only once carry no information and are dropped.

    Built the standard way: a coincidence matrix of value pairs within units,
    weighted 1/(m-1) so units with many raters do not dominate, then observed
    disagreement against the disagreement expected from the marginals.
    """
    units = [list(values) for values in units if len(values) >= 2]
    if not units:
        return float("nan")
    categories = sorted({value for values in units for value in values})
    index = {value: position for position, value in enumerate(categories)}

    coincidence = np.zeros((len(categories), len(categories)))
    for values in units:
        weight = 1.0 / (len(values) - 1)
        for i, first in enumerate(values):
            for j, second in enumerate(values):
                if i != j:
                    coincidence[index[first], index[second]] += weight

    total = coincidence.sum()
    if total <= 1:
        return float("nan")
    marginals = coincidence.sum(axis=1)
    observed = total - np.trace(coincidence)
    expected = (total ** 2 - (marginals ** 2).sum()) / (total - 1)
    if expected <= 0:
        return float("nan")
    return float(1 - observed / expected)


# --------------------------------------------------------------------------- #
# effect size and power
# --------------------------------------------------------------------------- #

def cohens_g(rate):
    """Cohen's g for a proportion against 0.5: the plain distance from chance."""
    return rate - 0.5


def odds_ratio_ci(successes, trials, confidence=0.95):
    """Odds of winning versus even odds, with a log-scale Wald interval."""
    if trials == 0 or successes in (0, trials):
        return (float("nan"), float("nan"), float("nan"))
    odds = successes / (trials - successes)
    standard_error = math.sqrt(1 / successes + 1 / (trials - successes))
    z = norm_ppf(1 - (1 - confidence) / 2)
    return (odds, odds * math.exp(-z * standard_error), odds * math.exp(z * standard_error))


def required_n(rate, power=0.8, alpha=0.05):
    """Judgements needed to detect a true win rate of `rate` against 0.5."""
    if abs(rate - 0.5) < 1e-9:
        return float("inf")
    z_alpha = norm_ppf(1 - alpha / 2)
    z_beta = norm_ppf(power)
    numerator = z_alpha * math.sqrt(0.25) + z_beta * math.sqrt(rate * (1 - rate))
    return math.ceil((numerator / (rate - 0.5)) ** 2)


def achieved_power(rate, trials, alpha=0.05):
    """Power of the two-sided test at the observed rate and sample size."""
    if trials == 0:
        return float("nan")
    z_alpha = norm_ppf(1 - alpha / 2)
    effect = abs(rate - 0.5) * math.sqrt(trials) / math.sqrt(rate * (1 - rate)) \
        if 0 < rate < 1 else float("inf")
    return 1 - norm_cdf(z_alpha - effect) + norm_cdf(-z_alpha - effect)
