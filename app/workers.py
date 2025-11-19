from __future__ import annotations

import asyncio
from pathlib import Path
from typing import List, Dict, Any

import numpy as np
from skimage.measure import find_contours
from instanseg import InstanSeg  # pip install instanseg-torch

from .models import Job, JobType
from .progress import recompute_workflow_progress
from .config import settings
from .tiling import load_wsi_and_tiles, TileInfo, SlideMeta
from .storage import InMemoryStore


# ----------------------------------------------------------------------
# Global InstanSeg model instance
# ----------------------------------------------------------------------
# We keep a single global InstanSeg model so that weights are loaded only once.
# For brightfield H&E slides, the "brightfield_nuclei" model is a good default.
instanseg_brightfield = InstanSeg(
    "brightfield_nuclei",
    image_reader="tiffslide",
    verbosity=0,
)


async def run_job(job: Job, store: InMemoryStore) -> str:
    """
    Entry point called by the scheduler.

    Based on job.job_type this function dispatches to the correct handler
    and returns the filesystem path to the produced result (JSONL / PNG).
    """
    if job.job_type == JobType.CELL_SEGMENTATION:
        return await _run_cell_segmentation(job, store)
    elif job.job_type == JobType.TISSUE_MASK:
        return await _run_tissue_mask(job, store)
    else:
        raise ValueError(f"Unsupported job type: {job.job_type}")


# ======================================================================
# Job type 1: Cell segmentation (InstanSeg over tiles)
# ======================================================================

async def _run_cell_segmentation(job: Job, store: InMemoryStore) -> str:
    """
    Perform tiled cell segmentation on a WSI using InstanSeg.

    Pipeline:
        1. Use load_wsi_and_tiles() to generate overlapping tiles.
        2. For each tile, run InstanSeg.eval_small_image().
        3. Convert the labeled mask into polygons in slide coordinates.
        4. Save all cell polygons as JSONL (one cell per line).

    Returns:
        String path to the JSONL result file.
    """
    image_path = job.image_path
    # (Optional) a quick existence check; if the path is bad we fail early.
    if not Path(image_path).exists():
        raise FileNotFoundError(f"Image not found: {image_path}")

    # 1) Build tiles and slide metadata
    tiles, meta = load_wsi_and_tiles(
        image_path,
        tile_size=settings.TILE_SIZE,
        overlap=settings.TILE_OVERLAP,
    )
    total_tiles = len(tiles)
    cells: List[Dict[str, Any]] = []

    # If you want to read extra parameters from job.params, you can do it here:
    # requested_pixel_size = float(job.params.get("pixel_size", meta.pixel_size))

    # 2) Run InstanSeg on each tile
    for idx, (tile_img, tile_info) in enumerate(tiles):
        # InstanSeg expects an RGB image; if there is an alpha channel, drop it.
        if tile_img.shape[-1] == 4:
            tile_img = tile_img[..., :3]

        # pixel_size can be approximate; for this take-home the main goal is to
        # have a working pipeline rather than perfect physical calibration.
        label_img, _ = instanseg_brightfield.eval_small_image(
            tile_img,
            meta.pixel_size,
        )

        # 3) Convert labels -> polygons in slide coordinates
        tile_cells = _labels_to_polygons(
            label_img,
            tile_info,
            meta,
        )
        cells.extend(tile_cells)

        # 4) Update progress (0.0–1.0)
        job.progress = float(idx + 1) / float(total_tiles)
        await _persist_progress(job, store)

        # Yield control back to the event loop so FastAPI remains responsive
        await asyncio.sleep(0)

    # 5) Save all cells as JSONL (one cell per line)
    output_dir = Path(settings.RESULTS_DIR) / job.user_id
    output_dir.mkdir(parents=True, exist_ok=True)
    out_path = output_dir / f"{job.job_id}_cells.jsonl"

    import json
    with out_path.open("w") as f:
        for cell in cells:
            f.write(json.dumps(cell) + "\n")

    job.progress = 1.0
    await _persist_progress(job, store)
    return str(out_path)


def _labels_to_polygons(
    label_img: np.ndarray,
    tile_info: TileInfo,
    meta: SlideMeta,
) -> List[Dict[str, Any]]:
    """
    Convert a labeled mask into cell polygons in slide coordinates.

    Args:
        label_img: 2D array of integer labels (0 = background).
        tile_info: Spatial information for the tile (x, y, level).
        meta:      Slide-level metadata (image_id, pixel size, etc.).

    Returns:
        A list of dictionaries, one per cell, each containing:
            - label_id: integer label for the cell
            - polygon: list of (x, y) coordinates in slide space
            - tile:    information about which tile it came from
            - pixel_size, image_id: metadata copied from SlideMeta
    """
    cells: List[Dict[str, Any]] = []

    unique_labels = np.unique(label_img)
    for label_id in unique_labels:
        if label_id == 0:
            # Skip background
            continue

        mask = (label_img == label_id).astype(np.uint8)

        # find_contours expects values > threshold; 0.5 is a standard choice.
        contours = find_contours(mask, 0.5)
        if not contours:
            continue

        # For simplicity we take the longest contour as the cell boundary.
        contour = max(contours, key=lambda c: c.shape[0])

        # Contour coordinates are (row, col) within the tile.
        # We map them into slide coordinates by adding tile offsets.
        ys = contour[:, 0] + tile_info.y
        xs = contour[:, 1] + tile_info.x

        polygon = list(zip(xs.astype(float).tolist(), ys.astype(float).tolist()))

        cell = {
            "label_id": int(label_id),
            "polygon": polygon,
            "tile": {
                "x": tile_info.x,
                "y": tile_info.y,
                "level": tile_info.level,
            },
            "pixel_size": meta.pixel_size,
            "image_id": meta.image_id,
        }
        cells.append(cell)

    return cells


# ======================================================================
# Job type 2: Tissue mask (simple background filtering)
# ======================================================================

async def _run_tissue_mask(job: Job, store: InMemoryStore) -> str:
    """
    Generate a simple tissue mask for a WSI.

    Steps:
        1. Read the slide at a low-resolution level using OpenSlide.
        2. Convert to grayscale and apply Otsu threshold to create a binary mask.
        3. Simulate tile-like work to update progress smoothly.
        4. Save the mask as a PNG file.

    Returns:
        String path to the PNG mask file.
    """
    import imageio.v2 as imageio
    from openslide import OpenSlide
    import cv2

    image_path = job.image_path
    if not Path(image_path).exists():
        raise FileNotFoundError(f"Image not found: {image_path}")

    slide = OpenSlide(image_path)

    # Use a lower-magnification level to make the computation cheaper.
    level = slide.get_best_level_for_downsample(16)
    w, h = slide.level_dimensions[level]
    region = slide.read_region((0, 0), level, (w, h))
    img = np.array(region)[:, :, :3]  # drop alpha channel if present

    gray = cv2.cvtColor(img, cv2.COLOR_RGB2GRAY)
    # Otsu threshold to get a binary tissue vs background mask
    _, mask = cv2.threshold(gray, 0, 255, cv2.THRESH_OTSU)

    # Simulate tile-based work so that progress increases smoothly
    tiles_y, tiles_x = 8, 8
    total = tiles_x * tiles_y
    for idx in range(total):
        await asyncio.sleep(0.01)
        job.progress = (idx + 1) / total
        await _persist_progress(job, store)

    output_dir = Path(settings.RESULTS_DIR) / job.user_id
    output_dir.mkdir(parents=True, exist_ok=True)
    out_path = output_dir / f"{job.job_id}_tissue_mask.png"
    imageio.imwrite(out_path, mask)

    job.progress = 1.0
    await _persist_progress(job, store)
    return str(out_path)