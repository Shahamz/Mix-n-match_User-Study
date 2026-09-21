# Analysis

```bash
pip install -r analysis/requirements.txt      # numpy is the only hard requirement
python analysis/analyze.py --responses answers.csv
```

`--responses` takes whatever the collection produced:

| you have | pass |
|---|---|
| the `answers` tab of the Sheet, exported | `answers.csv` |
| the `responses` tab (it carries `raw_json`) | `responses.csv` |
| files participants downloaded when the endpoint was unreachable | the folder, or one `.json` |

Before any real data exists, run the whole pipeline against synthetic participants:

```bash
python analysis/analyze.py --simulate 40
```

The simulation gives Mix-n-match a known edge over each baseline and adds a small
bias toward whichever picture is shown first, plus a fraction of careless raters —
so you can confirm the tests recover what was put in and that quality control
catches the careless ones.

Output lands in `analysis/out/`: `summary.md` (read this), `results.csv`,
`quality_control.json`, and three figures.

---

## Pre-registration

**Fix this before collecting data.** Everything below is what makes the primary
p-values interpretable; adding tests afterwards and reporting the best one does not.

### The question every participant answers

One format, thirty times: a set of our assembled images against the matching set of one
randomly chosen baseline's (the same tile combinations, image for image), judged as sets
on four criteria — **overall quality**, **seamlessness**, **overall coherence**,
**prompt alignment** — each answered *A wins* / *Tie* / *B wins*. Which method is shown
as A is randomised per participant.

Seamlessness and coherence are deliberately separate: the first is local (visible joins
where regions meet), the second is global (whether the scene holds together at all).
A composite can be flawlessly blended and still make no sense, and vice versa.

### Primary hypothesis

Mix-n-match is preferred to each of the three baselines more often than chance, on each
of the four criteria.

### Primary family — 12 tests, Holm-adjusted

Three baselines × four criteria. Exact two-sided binomial test against 0.5 on the
**decided** judgements, Wilson 95% intervals, Holm–Bonferroni across the twelve.

**Ties are dropped, not split.** A tie says the two images were indistinguishable on
that criterion, not that one won half of the time; splitting invents comparisons nobody
made. The tie rate is reported next to every cell, and a high one is itself a finding.

Everything else — the pooled-by-criterion table, the McNemar contrasts, the ranking,
agreement, subgroup effects — is **exploratory**, reported with Benjamini–Hochberg
q-values, and must be described as such.

### Exclusions, decided in advance

There are no attention checks: with a single question format there is nowhere to hide
one. A participant is dropped if their median time per image pair is under 2 seconds
(four criteria cannot be judged faster than that), if they choose the same side on more
than 95% of at least fifteen decided judgements, or if they give the identical answer to
every single criterion across a full session. `--keep-excluded` reruns everything
without these rules; report both if the conclusion changes.

### Target sample

For a true win rate of 0.60, 80% power at α = 0.05 needs ≈194 decided judgements per
cell. Each participant contributes roughly 10 pairs per baseline × 4 criteria, minus
ties, so plan for **at least 25–30 completed sessions**; `summary.md` prints the full
table.

---

## What each section is

| Section | What it answers |
|---|---|
| Who is in the analysis | Sample size, exclusions and why |
| Primary | The twelve pre-registered win rates, Holm-adjusted, with tie rates |
| Pooled over the three baselines | Where the advantage is largest across criteria |
| Paired contrasts (McNemar) | **The strongest evidence here.** All four criteria are answered on the *same* pair of images by the *same* person, so they are exactly paired: this asks whether the advantage on seamlessness really differs from the advantage on coherence, or on prompt alignment. Also, paired by config and by participant, whether the advantage over one baseline differs from another |
| Overall ranking | Bradley–Terry over every decided judgement, bootstrap intervals |
| Bias checks | Side bias from the display, and whether the advantage drifts as a session wears on |
| Agreement | Krippendorff's α and Fleiss' κ over win/loss/tie. Low agreement here is a finding, not a failure: it means the methods are close |
| Exploratory breakdowns | Static vs dynamic cropping, number of regions, options per region, self-reported familiarity, device |
| Clustered logistic model | GEE with an exchangeable working correlation, clustered by participant. statsmodels has no frequentist binomial GLMM, which is why this is GEE rather than a mixed model |
| Power | Decided judgements needed per cell at several true effect sizes |

## Caveats to carry into the write-up

- **Tiled Diffusion images are a faithful reconstruction, at a different aspect
  ratio.** Its composite is rebuilt exactly as `crop_output.compose_combination`
  builds it, from `tiles/` plus the placement recorded in `run_meta.json`. Because
  it generates every region as a 1024² square and chains them vertically, its image
  is `1024 × 1024·n` — taller than our square canvas. It is shown at half the width
  of ours, so its greater height is visible to the participant. Whether
  that aspect difference influenced judgements is worth acknowledging.
- **The naive baseline is shown as a grid of whole images, not one canvas.** It
  generates one plain image per prompt and knows nothing of the layout, so each is
  shown intact and unresized, in a near-square grid (halved to size). That is its
  real output, undistorted, but it plainly reads as separate pictures, which will
  have shaped the seamlessness and coherence judgements in particular. With 3, 5, 7
  or 8 crops the grid has empty cells, filled with the neutral mat.
- **Regions are not pixel-identical across methods.** Mix-n-match solves its own
  regions with `MRF_alpha`; the baselines use the config's rectangles. A pair is
  matched on *the set of descriptions being illustrated*, not on identical geometry.

## Files

| File | What it is |
|---|---|
| `analyze.py` | Loading, quality control, every analysis, the report and figures |
| `stats.py` | Binomial and McNemar exact tests, Wilson intervals, Holm and BH, Bradley–Terry with bootstrap, Fleiss' κ, Krippendorff's α, power. numpy only |
