import asyncio
from pathlib import Path
from typing import List, Dict, Any

import numpy as np
from skimage.measure import find_contours
from instanseg import InstanSeg  # pip install instanseg-torch

from .models import Job, JobType
from .config import settings
from .tiling import load_wsi_and_tiles, TileInfo, SlideMeta

# Global InstanSeg model instance (avoid re-loading weights for every job).
# For brightfield H&E, the "brightfield_nuclei" model is a good default.
instanseg_brightfield = InstanSeg(
    "brightfield_nuclei",
    image_reader="tiffslide",
    verbosity=0,
)

async def run_job(job: Job) -> str:
    """
    Entry point called by the scheduler.

    Dispatches to the correct handler based on job_type and returns the path
    to the result file (e.g., JSONL or PNG).
    """
    if job.job_type == JobType.CELL_SEGMENTATION:
        return await _run_cell_segmentation(job)
    elif job.job_type == JobType.TISSUE_MASK:
        return await _run_tissue_mask(job)
    else:
        raise ValueError(f"Unsupported job type: {job.job_type}")

# ---------- Job Type 1: Cell segmentation (InstanSeg on tiles) ----------

async def _run_cell_segmentation(job: Job) -> str:
    """
    Perform tiled cell segmentation on a WSI using InstanSeg.

    Pipeline:
      1. Tile the WSI at a chosen level (load_wsi_and_tiles).
      2. For each tile, run InstanSeg.eval_small_image.
      3. Convert the labeled mask into polygons in slide coordinates.
      4. Save all cell polygons as JSONL (one cell per line).
    """
    # 1) Generate tiles
    tiles, meta = load_wsi_and_tiles(
        job.image_path,
        tile_size=settings.TILE_SIZE,
        overlap=settings.TILE_OVERLAP,
    )
    total_tiles = len(tiles)
    cells: List[Dict[str, Any]] = []

    # Optionally, you can read extra parameters from job.params, e.g.:
    # requested_pixel_size = float(job.params.get("pixel_size", meta.pixel_size))

    for idx, (tile_img, tile_info) in enumerate(tiles):
        # 2) Run InstanSeg on tile
        # pixel_size here can be approximate; for the take-home the pipeline is
        # more important than exact physical calibration.
        labeled_output, _ = instanseg_brightfield.eval_small_image(
            tile_img,
            meta.pixel_size,
        )

        # 3) Convert label image into polygons in slide coordinates
        tile_cells = _labels_to_polygons(
            labeled_output,
            tile_info,
            meta,
        )
        cells.extend(tile_cells)

        # 4) Update progress
        job.progress = float(idx + 1) / float(total_tiles)
        # Yield control back to the event loop so FastAPI stays responsive
        await asyncio.sleep(0)

    # 5) Save cells as JSONL (one cell per line)
    output_dir = Path(settings.RESULTS_DIR) / job.user_id
    output_dir.mkdir(parents=True, exist_ok=True)
    out_path = output_dir / f"{job.job_id}_cells.jsonl"

    import json
    with out_path.open("w") as f:
        for cell in cells:
            f.write(json.dumps(cell) + "\n")

    job.progress = 1.0
    return str(out_path)


def _labels_to_polygons(
    label_img: np.ndarray,
    tile_info: TileInfo,
    meta: SlideMeta,
) -> List[Dict[str, Any]]:
    """
    Convert a labeled mask into cell polygons in slide coordinates.

    Inputs:
      - label_img: 2D array of integer labels (0 = background).
      - tile_info: spatial info for this tile (x, y, level).
      - meta:      slide-level metadata (image id, pixel size).

    Output:
      A list of dicts, one per cell, with fields:
        - label_id
        - polygon: [(x, y), ...] coordinate list in the chosen level
        - tile:    tile location and level
        - pixel_size, image_id metadata
    """
    cells: List[Dict[str, Any]] = []

    unique_labels = np.unique(label_img)
    for label_id in unique_labels:
        if label_id == 0:
            # Skip background
            continue

        mask = (label_img == label_id).astype(np.uint8)
        # find_contours expects values > threshold; 0.5 is standard here.
        contours = find_contours(mask, 0.5)
        if not contours:
            continue

        # For simplicity, take the longest contour as the cell boundary
        contour = max(contours, key=lambda c: c.shape[0])

        # Contour is in (row, col) coordinates inside the tile.
        # Map to slide coordinates by adding the tile offsets at this level.
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

# ---------- Job Type 2: Tissue mask (simple background filtering) ----------

async def _run_tissue_mask(job: Job) -> str:
    """
    Simple tissue mask generation:

    - Read the slide at a low-resolution level.
    - Convert to grayscale and apply Otsu threshold to obtain a binary mask.
    - Update progress in a tile-like loop to simulate long-running work.
    - Save the final mask as a PNG.
    """
    import imageio.v2 as imageio
    from openslide import OpenSlide
    import cv2

    slide = OpenSlide(job.image_path)
    # Use a lower magnification level for a cheaper mask
    level = slide.get_best_level_for_downsample(16)
    w, h = slide.level_dimensions[level]
    region = slide.read_region((0, 0), level, (w, h))
    img = np.array(region)[:, :, :3]

    gray = cv2.cvtColor(img, cv2.COLOR_RGB2GRAY)
    # Otsu threshold to get a tissue vs background mask
    _, mask = cv2.threshold(gray, 0, 255, cv2.THRESH_OTSU)

    # Simulate tile-based work so we can report smooth progress
    tiles_y, tiles_x = 8, 8
    total = tiles_x * tiles_y
    for idx in range(total):
        await asyncio.sleep(0.01)
        job.progress = (idx + 1) / total

    output_dir = Path(settings.RESULTS_DIR) / job.user_id
    output_dir.mkdir(parents=True, exist_ok=True)
    out_path = output_dir / f"{job.job_id}_tissue_mask.png"
    imageio.imwrite(out_path, mask)
    job.progress = 1.0
    return str(out_path)