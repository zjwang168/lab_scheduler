from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Iterable, List, Tuple

import numpy as np
from PIL import Image

from .config import settings

try:  # Optional SVS support
    from openslide import OpenSlide  # type: ignore
except Exception:  # pragma: no cover - optional dependency
    OpenSlide = None


@dataclass
class TileInfo:
    x: int
    y: int
    level: int


@dataclass
class SlideMeta:
    image_id: str
    pixel_size: float  # microns per pixel (approximate)
    level: int
    width: int
    height: int


def _tiles_for_array(
    img: np.ndarray, tile_size: int, overlap: int
) -> Iterable[Tuple[np.ndarray, TileInfo]]:
    h, w = img.shape[:2]
    stride = tile_size - overlap
    level = 0
    for y in range(0, h, stride):
        for x in range(0, w, stride):
            tile = img[y : y + tile_size, x : x + tile_size]
            if tile.shape[0] < tile_size or tile.shape[1] < tile_size:
                # pad to keep shape consistent
                pad_y = tile_size - tile.shape[0]
                pad_x = tile_size - tile.shape[1]
                tile = np.pad(
                    tile,
                    ((0, pad_y), (0, pad_x), (0, 0)),
                    mode="constant",
                    constant_values=0,
                )
            yield tile, TileInfo(x=x, y=y, level=level)


def load_wsi_and_tiles(
    path: str, tile_size: int | None = None, overlap: int | None = None
) -> Tuple[List[Tuple[np.ndarray, TileInfo]], SlideMeta]:
    """
    Loads a slide image and returns tiles + metadata.

    * If the file is .svs and OpenSlide is available -> use real WSI tiling
    * Otherwise -> treat it as a regular RGB image via Pillow.
    """
    tile_size = tile_size or settings.TILE_SIZE
    overlap = overlap or settings.TILE_OVERLAP

    p = Path(path)
    if not p.exists():
        raise FileNotFoundError(f"Image not found: {path}")

    ext = p.suffix.lower()
    if ext == ".svs" and OpenSlide is not None:
        slide = OpenSlide(str(p))
        level = slide.get_best_level_for_downsample(8)
        w, h = slide.level_dimensions[level]
        # read whole level into memory (OK for demo)
        region = slide.read_region((0, 0), level, (w, h)).convert("RGB")
        img = np.array(region)
        pixel_size = 0.5  # dummy value; real pipeline would query slide properties
    else:
        # Generic PNG / JPEG path
        img = np.array(Image.open(p).convert("RGB"))
        h, w = img.shape[:2]
        level = 0
        pixel_size = 0.5

    tiles = list(_tiles_for_array(img, tile_size, overlap))

    meta = SlideMeta(
        image_id=p.name,
        pixel_size=pixel_size,
        level=level,
        width=img.shape[1],
        height=img.shape[0],
    )
    return tiles, meta