import asyncio
from pathlib import Path
from typing import List, Dict, Any

import numpy as np
from skimage.measure import find_contours
from instanseg import InstanSeg  # pip install instanseg-torch

from .models import Job, JobType
from .config import settings
from .tiling import load_wsi_and_tiles, TileInfo, SlideMeta


# ------------------------------------------------------
# 🔥 Load InstanSeg model globally (avoid loading per job)
# ------------------------------------------------------
instanseg_brightfield = InstanSeg(
    "brightfield_nuclei",   # model name
    image_reader="tiffslide",
    verbosity=0,
)


# ------------------------------------------------------
# 🔥 Entry point called by Scheduler
# ------------------------------------------------------
async def run_job(job: Job) -> str:
    """
    Dispatcher for job types.
    Returns: result file path
    """
    if job.job_type == JobType.CELL_SEGMENTATION:
        return await _run_cell_segmentation(job)

    elif job.job_type == JobType.TISSUE_MASK:
        return await _run_tissue_mask(job)

    else:
        raise ValueError(f"Unsupported job type: {job.job_type}")


# ------------------------------------------------------
# 🔥 Job Type 1: Cell Segmentation (InstanSeg)
# ------------------------------------------------------
async def _run_cell_segmentation(job: Job) -> str:
    """
    Perform tiled cell segmentation on a WSI using InstanSeg.
    Output: JSONL file, one cell polygon per line.
    """
    # 1) Generate slide tiles
    tiles, meta = load_wsi_and_tiles(
        job.image_path,
        tile_size=settings.TILE_SIZE,
        overlap=settings.TILE_OVERLAP,
    )
    total_tiles = len(tiles)
    cells: List[Dict[str, Any]] = []

    # 2) Process each tile
    for idx, (tile_img, tile_info) in enumerate(tiles):

        # Run InstanSeg for cell segmentation
        labeled_output, _ = instanseg_brightfield.eval_small_image(
            tile_img,
            meta.pixel_size,
        )

        # Extract polygons in slide coordinates
        tile_cells = _labels_to_polygons(
            labeled_output,
            tile_info,
            meta,
        )
        cells.extend(tile_cells)

        # update progress
        job.progress = float(idx + 1) / float(total_tiles)
        await asyncio.sleep(0)  # keep event loop responsive

    # 3) Save results
    output_dir = Path(settings.RESULTS_DIR) / job.user_id
    output_dir.mkdir(parents=True, exist_ok=True)
    out_path = output_dir / f"{job.job_id}_cells.jsonl"

    import json
    with out_path.open("w") as f:
        for cell in cells:
            f.write(json.dumps(cell) + "\n")

    job.progress = 1.0
    return str(out_path)


# ------------------------------------------------------
# 🔧 Convert labeled mask → polygons in global slide coords
# ------------------------------------------------------
def _labels_to_polygons(
    label_img: np.ndarray,
    tile_info: TileInfo,
    meta: SlideMeta,
) -> List[Dict[str, Any]]:
    """
    Convert a labeled mask into per-cell polygons.
    """
    cells: List[Dict[str, Any]] = []

    unique_labels = np.unique(label_img)

    for label_id in unique_labels:
        if label_id == 0:
            continue  # background

        mask = (label_img == label_id).astype(np.uint8)
        contours = find_contours(mask, 0.5)
        if not contours:
            continue

        # take largest contour
        contour = max(contours, key=lambda c: c.shape[0])

        # local coords → slide coords
        ys = contour[:, 0] + tile_info.y
        xs = contour[:, 1] + tile_info.x

        polygon = list(zip(xs.astype(float).tolist(), ys.astype(float).tolist()))

        cells.append(
            {
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
        )

    return cells


# ------------------------------------------------------
# 🔥 Job Type 2: Tissue Mask (low-res brightfield mask)
# ------------------------------------------------------
async def _run_tissue_mask(job: Job) -> str:
    """
    Simple tissue mask from low-res slide.
    """
    import imageio.v2 as imageio
    from openslide import OpenSlide
    import cv2

    slide = OpenSlide(job.image_path)
    level = slide.get_best_level_for_downsample(16)
    w, h = slide.level_dimensions[level]
    region = slide.read_region((0, 0), level, (w, h))
    img = np.array(region)[:, :, :3]

    gray = cv2.cvtColor(img, cv2.COLOR_RGB2GRAY)
    _, mask = cv2.threshold(gray, 0, 255, cv2.THRESH_OTSU)

    # Simulate "tile-like" progress updates
    tiles_y, tiles_x = 8, 8
    total = tiles_x * tiles_y

    for idx in range(total):
        await asyncio.sleep(0.01)
        job.progress = (idx + 1) / total

    # save output
    output_dir = Path(settings.RESULTS_DIR) / job.user_id
    output_dir.mkdir(parents=True, exist_ok=True)
    out_path = output_dir / f"{job.job_id}_tissue_mask.png"

    imageio.imwrite(out_path, mask)

    job.progress = 1.0
    return str(out_path)