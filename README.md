# Mix-n-match user study

A blind, browser-based comparison of **Mix-n-match** against three baselines,
hosted on GitHub Pages, plus the paired statistics for the results.

- **The site** is static and fully generated: `build/build_study.py` walks the
  output tree, renders every comparison it can make *faithfully*, and writes the
  item bank the page serves.
- **Participants never see a method name.** The manifest labels methods `M1`–`M4`;
  only `data/legend.json` maps those back, and the page never fetches it.
- **Each participant sees ~30 of the items**, drawn to spread across configs, with
  left/right order randomised per person.

---

## Setting it up

### 1. Put the sources in place

Copy (or symlink) these two trees into the repo root:

```
configs_for_baseline/
├── configs_for_baseline_dynamic/config_*.json
└── configs_for_baseline_static/config_*.json

outputs/
├── configs_for_baseline_dynamic/
│   ├── <prefix>_<mode>_<tag>/tiles/{layout.json, crop<i>/tile<j>.png, cropBg/tile0.png}
│   └── <prefix>_baseline/tiles/crop<i>/tile<j>.png
├── configs_for_baseline_static/ …
├── tiled_diffusion/configs_for_baseline_*/<config name>_<prefix>/tiles/crop<ii>_cand<jj>.png
└── regional_prompting/configs_for_baseline_*/<prefix>/{layout.json, crop<i>/tile<j>.png}
```

Both are **gitignored on purpose**. The raw tree runs to many gigabytes; GitHub
Pages caps a published site near 1 GB, and only the WebP under `assets/` is
committed. `outputs/_baseline_logs/` and `_baseline_work/` are ignored.

Trim the config set to the configs you actually want in the study before building —
the item bank uses a quota per config, so it scales with whatever you leave in.

### 2. Build

```bash
pip install pillow numpy pyyaml
python build/build_study.py --root . --out . --dry-run   # what would be built, and what wouldn't
python build/build_study.py --root . --out . --clean
```

The dry run is worth reading. It prints the item bank and every
(config, method, level) combination it had to skip, with the reason — a missing run
folder, a `layout.json` from before the `crop_map` schema, a layout Tiled Diffusion's
vertical seam chain cannot represent. `analysis/build_report.json` has the per-config detail.

Knobs live in `build/build_config.yaml`: per-config quotas, which sets each baseline
may appear in, image quality and size, and how many questions one session holds.

### 3. Wire up response collection

GitHub Pages is static, so answers go to a Google Sheet through a small Apps Script.

1. Create a Sheet. Extensions → Apps Script. Paste `apps_script/Code.gs`.
2. Deploy → New deployment → **Web app**, *Execute as: Me*, *Who has access: Anyone*.
   Authorise it (the "unverified app" warning is your own script).
3. Copy the `/exec` URL into `endpoint` in `js/config.js`.

Each submission becomes one row in `responses` (with the raw JSON) and one row per
answer in `answers`. With no endpoint set the study still runs end to end and offers
participants a download instead — useful for testing, not for collecting.

While you are in `js/config.js`, set `contact` if you want an address on the welcome
screen. It is empty by default rather than publishing an address to scrapers.

### 4. Check it locally

```bash
python -m http.server 8000    # then open http://localhost:8000
```

Click all the way through, including the last screen, and confirm a row lands in the
Sheet.

### 5. Publish

Simplest: Settings → Pages → Deploy from branch, `main`, root. `.nojekyll` is already
in place. Share the resulting URL.

That also serves `build/` and `analysis/`, including `analysis/legend.json`, which
says which code was which method. A participant would have to go looking for it, but
it is there. For a study you are going to publish about, push only the site instead:

```bash
bash build/publish.sh --dry-run    # see what would go
bash build/publish.sh
```

then point Pages at the `gh-pages` branch. Nothing but `index.html`, `css/`, `js/`,
`data/manifest.json`, `assets/` and `.nojekyll` is served.

---

## Testing without real outputs

```bash
python build/make_mock_outputs.py --out mock_root
python build/build_study.py --root mock_root --out . --clean
python -m http.server 8000
```

`mock_root/` is a synthetic four-method tree with the real directory layout, file
names, alpha channels and JSON schemas. Every picture is a flat colour labelled with
its method, crop and tile, so anything mis-wired is obvious at a glance. Delete it
once the real outputs land, then rebuild.

---

## What the study asks

| Question type | What the participant sees | What it measures |
|---|---|---|
| `tile_ab_adherence` | One description, two versions of one region | Does the region show what was described |
| `tile_ab_quality` | The same, different question | Plain image quality |
| `comp_ab_coherence` | Two whole images | Does it hold together as one picture rather than pieces |
| `comp_ab_adherence` | Two whole images, all the descriptions listed | Does the whole image deliver every part |
| `tile_bw4` / `comp_bw4` | All four methods at once | Best and worst — an efficient full ranking |
| `attention` | Looks like an ordinary question; one picture is from an unrelated prompt | Filters careless responses |

Every baseline is compared against Mix-n-match on the **same** config, crop and tile
index — the same description — so the three pairs are matched, both for fairness and
so the analysis can pair them.

---

## Repository

| Path | What it is |
|---|---|
| `index.html`, `css/`, `js/` | The study. Vanilla JS, no build step, no runtime dependency |
| `build/build_study.py` | Walks the outputs, renders the items, writes the manifest |
| `build/build_config.yaml` | Quotas, per-baseline rules, encoding, session length |
| `build/make_mock_outputs.py` | Synthetic outputs for testing |
| `build/publish.sh` | Pushes only the site to a `gh-pages` branch (see Publish) |
| `data/manifest.json` | The item bank the page serves |
| `analysis/legend.json` | Method codes and attention-check answers. Deliberately outside `data/`, which is what the page fetches |
| `analysis/build_report.json` | What was built, what was skipped, and why |
| `assets/` | Generated WebP. Regenerated by the build; do not edit |
| `apps_script/Code.gs` | The Sheet endpoint |
| `analysis/` | The statistics. See `analysis/README.md` for the pre-registration |

---

## How comparisons are made fair

The four methods do not write the same kind of file, so the build normalises them:

| Method | What it writes | How it is shown |
|---|---|---|
| Mix-n-match | RGBA bounding box of the region it solved, transparent outside | Composited on a neutral mat, irregular outline intact |
| Regional Prompting | RGB, exactly the config's rectangle | Scaled to fit the same display box |
| Naive baseline | RGB, the whole 1024² canvas | Whole image scaled to fit inside the display box — it stays square, because that is what the method actually produced |
| Tiled Diffusion | RGB, whole 1024² per region | The same |

Whole-image questions composite one tile per region: Mix-n-match through its own
`crop_map`, Regional Prompting through its background region plus the config
rectangles, and the two whole-canvas methods by cutting each config rectangle out of
their image and pasting it back. Where that cannot be done without leaving holes, the
item is not produced. `analysis/README.md` lists the caveats that belong in the write-up.
