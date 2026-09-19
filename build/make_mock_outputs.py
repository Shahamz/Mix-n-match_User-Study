#!/usr/bin/env python3
"""
Synthesise a complete four-method output tree so build_study.py can be developed and
tested before the real runs exist.

Every image is a flat colour with its method, crop and tile index drawn on it, so a
mis-wired item is obvious at a glance in the study page. The directory layout, file
names, alpha channels and JSON schemas mirror the real producers exactly:

  mix_n_match         Mix_n_match/vis_app.py: save_outputs / save_separate_tiles
  mnm_baseline        Mix_n_match/vis_app.py: save_baseline_run
  tiled_diffusion     tiled-diffusion/crop_output.py: save_tiles / save_run_meta
  regional_prompting  Regional-Prompting-FLUX/infer_pixeldit_regional.py: save_tiles / write_layout

Delete the output directory once the real outputs land.

    python build/make_mock_outputs.py --out mock_root
"""

from __future__ import annotations

import argparse
import json
import shutil
from pathlib import Path

import numpy as np
from PIL import Image, ImageDraw, ImageFont

CANVAS = 1024
PATCH = 16                      # Mix_n_match/macros.py: PATCH_SIZE_PIXELS
GRID = CANVAS // PATCH          # crop_map is GRID x GRID

# Distinct hues per method, so a wrongly-sourced image is visible immediately.
METHOD_HUE = {
    "mix_n_match": (86, 132, 196),
    "mnm_baseline": (196, 132, 86),
    "tiled_diffusion": (132, 176, 96),
    "regional_prompting": (172, 108, 176),
}

NEGATIVE = "low quality, worst quality"


# --------------------------------------------------------------------------- #
# mock configs
# --------------------------------------------------------------------------- #

def vertical_bands(heights):
    """Full-width bands stacked top to bottom, tiling the canvas exactly."""
    crops, y = [], 0
    for height in heights:
        crops.append({"x": 0, "y": y, "width": CANVAS, "height": height})
        y += height
    assert y == CANVAS, y
    return crops


MOCK_CONFIGS = {
    "static": [
        # (num_crops, tiles_per_crop, crops, background_prompt, subject words)
        (3, 3, vertical_bands([300, 460, 264]), "A tall lighthouse on a rocky shore", "lighthouse"),
        (2, 3, vertical_bands([512, 512]), "A desert highway at sunset", "highway"),
        (4, 2, [  # a 2x2 grid: tiles the canvas, but not as a vertical chain
            {"x": 0, "y": 0, "width": 512, "height": 512},
            {"x": 512, "y": 0, "width": 512, "height": 512},
            {"x": 0, "y": 512, "width": 512, "height": 512},
            {"x": 512, "y": 512, "width": 512, "height": 512},
        ], "A cluttered artist's studio", "studio"),
    ],
    "dynamic": [
        (2, 3, [  # sparse seed rectangles: ~45% coverage, the rest is background
            {"x": 220, "y": 90, "width": 560, "height": 300},
            {"x": 160, "y": 520, "width": 700, "height": 380},
        ], "A grand piano in an empty hall", "piano"),
        (3, 2, [
            {"x": 60, "y": 40, "width": 380, "height": 260},
            {"x": 540, "y": 120, "width": 400, "height": 300},
            {"x": 240, "y": 600, "width": 560, "height": 300},
        ], "A bustling night market", "market"),
    ],
    # An extra set of the dynamic kind, so the build's configs_for_baseline_* discovery is exercised.
    "dynamic_fs": [
        (2, 3, [
            {"x": 100, "y": 60, "width": 820, "height": 400},
            {"x": 200, "y": 560, "width": 600, "height": 400},
        ], "A quiet harbour at dawn", "harbour"),
    ],
}


def build_config(set_name, index, spec):
    num_crops, tiles_per_crop, crops, background_prompt, subject = spec
    prompts = [
        [f"{subject} region {i + 1}, variant {j + 1}" for j in range(tiles_per_crop)]
        for i in range(num_crops)
    ]
    return {
        "prefix": f"cfg45_{set_name}_config_{index}",
        "num_steps": 70,
        "seed": 2025,
        "cfg_scale": 4.5,
        "width": CANVAS,
        "height": CANVAS,
        "num_crops": num_crops,
        "tiles_per_crop": tiles_per_crop,
        "background_prompt": background_prompt,
        "background_negative_prompt": NEGATIVE,
        "tile_prompts": prompts,
        "tile_negative_prompts": [[NEGATIVE] * tiles_per_crop for _ in range(num_crops)],
        "late_prompting": 2,
        "attend_on_avg": True,
        "average_target": "keys_values",
        "crops": crops,
        "static_cropping": False,
        "dynamic_cropping": "MRF_alpha",
        "mode_filter": 3,
        "mrf_smoothness": 0.02,
        "saliency": False,
        "background_crop": True,
        "saliency_direction": "image_to_prompt",
        "saliency_word_reduction": "max",
        "saliency_crop_reduction": "mean",
        "per_crop_normalize": "min_max",
        "gaussian_sigma": 32,
        "group_patches_by_crop": True,
        "sequential_cfg": False,
        "visualize": False,
        "save_separate_tiles": True,
    }


# --------------------------------------------------------------------------- #
# imagery
# --------------------------------------------------------------------------- #

_FONT_CACHE: dict[int, ImageFont.ImageFont] = {}


def font(size):
    if size not in _FONT_CACHE:
        for candidate in ("DejaVuSans.ttf", "LiberationSans-Regular.ttf"):
            try:
                _FONT_CACHE[size] = ImageFont.truetype(candidate, size)
                break
            except OSError:
                continue
        else:
            _FONT_CACHE[size] = ImageFont.load_default()
    return _FONT_CACHE[size]


def shade(method, crop_index, tile_index):
    """A colour unique per (method, crop, tile) that keeps the method's hue. crop_index None = background."""
    base = np.array(METHOD_HUE[method], dtype=float)
    slot = 3 if crop_index is None else crop_index % 4
    factor = 0.62 + 0.11 * slot + 0.05 * (tile_index % 3)
    return tuple(int(v) for v in np.clip(base * factor + 40, 0, 255))


def paint(size, method, crop_index, tile_index, note=""):
    """A flat labelled panel; `size` is (width, height)."""
    image = Image.new("RGB", size, shade(method, crop_index, tile_index))
    draw = ImageDraw.Draw(image)
    label = "Bg" if crop_index is None else str(crop_index)
    lines = [method, f"crop {label}  tile {tile_index}"]
    if note:
        lines.append(note)
    scale = max(14, min(size) // 12)
    for row, line in enumerate(lines):
        draw.text((12, 12 + row * (scale + 6)), line, fill=(255, 255, 255), font=font(scale))
    draw.rectangle([0, 0, size[0] - 1, size[1] - 1], outline=(255, 255, 255), width=3)
    return image


# --------------------------------------------------------------------------- #
# crop maps (what MRF_alpha would have solved)
# --------------------------------------------------------------------------- #

def crop_map_for(config, wobble):
    """
    A GRID x GRID patch -> crop index map. Seeded from the config's rectangles, with a
    sinusoidal boundary wobble so dynamic runs get the irregular regions the real
    cropping modes produce. Everything a rectangle does not claim goes to the
    background crop, exactly as `background_crop: true` does.
    """
    num_crops = config["num_crops"]
    background_index = num_crops
    grid = np.full((GRID, GRID), background_index, dtype=int)
    rows, cols = np.meshgrid(np.arange(GRID), np.arange(GRID), indexing="ij")
    for crop_index, crop in enumerate(config["crops"]):
        top, left = crop["y"] // PATCH, crop["x"] // PATCH
        bottom = (crop["y"] + crop["height"]) // PATCH
        right = (crop["x"] + crop["width"]) // PATCH
        inside = (rows >= top) & (rows < bottom) & (cols >= left) & (cols < right)
        if wobble:
            edge = np.sin(cols * 0.55 + crop_index) * 2.4 + np.cos(rows * 0.4) * 1.6
            inside = (rows >= top + edge) & (rows < bottom + edge) & (cols >= left) & (cols < right)
        grid[inside] = crop_index
    return grid


def regions_from_crop_map(crop_map, num_crops):
    """Mirror of Mix_n_match/vis_app.py: crop_regions — bbox plus mask, in pixels."""
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


# --------------------------------------------------------------------------- #
# the four writers
# --------------------------------------------------------------------------- #

def write_mix_n_match(config, set_root, wobble):
    """vis_app.save_outputs: <prefix>_<mode>_<tag>/tiles/{layout.json,crop<i>/tile<j>.png}."""
    num_crops, tiles_per_crop = config["num_crops"], config["tiles_per_crop"]
    background_index = num_crops
    crop_map = crop_map_for(config, wobble)
    regions = regions_from_crop_map(crop_map, num_crops + 1)

    tag = f"k2_nCrops{num_crops}_lp2_avgKV"
    tiles_dir = set_root / f"{config['prefix']}_MRF_alpha_{tag}" / "tiles"
    tiles_dir.mkdir(parents=True, exist_ok=True)
    (tiles_dir / "layout.json").write_text(json.dumps({
        "width": CANVAS, "height": CANVAS,
        "tiles_per_crop": tiles_per_crop,
        "num_crops": num_crops + 1,          # crop_count() includes the background crop
        "background_crop": background_index,
        "crop_map": crop_map.tolist(),
    }))

    for crop_index, region in enumerate(regions):
        if region is None:
            continue
        box, mask = region
        size = (box[2] - box[0], box[3] - box[1])
        is_background = crop_index == background_index
        name = "cropBg" if is_background else f"crop{crop_index}"
        folder = tiles_dir / name
        folder.mkdir(exist_ok=True)
        for tile_index in range(1 if is_background else tiles_per_crop):
            panel = paint(size, "mix_n_match", None if is_background else crop_index, tile_index)
            tile = panel.convert("RGBA")
            tile.putalpha(mask)                       # vis_app.cut_tile
            tile.save(folder / f"tile{tile_index}.png")


def write_mnm_baseline(config, set_root):
    """vis_app.save_baseline_run: <prefix>_baseline/tiles/crop<i>/tile<j>.png, whole canvas, RGB."""
    tiles_dir = set_root / f"{config['prefix']}_baseline" / "tiles"
    for crop_index in range(config["num_crops"]):
        folder = tiles_dir / f"crop{crop_index}"
        folder.mkdir(parents=True, exist_ok=True)
        for tile_index in range(config["tiles_per_crop"]):
            paint((CANVAS, CANVAS), "mnm_baseline", crop_index, tile_index,
                  "whole canvas").save(folder / f"tile{tile_index}.png")


def write_tiled_diffusion(config, config_filename, out_root):
    """crop_output.save_tiles: <configname>_<prefix>/tiles/crop<ii>_cand<jj>.png, 1024^2 RGB."""
    run_dir = out_root / f"{config_filename}_{config['prefix']}"
    tiles_dir = run_dir / "tiles"
    tiles_dir.mkdir(parents=True, exist_ok=True)
    for crop_index in range(config["num_crops"]):
        for candidate in range(config["tiles_per_crop"]):
            paint((CANVAS, CANVAS), "tiled_diffusion", crop_index, candidate,
                  "whole canvas").save(tiles_dir / f"crop{crop_index:02d}_cand{candidate:02d}.png")
    (run_dir / "run_meta.json").write_text(json.dumps({
        "args": {"config": config_filename, "steps": 70, "seed": config["seed"],
                 "cfg_scale": config["cfg_scale"], "combos": False},
        "image_width": CANVAS, "image_height": CANVAS * config["num_crops"],
        "config_image_width": CANVAS, "config_image_height": CANVAS,
        "num_crops": config["num_crops"], "tiles_per_crop": config["tiles_per_crop"],
        "tile_prompts": config["tile_prompts"],
        "tile_negative_prompts": config["tile_negative_prompts"],
        "crops": [
            {"config_index": i, "chain_position": i,
             "x": c["x"], "y": c["y"], "width": c["width"], "height": c["height"],
             "generated_width": CANVAS, "generated_height": CANVAS,
             "placed_x": 0, "placed_y": i * CANVAS,
             "placed_width": CANVAS, "placed_height": CANVAS}
            for i, c in enumerate(config["crops"])
        ],
    }, indent=2))


def write_regional_prompting(config, out_root):
    """
    infer_pixeldit_regional.save_tiles: <prefix>/crop<i>/tile<j>.png, the config rectangle,
    RGB, no alpha; layout.json at the run-folder root.

    build_region_masks appends a background region wherever the rectangles leave the
    canvas uncovered, and that region is regenerated on every run — so unlike
    mix_n_match it gets `tiles_per_crop` background tiles, not one.
    """
    run_dir = out_root / config["prefix"]
    covered = np.zeros((CANVAS, CANVAS), dtype=bool)
    for crop_index, crop in enumerate(config["crops"]):
        folder = run_dir / f"crop{crop_index}"
        folder.mkdir(parents=True, exist_ok=True)
        covered[crop["y"]:crop["y"] + crop["height"], crop["x"]:crop["x"] + crop["width"]] = True
        for tile_index in range(config["tiles_per_crop"]):
            paint((crop["width"], crop["height"]), "regional_prompting",
                  crop_index, tile_index).save(folder / f"tile{tile_index}.png")

    has_background = bool((~covered).any())
    if has_background:
        folder = run_dir / "cropBg"
        folder.mkdir(parents=True, exist_ok=True)
        for tile_index in range(config["tiles_per_crop"]):
            panel = paint((CANVAS, CANVAS), "regional_prompting", None, tile_index, "background region")
            tile = panel.convert("RGBA")
            tile.putalpha(Image.fromarray((~covered).astype(np.uint8) * 255))
            tile.save(folder / f"tile{tile_index}.png")

    (run_dir / "layout.json").write_text(json.dumps({
        "prefix": config["prefix"],
        "num_crops": config["num_crops"], "tiles_per_crop": config["tiles_per_crop"],
        "width": CANVAS, "height": CANVAS, "seed": config["seed"],
        "cfg_scale": config["cfg_scale"], "num_steps": config["num_steps"],
        "background_prompt": config["background_prompt"],
        "background_region_added": has_background,
        "crops": config["crops"], "tile_prompts": config["tile_prompts"],
        "regional_prompting": {"mask_inject_steps": 4, "base_ratio": 0.3, "inject_blocks_interval": 1},
        "runs": "tiles", "combinations_saved": False,
    }, indent=2))


# --------------------------------------------------------------------------- #

def main():
    parser = argparse.ArgumentParser(description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--out", type=Path, default=Path("mock_root"))
    parser.add_argument("--force", action="store_true", help="delete an existing --out first")
    args = parser.parse_args()

    if args.out.exists():
        if not args.force:
            parser.error(f"{args.out} already exists; pass --force to replace it")
        shutil.rmtree(args.out)

    written = 0
    for set_name, specs in MOCK_CONFIGS.items():
        set_folder = f"configs_for_baseline_{set_name}"
        config_dir = args.out / "configs_for_baseline" / set_folder
        config_dir.mkdir(parents=True, exist_ok=True)
        mnm_root = args.out / "outputs" / set_folder
        td_root = args.out / "outputs" / "tiled_diffusion" / set_folder
        rp_root = args.out / "outputs" / "regional_prompting" / set_folder

        for index, spec in enumerate(specs, start=1):
            config = build_config(set_name, index, spec)
            filename = f"config_{index}"
            (config_dir / f"{filename}.json").write_text(json.dumps(config, indent=2))
            write_mix_n_match(config, mnm_root, wobble=set_name.startswith("dynamic"))
            write_mnm_baseline(config, mnm_root)
            write_tiled_diffusion(config, filename, td_root)
            write_regional_prompting(config, rp_root)
            written += 1
            print(f"  {config['prefix']}: {config['num_crops']} crops x "
                  f"{config['tiles_per_crop']} tiles")

    print(f"\nwrote {written} mock configs x 4 methods into {args.out}/")
    print("This is a test fixture. Delete it once the real outputs are in place.")


if __name__ == "__main__":
    main()
