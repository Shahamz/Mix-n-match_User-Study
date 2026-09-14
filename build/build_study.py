#!/usr/bin/env python3
"""
Build the Mix-n-match user study from a four-method output tree.

Nothing about the study is hardcoded: this walks whatever configs and run folders are
present, works out which comparisons it can render *faithfully*, renders them to WebP,
and writes the item bank the page serves.

    python build/build_study.py --root mock_root --out . --dry-run
    python build/build_study.py --root . --out .

Outputs
    data/manifest.json      the item bank (public; methods appear only as codes M1..M4)
    data/legend.json        code -> method, and the attention-check answers (NOT served)
    data/build_report.json  what was built, what was skipped and why
    assets/{tile,comp}/*.webp

Source-of-truth for the on-disk formats (do not edit those repos):
    Mix_n_match/vis_app.py          save_separate_tiles, crop_regions, assemble_combination
    tiled-diffusion/crop_output.py  save_tiles, save_run_meta
    Regional-Prompting-FLUX/infer_pixeldit_regional.py  save_tiles, write_layout
"""

from __future__ import annotations

import argparse
import hashlib
import json
import random
import shutil
from collections import Counter, defaultdict
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path

import numpy as np
import yaml
from PIL import Image

PATCH = 16                  # Mix_n_match/macros.py: PATCH_SIZE_PIXELS
SETS = ("static", "dynamic")
REFERENCE = "mix_n_match"
BASELINES = ("mnm_baseline", "regional_prompting", "tiled_diffusion")
ALL_METHODS = (REFERENCE,) + BASELINES

# Fixed codes, so a rebuild does not invalidate data already collected.
METHOD_CODE = {"mix_n_match": "M1", "mnm_baseline": "M2",
               "regional_prompting": "M3", "tiled_diffusion": "M4"}


# =========================================================================== #
# discovery
# =========================================================================== #

@dataclass
class Run:
    """One method's output folder for one config, or the reason there isn't one."""
    method: str
    path: Path | None = None
    extra: dict = field(default_factory=dict)
    reason: str = ""

    @property
    def ok(self):
        return self.path is not None


@dataclass
class Entry:
    """A config plus every run of it that was found."""
    set_name: str
    config_path: Path
    config: dict
    runs: dict
    layout: dict | None = None          # mix_n_match tiles/layout.json
    regions: list = field(default_factory=list)   # per crop: (bbox, mask) or None

    prefix: str = ""
    num_crops: int = 0
    tiles_per_crop: int = 0

    def __post_init__(self):
        self.prefix = self.config["prefix"]
        self.num_crops = self.config["num_crops"]
        self.tiles_per_crop = self.config["tiles_per_crop"]

    @property
    def size(self):
        return self.config["width"], self.config["height"]

    def prompt(self, crop_index, tile_index):
        return self.config["tile_prompts"][crop_index][tile_index]


def find_mix_n_match(outputs, set_folder, prefix):
    """outputs/<set>/<prefix>_<mode>_<tag>/tiles/ — the sibling <prefix>_baseline is a different run."""
    base = outputs / set_folder
    if not base.is_dir():
        return Run(REFERENCE, reason=f"no {set_folder}/ under outputs")
    candidates = sorted(p for p in base.glob(f"{prefix}_*")
                        if p.is_dir() and p.name != f"{prefix}_baseline" and (p / "tiles").is_dir())
    if not candidates:
        return Run(REFERENCE, reason="no run folder with a tiles/ directory")
    tiles = candidates[-1] / "tiles"
    layout_file = tiles / "layout.json"
    if not layout_file.is_file():
        return Run(REFERENCE, reason="run folder has no tiles/layout.json")
    layout = json.loads(layout_file.read_text())
    if "crop_map" not in layout:
        # Older runs wrote a rectangle list. Their geometry cannot be recovered.
        return Run(REFERENCE, reason="layout.json predates the crop_map schema")
    return Run(REFERENCE, path=tiles, extra={"layout": layout})


def find_mnm_baseline(outputs, set_folder, prefix):
    tiles = outputs / set_folder / f"{prefix}_baseline" / "tiles"
    if not tiles.is_dir():
        return Run("mnm_baseline", reason="no <prefix>_baseline/tiles/ folder")
    return Run("mnm_baseline", path=tiles)


def find_tiled_diffusion(outputs, set_folder, prefix):
    """outputs/tiled_diffusion/<set>/<config file name>_<prefix>/tiles/crop<ii>_cand<jj>.png."""
    base = outputs / "tiled_diffusion" / set_folder
    if not base.is_dir():
        return Run("tiled_diffusion", reason=f"no tiled_diffusion/{set_folder}/")
    candidates = sorted(p for p in base.glob(f"*{prefix}") if (p / "tiles").is_dir())
    if not candidates:
        return Run("tiled_diffusion", reason="no run folder matching the config prefix")
    run = candidates[-1]
    meta_file = run / "run_meta.json"
    meta = json.loads(meta_file.read_text()) if meta_file.is_file() else {}
    return Run("tiled_diffusion", path=run / "tiles", extra={"meta": meta})


def find_regional_prompting(outputs, set_folder, prefix):
    """outputs/regional_prompting/<set>/<prefix>/crop<i>/tile<j>.png, layout.json at the root."""
    run = outputs / "regional_prompting" / set_folder / prefix
    if not run.is_dir():
        return Run("regional_prompting", reason="no run folder named after the config prefix")
    layout_file = run / "layout.json"
    layout = json.loads(layout_file.read_text()) if layout_file.is_file() else {}
    return Run("regional_prompting", path=run, extra={"layout": layout})


def discover(root, config_dir, outputs_dir):
    configs = root / config_dir
    outputs = root / outputs_dir
    entries, problems = [], []
    for set_name in SETS:
        set_folder = f"configs_for_baseline_{set_name}"
        folder = configs / set_folder
        if not folder.is_dir():
            problems.append(f"{set_folder}: no config folder at {folder}")
            continue
        for config_path in sorted(folder.glob("config_*.json"), key=natural_key):
            try:
                config = json.loads(config_path.read_text())
            except json.JSONDecodeError as error:
                problems.append(f"{config_path.name}: unreadable ({error})")
                continue
            if "prefix" not in config or "crops" not in config:
                problems.append(f"{config_path.name}: not a generation config")
                continue
            prefix = config["prefix"]
            runs = {
                REFERENCE: find_mix_n_match(outputs, set_folder, prefix),
                "mnm_baseline": find_mnm_baseline(outputs, set_folder, prefix),
                "tiled_diffusion": find_tiled_diffusion(outputs, set_folder, prefix),
                "regional_prompting": find_regional_prompting(outputs, set_folder, prefix),
            }
            entry = Entry(set_name, config_path, config, runs)
            if runs[REFERENCE].ok:
                entry.layout = runs[REFERENCE].extra["layout"]
                entry.regions = crop_regions(entry.layout["crop_map"], entry.layout["num_crops"])
            entries.append(entry)
    return entries, problems


def natural_key(path):
    digits = "".join(c for c in path.stem if c.isdigit())
    return (int(digits) if digits else 0, path.stem)


# =========================================================================== #
# geometry
# =========================================================================== #

def crop_regions(crop_map, num_crops):
    """
    Mirror of Mix_n_match/vis_app.py: crop_regions.
    crop_map is one entry per 16x16 patch, so it upscales by PATCH to reach pixels.
    """
    pixel_map = np.asarray(crop_map).repeat(PATCH, axis=0).repeat(PATCH, axis=1)
    regions = []
    for crop_index in range(num_crops):
        rows, cols = np.nonzero(pixel_map == crop_index)
        if len(rows) == 0:
            regions.append(None)
            continue
        box = (int(cols.min()), int(rows.min()), int(cols.max()) + 1, int(rows.max()) + 1)
        inside = pixel_map[box[1]:box[3], box[0]:box[2]] == crop_index
        regions.append((box, Image.fromarray(inside.astype(np.uint8) * 255)))
    return regions


def rect_box(crop):
    return (crop["x"], crop["y"], crop["x"] + crop["width"], crop["y"] + crop["height"])


def rects_tile_canvas(entry):
    """True when the config's rectangles cover every pixel exactly once."""
    width, height = entry.size
    covered = np.zeros((height, width), dtype=np.int16)
    for crop in entry.config["crops"]:
        left, top, right, bottom = rect_box(crop)
        covered[top:bottom, left:right] += 1
    return bool((covered == 1).all())


def is_vertical_band_layout(entry):
    """
    Tiled Diffusion seam-constrains its crops as a vertical chain, so pasting its
    per-crop images back into the canvas is only faithful when the config's crops are
    full-width bands stacked top to bottom.
    """
    width, height = entry.size
    crops = entry.config["crops"]
    if not all(c["x"] == 0 and c["width"] == width for c in crops):
        return False
    spans = sorted((c["y"], c["y"] + c["height"]) for c in crops)
    return (spans[0][0] == 0 and spans[-1][1] == height
            and all(spans[i][1] == spans[i + 1][0] for i in range(len(spans) - 1)))


def background_index(entry):
    """Index of Mix-n-match's background crop in its crop_map, or None."""
    return entry.layout.get("background_crop") if entry.layout else None


def region_descriptor(entry, crop_index):
    """
    Where this crop sits on the whole canvas, normalised to 0..1, so the page can draw a
    thumbnail of the layout and show the participant which part they are judging.
    """
    width, height = entry.size
    if entry.regions and crop_index < len(entry.regions) and entry.regions[crop_index]:
        left, top, right, bottom = entry.regions[crop_index][0]
    else:
        left, top, right, bottom = rect_box(entry.config["crops"][crop_index])
    return {"x": round(left / width, 4), "y": round(top / height, 4),
            "w": round((right - left) / width, 4), "h": round((bottom - top) / height, 4),
            "ar": round(width / height, 4)}


def target_box(entry, crop_index):
    """
    The display box a tile item is rendered into: Mix-n-match's own region bounding box
    when we have it, else the config rectangle. Every method in the item shares it.
    """
    if entry.regions and crop_index < len(entry.regions) and entry.regions[crop_index]:
        box = entry.regions[crop_index][0]
        return box[2] - box[0], box[3] - box[1]
    crop = entry.config["crops"][crop_index]
    return crop["width"], crop["height"]


# =========================================================================== #
# image sources
# =========================================================================== #

def open_rgba(path):
    with Image.open(path) as image:
        return image.convert("RGBA")


def tile_source(entry, method, crop_index, tile_index):
    """The raw file a method offers for (crop, tile), or None if it is not on disk."""
    run = entry.runs[method]
    if not run.ok:
        return None
    if method == REFERENCE:
        name = "cropBg" if crop_index == background_index(entry) else f"crop{crop_index}"
        path = run.path / name / f"tile{tile_index}.png"
    elif method == "mnm_baseline":
        path = run.path / f"crop{crop_index}" / f"tile{tile_index}.png"
    elif method == "regional_prompting":
        folder = run.path / f"crop{crop_index}"
        if not folder.is_dir():                      # the appended background region
            folder = run.path / "cropBg"
        path = folder / f"tile{tile_index}.png"
    else:                                            # tiled_diffusion
        path = run.path / f"crop{crop_index:02d}_cand{tile_index:02d}.png"
    return path if path.is_file() else None


def regional_background(entry):
    """RP's background region folder once it exists, else None."""
    run = entry.runs["regional_prompting"]
    if not run.ok:
        return None
    for name in ("cropBg", f"crop{entry.num_crops}"):
        folder = run.path / name
        if folder.is_dir() and any(folder.glob("tile*.png")):
            return folder
    return None


# =========================================================================== #
# rendering
# =========================================================================== #

def fit_into(image, box, mat):
    """
    Scale `image` to fit inside `box` without distorting it and centre it on a neutral
    mat of exactly `box`. A whole-canvas image therefore stays square, which is what we
    want: the baselines are not pretending to have produced a region-shaped result.
    """
    target_w, target_h = box
    canvas = Image.new("RGB", (target_w, target_h), mat)
    scale = min(target_w / image.width, target_h / image.height)
    size = (max(1, round(image.width * scale)), max(1, round(image.height * scale)))
    resized = image.resize(size, Image.LANCZOS)
    flat = Image.new("RGB", size, mat)
    flat.paste(resized, (0, 0), resized if resized.mode == "RGBA" else None)
    canvas.paste(flat, ((target_w - size[0]) // 2, (target_h - size[1]) // 2))
    return canvas


def render_tile(entry, method, crop_index, tile_index, mat):
    source = tile_source(entry, method, crop_index, tile_index)
    if source is None:
        return None
    return fit_into(open_rgba(source), target_box(entry, crop_index), mat)


def render_composite(entry, method, combination, mat):
    """
    One whole image per method, using the same tile index per crop for all of them.

    mix_n_match        pastes each region tile at its own bounding box through its alpha
    regional_prompting pastes its background region, then each config rectangle on top
    mnm_baseline       cuts each config rectangle out of that prompt's whole-canvas image
    tiled_diffusion    the same cut-and-paste; only faithful for a vertical-band layout
    """
    width, height = entry.size
    canvas = Image.new("RGB", (width, height), mat)

    if method == REFERENCE:
        background = background_index(entry)
        for crop_index, region in enumerate(entry.regions):
            if region is None:
                continue
            tile_index = 0 if crop_index == background else combination[crop_index]
            source = tile_source(entry, method, crop_index, tile_index)
            if source is None:
                return None
            tile = open_rgba(source)
            canvas.paste(tile, region[0][:2], tile)
        return canvas

    if method == "regional_prompting":
        folder = regional_background(entry)
        if folder is not None:
            path = folder / f"tile{combination[0]}.png"
            if not path.is_file():
                available = sorted(folder.glob("tile*.png"))
                if not available:
                    return None
                path = available[0]
            background_tile = open_rgba(path)
            canvas.paste(background_tile, (0, 0), background_tile)
        for crop_index, crop in enumerate(entry.config["crops"]):
            source = tile_source(entry, method, crop_index, combination[crop_index])
            if source is None:
                return None
            canvas.paste(open_rgba(source).convert("RGB"), (crop["x"], crop["y"]))
        return canvas

    # mnm_baseline and tiled_diffusion: whole-canvas images cut down to the rectangle.
    for crop_index, crop in enumerate(entry.config["crops"]):
        source = tile_source(entry, method, crop_index, combination[crop_index])
        if source is None:
            return None
        whole = open_rgba(source).convert("RGB")
        if whole.size != (width, height):
            whole = whole.resize((width, height), Image.LANCZOS)
        canvas.paste(whole.crop(rect_box(crop)), (crop["x"], crop["y"]))
    return canvas


# =========================================================================== #
# capability: what each method can do for this config, and why not
# =========================================================================== #

def tile_capable(entry, method, settings):
    if not entry.runs[method].ok:
        return False, entry.runs[method].reason
    if entry.set_name not in settings.get(method, {}).get("tile_sets", list(SETS)):
        return False, f"tile items disabled for the {entry.set_name} set"
    if tile_source(entry, method, 0, 0) is None:
        return False, "run folder has no crop0/tile0 image"
    return True, ""


def composite_capable(entry, method, settings, allow_approximate):
    if not entry.runs[method].ok:
        return False, entry.runs[method].reason
    if entry.set_name not in settings.get(method, {}).get("composite_sets", list(SETS)):
        return False, f"composites disabled for the {entry.set_name} set"

    if method == REFERENCE:
        # The crop map always covers the canvas, background crop included.
        return True, ""

    tiles_canvas = rects_tile_canvas(entry)
    if method == "regional_prompting":
        if not tiles_canvas and regional_background(entry) is None:
            return False, ("config rectangles leave gaps and no background region is saved "
                           "(re-run with the background-region fix)")
        return True, ""

    if method == "mnm_baseline":
        if not tiles_canvas:
            return False, ("config rectangles leave gaps and the naive baseline generates no "
                           "background image, so the composite would have holes")
        return True, ""

    # tiled_diffusion
    if not tiles_canvas:
        return False, "config rectangles leave gaps, so the stitched canvas would have holes"
    if not is_vertical_band_layout(entry) and not allow_approximate:
        return False, ("crops are not full-width bands, so the vertical seam chain does not "
                       "correspond to this layout")
    return True, ""


# =========================================================================== #
# asset encoding
# =========================================================================== #

class Assets:
    """Content-hashed WebP writer. Identical renders are encoded once."""

    def __init__(self, out, settings, dry_run):
        self.out = out
        self.settings = settings
        self.dry_run = dry_run
        self.seen = {}
        self.bytes_written = 0

    def add(self, image, kind):
        limit = self.settings["tile_max_side" if kind == "tile" else "composite_max_side"]
        if max(image.size) > limit:
            scale = limit / max(image.size)
            image = image.resize((max(1, round(image.width * scale)),
                                  max(1, round(image.height * scale))), Image.LANCZOS)
        digest = hashlib.sha1(image.tobytes() + repr(image.size).encode()).hexdigest()[:16]
        relative = f"assets/{kind}/{digest}.webp"
        if relative not in self.seen:
            path = self.out / relative
            if not self.dry_run:
                path.parent.mkdir(parents=True, exist_ok=True)
                image.save(path, "WEBP", quality=self.settings["quality"], method=5)
                self.bytes_written += path.stat().st_size
            self.seen[relative] = image.size
        return {"src": relative, "w": image.size[0], "h": image.size[1]}


# =========================================================================== #
# item bank
# =========================================================================== #

QUESTIONS = {
    "tile_ab_adherence": "Which picture better matches the description?",
    "tile_ab_quality": "Which picture looks better made — sharper, more natural, fewer artefacts?",
    "comp_ab_coherence": "Which one looks more like a single, natural picture rather than "
                         "separate pieces put together?",
    "comp_ab_adherence": "Which one shows all of the described parts better?",
    "tile_bw4": "Which picture matches the description best, and which matches it worst?",
    "comp_bw4": "Which whole picture is best overall, and which is worst?",
    "attention": "Which picture better matches the description?",
}


def spread(count, wanted, rng):
    """`wanted` distinct values from range(count), spread out rather than clustered."""
    if count <= 0:
        return []
    values = list(range(count))
    rng.shuffle(values)
    return values[:wanted] if wanted <= count else [values[i % count] for i in range(wanted)]


def real_crops(entry):
    """Crop indices that carry per-crop prompts (the background crop has none)."""
    background = background_index(entry)
    return [i for i in range(entry.num_crops) if i != background]


class Builder:
    def __init__(self, entries, config, out, dry_run):
        self.entries = entries
        self.config = config
        self.assets = Assets(out, config["assets"], dry_run)
        self.mat = tuple(config["assets"]["background"])
        self.rng = random.Random(config["seed"])
        self.items = []
        self.report = defaultdict(list)
        self.counter = 0

    # -- helpers ---------------------------------------------------------- #

    def next_id(self, kind):
        self.counter += 1
        return f"{kind[0]}{self.counter:04d}"

    def note(self, entry, method, level, reason):
        self.report[f"{entry.set_name}/{entry.prefix}"].append(
            {"method": method, "level": level, "skipped": reason})

    def emit(self, entry, kind, options, **fields):
        """options: list of (method, PIL image). Shuffled here so M1 is not always first."""
        rendered = []
        for method, image in options:
            asset = self.assets.add(image, "tile" if kind.startswith(("tile", "attention")) else "comp")
            rendered.append({"m": METHOD_CODE[method], **asset})
        order = list(range(len(rendered)))
        self.rng.shuffle(order)
        item = {
            "id": self.next_id(kind),
            "type": kind,
            "level": "comp" if kind.startswith("comp") else "tile",
            "question": QUESTIONS[kind],
            "set": entry.set_name,
            "config": entry.prefix,
            "num_crops": entry.num_crops,
            "tiles_per_crop": entry.tiles_per_crop,
            "options": [rendered[i] for i in order],
            **fields,
        }
        self.items.append(item)
        return item

    # -- tile items ------------------------------------------------------- #

    def build_tile_pairs(self, entry, capable):
        """
        Every baseline is compared against the reference on the *same* tile: same
        config, same crop, same tile index, so the prompt is identical across the
        three pairs. That is what makes the comparison fair, and it is also what
        lets the analysis pair the three outcomes for a McNemar contrast.
        """
        quotas = self.config["quotas"]
        crops = real_crops(entry)
        if not crops:
            return
        for kind in ("tile_ab_adherence", "tile_ab_quality"):
            for slot in range(quotas.get(kind, 0)):
                crop_index = crops[slot % len(crops)]
                tile_index = self.rng.randrange(entry.tiles_per_crop)
                ours = render_tile(entry, REFERENCE, crop_index, tile_index, self.mat)
                if ours is None:
                    continue
                for baseline in BASELINES:
                    if baseline not in capable:
                        continue
                    theirs = render_tile(entry, baseline, crop_index, tile_index, self.mat)
                    if theirs is None:
                        continue
                    self.emit(entry, kind, [(REFERENCE, ours), (baseline, theirs)],
                              crop_index=crop_index, tile_index=tile_index,
                              region=region_descriptor(entry, crop_index),
                              prompt=entry.prompt(crop_index, tile_index))

    def build_tile_bw4(self, entry, capable):
        if len(capable) < len(BASELINES) or not self.config["quotas"].get("tile_bw4"):
            return
        crops = real_crops(entry)
        if not crops:
            return
        crop_index = self.rng.choice(crops)
        tile_index = self.rng.randrange(entry.tiles_per_crop)
        options = []
        for method in ALL_METHODS:
            image = render_tile(entry, method, crop_index, tile_index, self.mat)
            if image is None:
                return
            options.append((method, image))
        self.emit(entry, "tile_bw4", options, crop_index=crop_index, tile_index=tile_index,
                  region=region_descriptor(entry, crop_index),
                  prompt=entry.prompt(crop_index, tile_index))

    # -- composite items -------------------------------------------------- #

    def combination(self, entry):
        return [self.rng.randrange(entry.tiles_per_crop) for _ in range(entry.num_crops)]

    def composite_prompts(self, entry, combination):
        crops = real_crops(entry)
        return [entry.prompt(i, combination[i]) for i in crops]

    def build_composite_pairs(self, entry, capable, flags):
        quotas = self.config["quotas"]
        for kind in ("comp_ab_coherence", "comp_ab_adherence"):
            for _ in range(quotas.get(kind, 0)):
                # One combination of tiles per slot, shared by every baseline, so the
                # three pairs show the same set of descriptions.
                combination = self.combination(entry)
                ours = render_composite(entry, REFERENCE, combination, self.mat)
                if ours is None:
                    continue
                for baseline in BASELINES:
                    if baseline not in capable:
                        continue
                    theirs = render_composite(entry, baseline, combination, self.mat)
                    if theirs is None:
                        continue
                    self.emit(entry, kind, [(REFERENCE, ours), (baseline, theirs)],
                              combination=combination,
                              background_prompt=entry.config["background_prompt"],
                              prompts=self.composite_prompts(entry, combination),
                              flags={k: v for k, v in flags.items()
                                     if k == METHOD_CODE[baseline]})

    def build_composite_bw4(self, entry, capable, flags):
        if len(capable) < len(BASELINES) or not self.config["quotas"].get("comp_bw4"):
            return
        combination = self.combination(entry)
        options = []
        for method in ALL_METHODS:
            image = render_composite(entry, method, combination, self.mat)
            if image is None:
                return
            options.append((method, image))
        self.emit(entry, "comp_bw4", options, combination=combination,
                  background_prompt=entry.config["background_prompt"],
                  prompts=self.composite_prompts(entry, combination), flags=flags)

    # -- attention checks ------------------------------------------------- #

    def build_attention(self, entries):
        """
        An ordinary-looking adherence item whose second picture is a region from an
        unrelated config, so the described thing plainly is not in it. The expected
        answer lives in legend.json, never in the served manifest.
        """
        wanted = self.config.get("attention_checks", 0)
        usable = [e for e in entries if e.runs[REFERENCE].ok and real_crops(e)]
        expected = {}
        if wanted <= 0 or len(usable) < 2:
            return expected
        for slot in range(wanted):
            entry = usable[slot % len(usable)]
            other = usable[(slot + 1 + slot // len(usable)) % len(usable)]
            if other.prefix == entry.prefix:
                continue
            crop_index = self.rng.choice(real_crops(entry))
            tile_index = self.rng.randrange(entry.tiles_per_crop)
            decoy_crop = self.rng.choice(real_crops(other))
            decoy_tile = self.rng.randrange(other.tiles_per_crop)

            correct = render_tile(entry, REFERENCE, crop_index, tile_index, self.mat)
            decoy = render_tile(other, REFERENCE, decoy_crop, decoy_tile, self.mat)
            if correct is None or decoy is None:
                continue
            decoy = fit_into(decoy, correct.size, self.mat)
            item = self.emit(entry, "attention",
                             [(REFERENCE, correct), (REFERENCE, decoy)],
                             crop_index=crop_index, tile_index=tile_index,
                             region=region_descriptor(entry, crop_index),
                             prompt=entry.prompt(crop_index, tile_index))
            # emit() shuffled the options; find where the correct render landed.
            correct_asset = self.assets.add(correct, "tile")["src"]
            expected[item["id"]] = next(i for i, option in enumerate(item["options"])
                                        if option["src"] == correct_asset)
        return expected

    # -- driver ----------------------------------------------------------- #

    def run(self):
        settings = self.config["methods"]
        allow_approximate = self.config["allow_approximate_stitching"]
        for entry in self.entries:
            if not entry.runs[REFERENCE].ok:
                self.note(entry, REFERENCE, "all", entry.runs[REFERENCE].reason)
                continue

            tile_capable_methods, composite_capable_methods, flags = [], [], {}
            for baseline in BASELINES:
                ok, reason = tile_capable(entry, baseline, settings)
                if ok:
                    tile_capable_methods.append(baseline)
                else:
                    self.note(entry, baseline, "tile", reason)
                ok, reason = composite_capable(entry, baseline, settings, allow_approximate)
                if ok:
                    composite_capable_methods.append(baseline)
                else:
                    self.note(entry, baseline, "composite", reason)

            if "tiled_diffusion" in composite_capable_methods:
                # Keyed by code, not name: the manifest is served to participants.
                flags[METHOD_CODE["tiled_diffusion"]] = {
                    "stitch_faithful": is_vertical_band_layout(entry)}

            self.build_tile_pairs(entry, tile_capable_methods)
            self.build_tile_bw4(entry, tile_capable_methods)
            ok, reason = composite_capable(entry, REFERENCE, settings, allow_approximate)
            if ok:
                self.build_composite_pairs(entry, composite_capable_methods, flags)
                self.build_composite_bw4(entry, composite_capable_methods, flags)
            else:
                self.note(entry, REFERENCE, "composite", reason)

        return self.build_attention(self.entries)


# =========================================================================== #

def summarise(items):
    by_type = Counter(item["type"] for item in items)
    by_set = Counter(item["set"] for item in items)
    pairs = Counter()
    for item in items:
        if item["type"].endswith(("adherence", "quality", "coherence")) and len(item["options"]) == 2:
            codes = {option["m"] for option in item["options"]}
            other = codes - {METHOD_CODE[REFERENCE]}
            if other:
                pairs[f"{METHOD_CODE[REFERENCE]} vs {other.pop()}"] += 1
    return {"total": len(items), "by_type": dict(by_type), "by_set": dict(by_set),
            "by_pair": dict(pairs)}


def main():
    parser = argparse.ArgumentParser(description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--root", type=Path, help="where outputs/ and configs_for_baseline/ live")
    parser.add_argument("--out", type=Path, default=Path("."), help="repo root to write into")
    parser.add_argument("--config", type=Path, default=Path("build/build_config.yaml"))
    parser.add_argument("--seed", type=int, help="override the item-bank seed")
    parser.add_argument("--dry-run", action="store_true",
                        help="report what would be built without writing images")
    parser.add_argument("--clean", action="store_true", help="delete assets/ before building")
    args = parser.parse_args()

    settings = yaml.safe_load(args.config.read_text())
    root = args.root or Path(settings.get("root", "."))
    if args.seed is not None:
        settings["seed"] = args.seed

    entries, problems = discover(root, settings["config_dir"], settings["outputs_dir"])
    print(f"root: {root.resolve()}")
    print(f"configs found: {len(entries)}"
          f"  ({Counter(e.set_name for e in entries)})")
    for problem in problems:
        print(f"  ! {problem}")
    if not entries:
        print("\nNothing to build. Put outputs/ and configs_for_baseline/ in place first.")
        return 1

    if args.clean and not args.dry_run:
        shutil.rmtree(args.out / "assets", ignore_errors=True)

    builder = Builder(entries, settings, args.out, args.dry_run)
    expected = builder.run()
    counts = summarise(builder.items)

    print("\nitem bank")
    for key, value in counts["by_type"].items():
        print(f"  {key:22s} {value}")
    print(f"  {'TOTAL':22s} {counts['total']}")
    print("\ncomparisons against the reference method")
    for key, value in counts["by_pair"].items():
        print(f"  {key:22s} {value}")

    skipped = sum(len(v) for v in builder.report.values())
    if skipped:
        print(f"\n{skipped} (config, method, level) combinations skipped "
              f"— see analysis/build_report.json")
        shown = Counter(note["skipped"] for notes in builder.report.values() for note in notes)
        for reason, count in shown.most_common(6):
            print(f"  {count:3d}x {reason}")

    if args.dry_run:
        print("\n--dry-run: nothing written")
        return 0

    manifest = {
        "version": 1,
        "built_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        # Marks a build made from the test fixture, so a preview cannot go live
        # looking like the real thing.
        "preview": "mock" in root.name.lower(),
        "seed": settings["seed"],
        "session": settings["session"],
        "counts": counts,
        "items": builder.items,
    }
    data = args.out / "data"
    data.mkdir(parents=True, exist_ok=True)
    (data / "manifest.json").write_text(json.dumps(manifest, separators=(",", ":")))

    # The key and the build log stay out of data/, which is what the page fetches.
    keys = args.out / "analysis"
    keys.mkdir(parents=True, exist_ok=True)
    (keys / "legend.json").write_text(json.dumps({
        "note": "Analysis key: which code was which method, and the attention-check "
                "answers. The study page never fetches this.",
        "methods": {code: method for method, code in METHOD_CODE.items()},
        "attention_expected": expected,
    }, indent=2))
    (keys / "build_report.json").write_text(json.dumps({
        "built_at": manifest["built_at"],
        "root": str(root.resolve()),
        "configs": len(entries),
        "problems": problems,
        "counts": counts,
        "skipped": dict(builder.report),
    }, indent=2))

    megabytes = builder.assets.bytes_written / 1_000_000
    print(f"\nwrote {len(builder.assets.seen)} images ({megabytes:.1f} MB) to {args.out}/assets/")
    print(f"wrote {args.out}/data/manifest.json")
    print(f"wrote {args.out}/analysis/legend.json, {args.out}/analysis/build_report.json")
    if megabytes > 900:
        print("  ! over 900 MB — GitHub Pages caps a published site near 1 GB. "
              "Reduce the quotas or the config set.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
