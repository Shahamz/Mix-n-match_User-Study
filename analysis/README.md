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

### Primary hypothesis

Mix-n-match is preferred to each of the three baselines more often than chance, at
both question levels.

### Primary family — 6 tests, Holm-adjusted

The reference method's win rate against each of `mnm_baseline`,
`regional_prompting` and `tiled_diffusion`, separately for single-region and
whole-image questions. Exact two-sided binomial test against 0.5, Wilson 95%
intervals, Holm–Bonferroni across the six.

Everything else — the per-question breakdown, subgroup effects, the ranking,
agreement — is **exploratory**, reported with Benjamini–Hochberg q-values, and
must be described as such.

### Exclusions, decided in advance

A participant is dropped if they fail any attention check, if their median answer
time is under 800 ms, or if they choose the same side on more than 90% of at least
ten questions. `--keep-excluded` reruns everything without these rules; report both
if the conclusion changes.

### Target sample

For a true win rate of 0.60, 80% power at α = 0.05 needs ≈194 judgements per
comparison. Each participant contributes roughly 3 judgements per comparison, so
plan for **at least 65 completed sessions**; `summary.md` prints the full table.

---

## What each section is

| Section | What it answers |
|---|---|
| Who is in the analysis | Sample size, exclusions and why |
| Primary | The pre-registered win rates, Holm-adjusted |
| By the exact question | Does the advantage come from prompt match, coherence, or plain image quality |
| Paired contrasts (McNemar) | Does the advantage over one baseline differ from the advantage over another, paired by picture content and again by person; and whether whole-image wins differ between the coherence and prompt-match questions |
| Overall ranking | Bradley–Terry over every pairwise judgement, four-way answers expanded into the pairs they imply, bootstrap intervals |
| Best–worst counting scores | The simple `(best − worst) / shown` score, as a check that the model agrees with the raw counts |
| Bias checks | Side bias from the display, and whether the advantage drifts as a session wears on |
| Agreement | Krippendorff's α and Fleiss' κ. Low agreement here is a finding, not a failure: it means the methods are close |
| Exploratory breakdowns | Static vs dynamic cropping, number of regions, options per region, self-reported familiarity, device |
| Clustered logistic model | GEE with an exchangeable working correlation, clustered by participant. statsmodels has no frequentist binomial GLMM, which is why this is GEE rather than a mixed model |
| Power | Judgements needed per comparison at several true effect sizes |

## Caveats to carry into the write-up

- **Tiled Diffusion composites are a reconstruction.** It emits whole 1024² images
  per region and constrains seams along a vertical chain; the build script cuts the
  config's rectangle out of each and pastes it back. That is faithful where the
  config's crops are full-width bands stacked top to bottom, and the build tags
  every composite item with `stitch_faithful`. Items are only produced for layouts
  where it holds unless `allow_approximate_stitching` is turned on.
- **Regions are not pixel-identical across methods.** Mix-n-match solves its own
  regions with `MRF_alpha`; the baselines use the config's rectangles. A pair is
  matched on *the description being illustrated*, not on identical geometry, and
  participants are told to judge content rather than outline.
- **The naive baseline cannot be composited where the config's rectangles leave
  gaps**, because it generates no background image. Those items are skipped, so its
  whole-image comparisons come from a narrower set of configs than the others. The
  counts in the primary table show this.

## Files

| File | What it is |
|---|---|
| `analyze.py` | Loading, quality control, every analysis, the report and figures |
| `stats.py` | Binomial and McNemar exact tests, Wilson intervals, Holm and BH, Bradley–Terry with bootstrap, Fleiss' κ, Krippendorff's α, power. numpy only |
