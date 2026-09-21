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
    assets/{comp,thumb}/*.webp

Source-of-truth for the on-disk formats (do not edit those repos):
    Mix_n_match/vis_app.py          save_separate_tiles, crop_regions, assemble_combination
    tiled-diffusion/crop_output.py  save_tiles, save_run_meta
    Regional-Prompting-FLUX/infer_pixeldit_regional.py  save_tiles, write_layout
"""

from __future__ import annotations

import argparse
import hashlib
import json
import math
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

# tiled-diffusion/crop_output.py: UNCOVERED_COLOUR.
UNCOVERED = (0, 0, 0)
SETS = ("static", "dynamic")
# A config set lives in configs_for_baseline_<name>/, and its runs in folders of the same name. <name> is a
# cropping kind of SETS, optionally followed by _<anything> (static_2, dynamic_fs): more configs of that kind.
SET_FOLDER_PREFIX = "configs_for_baseline_"
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


def set_folders(configs):
    """
    Every configs_for_baseline_* folder under the config root. The plain static and dynamic sets come first,
    in that order, so adding a set appends to the item bank rather than redrawing it.
    """
    found = {path.name for path in configs.glob(f"{SET_FOLDER_PREFIX}*") if path.is_dir()}
    first = [f"{SET_FOLDER_PREFIX}{kind}" for kind in SETS if f"{SET_FOLDER_PREFIX}{kind}" in found]
    return first + sorted(found - set(first))


def discover(root, config_dir, outputs_dir):
    configs = root / config_dir
    outputs = root / outputs_dir
    entries, problems = [], []
    folders = set_folders(configs)
    if not folders:
        problems.append(f"no {SET_FOLDER_PREFIX}* config folder under {configs}")
    for set_folder in folders:
        folder = configs / set_folder
        # The item's "set" is the cropping kind (analysis and composite_sets group by it); the folder is only
        # where the config and its runs are.
        set_name = set_folder[len(SET_FOLDER_PREFIX):].split("_")[0]
        if set_name not in SETS:
            problems.append(f"{set_folder}: the set name must start with one of {list(SETS)}")
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

    mix_n_match         pastes each region tile at its own bounding box through its alpha
    regional_prompting  pastes its background region, then each config rectangle on top
    mnm_baseline        its whole images side by side, unresized, in a near-square grid
                        (see gallery); empty cells get the mat
    tiled_diffusion     reproduces crop_output.compose_combination exactly, from
                        run_meta.json: its own canvas, each tile at its placed box
    """
    width, height = entry.size

    if method == REFERENCE:
        canvas = Image.new("RGB", (width, height), mat)
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
        canvas = Image.new("RGB", (width, height), UNCOVERED)
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

    if method == "mnm_baseline":
        images = []
        for crop_index in reading_order(entry):
            source = tile_source(entry, method, crop_index, combination[crop_index])
            if source is None:
                return None
            images.append(open_rgba(source).convert("RGB"))
        return gallery(images, mat)

    # tiled_diffusion
    places = tiled_diffusion_layout(entry)
    if not places:
        return None
    canvas_size = (places["canvas"][0], places["canvas"][1])
    canvas = Image.new("RGB", canvas_size, UNCOVERED)
    for place in places["crops"]:
        crop_index = place["config_index"]
        if crop_index >= len(combination):
            return None
        source = tile_source(entry, method, crop_index, combination[crop_index])
        if source is None:
            return None
        tile = open_rgba(source).convert("RGB")
        box = (place["placed_width"], place["placed_height"])
        if tile.size != box:
            tile = tile.resize(box, Image.LANCZOS)
        canvas.paste(tile, (place["placed_x"], place["placed_y"]))
    return canvas


def reading_order(entry):
    """Crop indices ordered by their config rectangles: top to bottom, then left to right."""
    crops = entry.config["crops"]
    return sorted(range(len(crops)), key=lambda index: (crops[index]["y"], crops[index]["x"]))


def gallery(images, mat):
    """
    The naive baseline's whole images, each kept intact and unresized, in a near-square
    grid of ceil(sqrt(n)) columns, then the whole grid halved.

    The baseline generates one plain image per prompt and knows nothing of the layout,
    so squeezing those images into the config's rectangles would distort them and leave
    black wherever the rectangles do not reach. Shown whole, the only thing it can lose
    on is what it really lacks: composition. Near-square rather than a strip, so that
    no image is lost to a scorer's centre crop. Cells the images do not fill (3, 5, 7
    or 8 crops) get the mat: no picture there, rather than a black part of one.
    """
    columns = math.ceil(math.sqrt(len(images)))
    rows = math.ceil(len(images) / columns)
    cell_w = max(image.width for image in images)
    cell_h = max(image.height for image in images)
    canvas = Image.new("RGB", (columns * cell_w, rows * cell_h), mat)
    for index, image in enumerate(images):
        row, column = divmod(index, columns)
        canvas.paste(image, (column * cell_w + (cell_w - image.width) // 2,
                             row * cell_h + (cell_h - image.height) // 2))
    return canvas.resize((canvas.width // 2, canvas.height // 2), Image.LANCZOS)


def tiled_diffusion_layout(entry):
    """
    Where Tiled Diffusion put each crop, straight from its own run_meta.json.

    crop_layout.plan_layout with FORCE_SQUARE_TILE_SIZE ignores the config's boxes
    entirely: every tile is generated as one square and the crops are chained in the
    order they are listed, so the canvas is <size> wide by <size * num_crops> tall
    whatever the config's geometry. run_meta.json records the result, so this reads
    it rather than re-deriving it. The fallback rebuilds that same stack when a run
    predates the metadata.
    """
    run = entry.runs["tiled_diffusion"]
    if not run.ok:
        return None
    meta = run.extra.get("meta") or {}
    crops = meta.get("crops")
    if crops and meta.get("image_width") and meta.get("image_height"):
        return {"canvas": (meta["image_width"], meta["image_height"]),
                "crops": sorted(crops, key=lambda c: c.get("chain_position", 0)),
                "source": "run_meta.json"}

    first = tile_source(entry, "tiled_diffusion", 0, 0)
    if first is None:
        return None
    with Image.open(first) as probe:
        side = max(probe.size)
    count = entry.num_crops
    return {"canvas": (side, side * count),
            "crops": [{"config_index": i, "chain_position": i,
                       "placed_x": 0, "placed_y": i * side,
                       "placed_width": side, "placed_height": side}
                      for i in range(count)],
            "source": "reconstructed stack (no run_meta.json)"}


# =========================================================================== #
# capability: what each method can do for this config, and why not
# =========================================================================== #

def composite_capable(entry, method, settings, allow_approximate):
    """Can this method's whole image be assembled for this config, and if not, why not."""
    if not entry.runs[method].ok:
        return False, entry.runs[method].reason
    if entry.set_name not in settings.get(method, {}).get("composite_sets", list(SETS)):
        return False, f"composites disabled for the {entry.set_name} set"

    if method == REFERENCE:
        # The crop map always covers the canvas, background crop included.
        return True, ""

    if method == "regional_prompting":
        if not rects_tile_canvas(entry) and regional_background(entry) is None:
            return False, ("config rectangles leave gaps and no background region is saved "
                           "(re-run with the background-region fix)")
        return True, ""

    if method == "mnm_baseline":
        # A grid of its whole images, independent of the config's geometry.
        return True, ""

    # tiled_diffusion: its own chain layout, independent of the config's geometry.
    if tiled_diffusion_layout(entry) is None:
        return False, "no tiles and no run_meta.json to place them by"
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

    def encode(self, image, width, folder):
        """Write `image` at exactly `width` pixels across, keeping its aspect ratio."""
        if image.width != width:
            height = max(1, round(image.height * width / image.width))
            image = image.resize((width, height), Image.LANCZOS)
        digest = hashlib.sha1(image.tobytes() + repr(image.size).encode()).hexdigest()[:16]
        relative = f"assets/{folder}/{digest}.webp"
        if relative not in self.seen:
            path = self.out / relative
            if not self.dry_run:
                path.parent.mkdir(parents=True, exist_ok=True)
                image.save(path, "WEBP", quality=self.settings["quality"], method=5)
                self.bytes_written += path.stat().st_size
            self.seen[relative] = image.size
        return relative, image.size

    def add(self, image):
        """
        One composite, at full size for the enlarged view and as a thumbnail for the grid.

        Every picture is encoded at the same width, so the comparison is at one scale.
        Height is left free: a Tiled Diffusion stack is genuinely taller than a square
        canvas and is shown that way rather than squashed.
        """
        src, (width, height) = self.encode(image, self.settings["composite_width"], "comp")
        thumb, (thumb_width, _) = self.encode(image, self.settings["thumb_width"], "thumb")
        return {"src": src, "w": width, "h": height, "thumb": thumb, "tw": thumb_width}


# =========================================================================== #
# item bank
# =========================================================================== #

# One question, four criteria, each judged on a whole set of images rather than on one picture. Every
# screen in the study is this and nothing else.
CRITERIA = [
    {"id": "overall",
     "label": "Overall quality",
     "question": "Which set of images looks better overall?",
     "hint": "Judge each set as a whole, taking all of its images together: detail, colour, "
             "and anything that looks wrong, blurry or broken."},
    {"id": "seamless",
     "label": "Seamlessness",
     "question": "Which set blends together more seamlessly?",
     "hint": "Across the images in each set, look at where the parts meet. Are there visible "
             "joins, hard edges, or abrupt changes in texture where one region ends and the "
             "next begins?"},
    {"id": "coherence",
     "label": "Overall coherence",
     "question": "In which set do the images make more sense as single scenes?",
     "hint": "Ignore the joins themselves and the picture quality. Across each set, ask "
             "whether the parts of each image belong together: consistent lighting, scale "
             "and perspective, and a scene that holds together rather than unrelated things "
             "placed side by side."},
    {"id": "alignment",
     "label": "Prompt alignment",
     "question": "Which set matches its descriptions better?",
     "hint": "Each numbered image has its own descriptions, the same for that number in both "
             "sets. Every description should be visible somewhere in its image."},
]


def real_crops(entry):
    """Crop indices that carry per-crop prompts (the background crop has none)."""
    background = background_index(entry)
    return [i for i in range(entry.num_crops) if i != background]


class Builder:
    """
    Builds the item bank: a set of our composites against the matching set of a single
    baseline's, judged on four criteria. Nothing else.
    """

    def __init__(self, entries, config, out, dry_run):
        self.entries = entries
        self.config = config
        self.assets = Assets(out, config["assets"], dry_run)
        self.mat = tuple(config["assets"]["background"])
        self.per_method = config["combinations_per_method"]
        self.rng = random.Random(config["seed"])
        self.items = []
        self.report = defaultdict(list)
        self.counter = 0

    def note(self, entry, method, reason):
        self.report[f"{entry.set_name}/{entry.prefix}"].append(
            {"method": method, "skipped": reason})

    def combinations(self, entry):
        """
        Up to `combinations_per_method` distinct tile combinations, one tile index per crop. Distinct over
        the crops that carry prompts, so no two images in a set illustrate the same descriptions; the
        background crop's index only matters to some methods and is drawn freely.
        """
        crops = real_crops(entry)
        possible = entry.tiles_per_crop ** len(crops)
        wanted = min(self.per_method, possible)
        chosen, seen = [], set()
        while len(chosen) < wanted:
            combination = [self.rng.randrange(entry.tiles_per_crop) for _ in range(entry.num_crops)]
            key = tuple(combination[i] for i in crops)
            if key not in seen:
                seen.add(key)
                chosen.append(combination)
        return chosen

    def prompts(self, entry, combination):
        return [entry.prompt(i, combination[i]) for i in real_crops(entry)]

    def render_set(self, entry, method, combinations):
        """One composite per combination, in the given order, or None if any of them cannot be made."""
        images = [render_composite(entry, method, combination, self.mat) for combination in combinations]
        return None if any(image is None for image in images) else images

    def emit(self, entry, baseline, ours, theirs, combinations, flags):
        """
        One comparison of two sets. Image i of each set is the same combination i, so the two sets
        illustrate exactly the same descriptions in the same order. Which set is shown first is decided
        here and again per participant, so neither method sits on a fixed side.
        """
        self.counter += 1
        options = [(REFERENCE, ours), (baseline, theirs)]
        self.rng.shuffle(options)
        self.items.append({
            "id": f"q{self.counter:04d}",
            "set": entry.set_name,
            "config": entry.prefix,
            "seed": entry.config.get("seed"),
            "num_crops": entry.num_crops,
            "tiles_per_crop": entry.tiles_per_crop,
            "combinations": combinations,
            "background_prompt": entry.config["background_prompt"],
            "prompts": [self.prompts(entry, combination) for combination in combinations],
            "options": [{"m": METHOD_CODE[method], "images": [self.assets.add(image) for image in images]}
                        for method, images in options],
            "flags": flags,
        })

    def run(self):
        settings = self.config["methods"]
        allow_approximate = self.config["allow_approximate_stitching"]
        pairs_per_config = self.config["quotas"]["pairs_per_baseline"]

        for entry in self.entries:
            if not entry.runs[REFERENCE].ok:
                self.note(entry, REFERENCE, entry.runs[REFERENCE].reason)
                continue
            ok, reason = composite_capable(entry, REFERENCE, settings, allow_approximate)
            if not ok:
                self.note(entry, REFERENCE, reason)
                continue

            usable = []
            for baseline in BASELINES:
                ok, reason = composite_capable(entry, baseline, settings, allow_approximate)
                if ok:
                    usable.append(baseline)
                else:
                    self.note(entry, baseline, reason)
            if not usable:
                continue

            flags = {}
            layout = tiled_diffusion_layout(entry)
            if "tiled_diffusion" in usable and layout:
                flags[METHOD_CODE["tiled_diffusion"]] = {
                    "canvas": list(layout["canvas"]), "from": layout["source"]}

            for _ in range(pairs_per_config):
                # One list of tile combinations per round, shared by every method in the same order, so
                # every comparison in that round shows the same descriptions image for image.
                combinations = self.combinations(entry)
                if len(combinations) < self.per_method:
                    self.note(entry, REFERENCE, f"only {len(combinations)} distinct combinations exist; "
                                                f"showing {len(combinations)} per method")
                ours = self.render_set(entry, REFERENCE, combinations)
                if ours is None:
                    self.note(entry, REFERENCE, "a tile referenced by the layout is missing")
                    break
                for baseline in usable:
                    theirs = self.render_set(entry, baseline, combinations)
                    if theirs is None:
                        self.note(entry, baseline, "a tile the composite needs is missing")
                        continue
                    self.emit(entry, baseline, ours, theirs, combinations, flags)


def summarise(items):
    by_set = Counter(item["set"] for item in items)
    pairs = Counter()
    for item in items:
        other = {option["m"] for option in item["options"]} - {METHOD_CODE[REFERENCE]}
        if other:
            pairs[f"{METHOD_CODE[REFERENCE]} vs {other.pop()}"] += 1
    return {"total": len(items), "by_set": dict(by_set), "by_pair": dict(pairs),
            "configs": len({item["config"] for item in items})}


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
    settings.setdefault("combinations_per_method", 4)
    if settings["combinations_per_method"] < 1:
        parser.error("combinations_per_method must be at least 1")
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
    builder.run()
    counts = summarise(builder.items)

    print(f"\nitem bank: {counts['total']} comparisons over {counts['configs']} configs, "
          f"{settings['combinations_per_method']} combinations per method")
    for key, value in sorted(counts["by_set"].items()):
        print(f"  {key + ' set':22s} {value}")
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
        "version": 2,
        "built_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        # Marks a build made from the test fixture, so a preview cannot go live
        # looking like the real thing.
        "preview": "mock" in root.name.lower(),
        "seed": settings["seed"],
        "session": settings["session"],
        "reference_code": METHOD_CODE[REFERENCE],
        "combinations_per_method": settings["combinations_per_method"],
        "criteria": CRITERIA,
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
        "note": "Analysis key: which code was which method. The study page never "
                "fetches this.",
        "methods": {code: method for method, code in METHOD_CODE.items()},
        "criteria": [criterion["id"] for criterion in CRITERIA],
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
