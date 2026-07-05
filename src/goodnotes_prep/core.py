from __future__ import annotations

import json
import math
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import fitz
from PIL import Image


POINTS_PER_INCH = 72.0
EDGE_MAX_DISTANCE_PT = 96.0
LONG_EDGE_MAX_DISTANCE_PT = 220.0
LONG_LINE_TEXT_DISTANCE_PT = 140.0
LONG_CONNECTOR_LENGTH_PT = 500.0
ULTRA_LONG_CONNECTOR_LENGTH_PT = 2000.0


@dataclass(frozen=True)
class PrepSettings:
    dpi: int = 180
    tile_px: int = 1600
    overlap_px: int = 384
    global_max_px: int = 2400

    def validate(self) -> None:
        if self.dpi <= 0:
            raise ValueError("--dpi must be positive")
        if self.tile_px <= 0:
            raise ValueError("--tile-px must be positive")
        if self.overlap_px < 0:
            raise ValueError("--overlap-px must be non-negative")
        if self.overlap_px >= self.tile_px:
            raise ValueError("--overlap-px must be smaller than --tile-px")
        if self.global_max_px <= 0:
            raise ValueError("--global-max-px must be positive")


@dataclass(frozen=True)
class AgentTileSettings:
    dpi: int = 200
    tile_height_px: int = 2000
    overlap: float = 0.05
    quality: int = 90

    def validate(self) -> None:
        if self.dpi <= 0:
            raise ValueError("--agent-dpi must be positive")
        if self.tile_height_px <= 0:
            raise ValueError("--agent-tile-height must be positive")
        if not 0 <= self.overlap < 1:
            raise ValueError("--agent-overlap must be >= 0 and < 1")
        if not 1 <= self.quality <= 100:
            raise ValueError("--agent-quality must be between 1 and 100")


def prepare_pdf(
    source_pdf: Path,
    out_dir: Path,
    ocr_pdf: Path | None = None,
    settings: PrepSettings | None = None,
    emit_recognition_tasks: bool = False,
    emit_agent_tiles: bool = False,
    agent_tile_settings: AgentTileSettings | None = None,
) -> dict[str, Any]:
    settings = settings or PrepSettings()
    settings.validate()
    agent_tile_settings = agent_tile_settings or AgentTileSettings()
    agent_tile_settings.validate()

    source_pdf = source_pdf.expanduser().resolve()
    out_dir = out_dir.expanduser().resolve()
    ocr_pdf = ocr_pdf.expanduser().resolve() if ocr_pdf else source_pdf

    if not source_pdf.is_file():
        raise FileNotFoundError(f"source PDF not found: {source_pdf}")
    if not ocr_pdf.is_file():
        raise FileNotFoundError(f"OCR PDF not found: {ocr_pdf}")
    ensure_empty_output_dir(out_dir)

    with fitz.open(source_pdf) as source_doc, fitz.open(ocr_pdf) as ocr_doc:
        manifest: dict[str, Any] = {
            "created_at": datetime.now(timezone.utc).isoformat(),
            "created_by": "goodnotes-pdf-prep 0.1.0",
            "source_pdf": str(source_pdf),
            "ocr_pdf": str(ocr_pdf),
            "settings": {
                "dpi": settings.dpi,
                "tile_px": settings.tile_px,
                "overlap_px": settings.overlap_px,
                "global_max_px": settings.global_max_px,
                "edge_max_distance_pt": EDGE_MAX_DISTANCE_PT,
                "agent_tiles": {
                    "enabled": emit_agent_tiles,
                    "dpi": agent_tile_settings.dpi,
                    "tile_height_px": agent_tile_settings.tile_height_px,
                    "overlap": agent_tile_settings.overlap,
                    "quality": agent_tile_settings.quality,
                },
            },
            "pages": [],
            "warnings": [],
        }

        if source_doc.page_count != ocr_doc.page_count:
            manifest["warnings"].append(
                f"source PDF has {source_doc.page_count} pages but OCR PDF has {ocr_doc.page_count}; "
                "text extraction will use matching page indexes only."
            )

        for page_index in range(source_doc.page_count):
            source_page = source_doc[page_index]
            ocr_page = ocr_doc[page_index] if page_index < ocr_doc.page_count else None
            page_result = process_page(
                source_page,
                ocr_page,
                page_index,
                out_dir,
                settings,
                emit_agent_tiles,
                agent_tile_settings,
            )
            manifest["pages"].append(page_result)

        if emit_agent_tiles:
            write_agent_tiles_metadata(out_dir, manifest)
        write_json(out_dir / "manifest.json", manifest)
        if emit_recognition_tasks:
            write_recognition_tasks(out_dir, manifest)
        write_json(out_dir / "graph.json", build_graph(manifest))
        write_text(out_dir / "prompt.md", build_prompt(manifest, emit_recognition_tasks))
        return manifest


def ensure_empty_output_dir(out_dir: Path) -> None:
    if out_dir.exists() and not out_dir.is_dir():
        raise FileExistsError(f"output path exists and is not a directory: {out_dir}")
    if out_dir.exists() and any(out_dir.iterdir()):
        raise FileExistsError(f"output directory must be empty or absent: {out_dir}")
    out_dir.mkdir(parents=True, exist_ok=True)


def process_page(
    source_page: fitz.Page,
    ocr_page: fitz.Page | None,
    page_index: int,
    out_dir: Path,
    settings: PrepSettings,
    emit_agent_tiles: bool = False,
    agent_tile_settings: AgentTileSettings | None = None,
) -> dict[str, Any]:
    page_num = page_index + 1
    page_dir = out_dir / "pages" / f"page_{page_num:03d}"
    tiles_dir = page_dir / "tiles"
    tiles_dir.mkdir(parents=True, exist_ok=True)

    page_rect = source_page.rect
    warnings: list[str] = []

    overview_rel = Path("pages") / f"page_{page_num:03d}" / "overview.png"
    render_overview(source_page, out_dir / overview_rel, settings.global_max_px)
    agent_tiles = (
        render_agent_tiles(source_page, out_dir, page_index, agent_tile_settings or AgentTileSettings())
        if emit_agent_tiles
        else []
    )

    tiles: list[dict[str, Any]] = []
    for tile_index, bbox in enumerate(build_tile_bboxes(page_rect.width, page_rect.height, settings), start=1):
        tile_id = f"tile_{tile_index:04d}"
        tile_rel = Path("pages") / f"page_{page_num:03d}" / "tiles" / f"{tile_id}.png"
        pix = source_page.get_pixmap(dpi=settings.dpi, clip=fitz.Rect(bbox), alpha=False)
        pix.save(out_dir / tile_rel)
        tiles.append(
            {
                "id": tile_id,
                "image": tile_rel.as_posix(),
                "bbox": round_rect(bbox),
                "pixel_width": pix.width,
                "pixel_height": pix.height,
                "dpi": settings.dpi,
                "overlap_px": settings.overlap_px,
            }
        )

    text_source = "ocr_pdf_text_layer" if ocr_page is not None else None
    text_blocks = extract_text_blocks(ocr_page, text_source) if ocr_page is not None else []
    if not text_blocks:
        warnings.append("No extractable text layer found for this page; downstream AI should read text from tile images.")

    drawing_paths = extract_drawing_paths(source_page)
    if not drawing_paths:
        warnings.append("No vector drawing paths found for this page; connector analysis is unavailable.")

    edge_analysis = build_edge_analysis(drawing_paths, text_blocks)
    edge_candidates = edge_analysis["edge_candidates"]
    if drawing_paths and not any(item["connector_candidate"] for item in drawing_paths):
        warnings.append("Vector paths were found, but none matched the connector-candidate heuristic.")

    page_result: dict[str, Any] = {
        "page_index": page_index,
        "page_number": page_num,
        "size_pt": [round_float(page_rect.width), round_float(page_rect.height)],
        "overview": overview_rel.as_posix(),
        "tiles": tiles,
        "agent_tiles": agent_tiles,
        "text_blocks": text_blocks,
        "drawing_paths": drawing_paths,
        "edge_candidates": edge_candidates,
        "unresolved_connectors": edge_analysis["unresolved_connectors"],
        "warnings": warnings,
    }
    write_json(page_dir / "page.json", page_result)
    write_json(page_dir / "debug_edges.json", edge_analysis)
    return page_result


def build_tile_bboxes(width_pt: float, height_pt: float, settings: PrepSettings) -> list[list[float]]:
    tile_pt = settings.tile_px * POINTS_PER_INCH / settings.dpi
    overlap_pt = settings.overlap_px * POINTS_PER_INCH / settings.dpi
    stride_pt = tile_pt - overlap_pt

    xs = axis_starts(width_pt, tile_pt, stride_pt)
    ys = axis_starts(height_pt, tile_pt, stride_pt)

    bboxes: list[list[float]] = []
    for y0 in ys:
        for x0 in xs:
            bboxes.append([x0, y0, min(x0 + tile_pt, width_pt), min(y0 + tile_pt, height_pt)])
    return bboxes


def build_agent_tile_bboxes(width_pt: float, height_pt: float, settings: AgentTileSettings) -> list[list[float]]:
    tile_height_pt = settings.tile_height_px * POINTS_PER_INCH / settings.dpi
    overlap_pt = tile_height_pt * settings.overlap
    stride_pt = tile_height_pt - overlap_pt
    bboxes: list[list[float]] = []
    y0 = 0.0
    while y0 < height_pt:
        y1 = min(y0 + tile_height_pt, height_pt)
        bboxes.append([0.0, y0, width_pt, y1])
        if y1 >= height_pt:
            break
        y0 += stride_pt
    return bboxes


def axis_starts(length: float, tile_size: float, stride: float) -> list[float]:
    if length <= tile_size:
        return [0.0]

    starts: list[float] = []
    pos = 0.0
    while pos + tile_size < length:
        starts.append(pos)
        pos += stride

    starts.append(max(0.0, length - tile_size))
    return dedupe_sorted(starts)


def dedupe_sorted(values: list[float], tolerance: float = 1e-6) -> list[float]:
    result: list[float] = []
    for value in sorted(values):
        if not result or abs(value - result[-1]) > tolerance:
            result.append(value)
    return result


def render_overview(page: fitz.Page, out_path: Path, max_edge_px: int) -> None:
    rect = page.rect
    zoom = min(1.0, max_edge_px / max(rect.width, rect.height))
    if max(rect.width, rect.height) < max_edge_px:
        zoom = max_edge_px / max(rect.width, rect.height)
    pix = page.get_pixmap(matrix=fitz.Matrix(zoom, zoom), alpha=False)
    pix.save(out_path)


def render_agent_tiles(
    page: fitz.Page,
    out_dir: Path,
    page_index: int,
    settings: AgentTileSettings,
) -> list[dict[str, Any]]:
    page_num = page_index + 1
    agent_tiles_dir = out_dir / "agent_tiles"
    agent_tiles_dir.mkdir(parents=True, exist_ok=True)

    bboxes = build_agent_tile_bboxes(page.rect.width, page.rect.height, settings)
    overlap_px = int(round(settings.tile_height_px * settings.overlap))
    tiles: list[dict[str, Any]] = []
    for tile_index, bbox in enumerate(bboxes):
        tile_id = f"agent_tile_{len(list(agent_tiles_dir.glob('*.jpg'))):03d}"
        image_rel = Path("agent_tiles") / f"{tile_id}.jpg"
        pix = page.get_pixmap(dpi=settings.dpi, clip=fitz.Rect(bbox), alpha=False)
        image = Image.frombytes("RGB", (pix.width, pix.height), pix.samples)
        image.save(out_dir / image_rel, format="JPEG", quality=settings.quality, optimize=True)

        tiles.append(
            {
                "id": tile_id,
                "image": image_rel.as_posix(),
                "page_index": page_index,
                "page_number": page_num,
                "bbox": round_rect(bbox),
                "global_pdf_bbox": round_rect(bbox),
                "pixel_width": pix.width,
                "pixel_height": pix.height,
                "dpi": settings.dpi,
                "tile_height_px": settings.tile_height_px,
                "overlap": settings.overlap,
                "overlap_top_px": overlap_px if tile_index > 0 else 0,
                "overlap_bottom_px": overlap_px if tile_index < len(bboxes) - 1 else 0,
            }
        )
    return tiles


def write_agent_tiles_metadata(out_dir: Path, manifest: dict[str, Any]) -> None:
    tiles = [tile for page in manifest["pages"] for tile in page.get("agent_tiles", [])]
    metadata = {
        "source_pdf": manifest["source_pdf"],
        "settings": manifest["settings"]["agent_tiles"],
        "tile_count": len(tiles),
        "tiles": tiles,
    }
    write_json(out_dir / "agent_tiles" / "metadata.json", metadata)


def extract_text_blocks(page: fitz.Page | None, source: str | None) -> list[dict[str, Any]]:
    if page is None:
        return []

    blocks: list[dict[str, Any]] = []
    text_dict = page.get_text("dict")
    block_id = 1

    for block in text_dict.get("blocks", []):
        if block.get("type") != 0:
            continue

        lines: list[str] = []
        for line in block.get("lines", []):
            line_text = "".join(span.get("text", "") for span in line.get("spans", [])).strip()
            if line_text:
                lines.append(line_text)

        text = "\n".join(lines).strip()
        if not text:
            continue

        blocks.append(
            {
                "id": f"t_{block_id:04d}",
                "text": text,
                "bbox": round_rect(block["bbox"]),
                "source": source,
                "confidence": None,
            }
        )
        block_id += 1

    return blocks


def extract_drawing_paths(page: fitz.Page) -> list[dict[str, Any]]:
    results: list[dict[str, Any]] = []
    for index, path in enumerate(page.get_drawings(), start=1):
        rect = fitz.Rect(path.get("rect") or fitz.Rect())
        points, total_length = path_points_and_length(path.get("items", []))
        endpoints = choose_endpoints(points, rect)
        stroke_color = round_color(path.get("color"))
        fill_color = round_color(path.get("fill"))
        connector_candidate = is_connector_candidate(rect, total_length, points, path.get("color"), path.get("fill"))

        results.append(
            {
                "id": f"d_{index:04d}",
                "bbox": round_rect(rect),
                "endpoints": [round_point(endpoints[0]), round_point(endpoints[1])] if endpoints else None,
                "points": [round_point(point) for point in points],
                "path_type": path.get("type"),
                "stroke_color": stroke_color,
                "fill_color": fill_color,
                "color_hint": color_hint(path.get("color") or path.get("fill")),
                "stroke_width": round_float(path.get("width")) if path.get("width") is not None else None,
                "total_length_pt": round_float(total_length),
                "connector_candidate": connector_candidate,
                "semantic_hint": "long_distance_connector"
                if is_long_distance_connector(rect, total_length)
                else None,
            }
        )
    return results


def path_points_and_length(items: list[Any]) -> tuple[list[fitz.Point], float]:
    points: list[fitz.Point] = []
    length = 0.0

    for item in items:
        if not item:
            continue
        kind = item[0]
        if kind == "l":
            p1, p2 = fitz.Point(item[1]), fitz.Point(item[2])
            if not points:
                points.append(p1)
            points.append(p2)
            length += point_distance(p1, p2)
        elif kind == "c":
            p1, p2, p3, p4 = (fitz.Point(item[1]), fitz.Point(item[2]), fitz.Point(item[3]), fitz.Point(item[4]))
            if not points:
                points.append(p1)
            points.append(p4)
            length += point_distance(p1, p2) + point_distance(p2, p3) + point_distance(p3, p4)
        elif kind == "re":
            rect = fitz.Rect(item[1])
            points.extend([rect.tl, rect.tr, rect.br, rect.bl])
            length += 2 * (rect.width + rect.height)
        elif kind == "qu":
            quad = fitz.Quad(item[1])
            quad_points = [quad.ul, quad.ur, quad.lr, quad.ll]
            points.extend(quad_points)
            length += sum(point_distance(a, b) for a, b in zip(quad_points, quad_points[1:] + quad_points[:1]))

    return points, length


def choose_endpoints(points: list[fitz.Point], rect: fitz.Rect) -> tuple[fitz.Point, fitz.Point] | None:
    if len(points) >= 2:
        return points[0], points[-1]
    if rect.is_empty:
        return None
    if rect.width >= rect.height:
        return fitz.Point(rect.x0, rect.y0 + rect.height / 2), fitz.Point(rect.x1, rect.y0 + rect.height / 2)
    return fitz.Point(rect.x0 + rect.width / 2, rect.y0), fitz.Point(rect.x0 + rect.width / 2, rect.y1)


def is_connector_candidate(
    rect: fitz.Rect,
    total_length: float,
    points: list[fitz.Point],
    stroke_color: Any = None,
    fill_color: Any = None,
) -> bool:
    if rect.is_empty or len(points) < 2:
        return False

    width = abs(rect.width)
    height = abs(rect.height)
    max_dim = max(width, height)
    min_dim = max(min(width, height), 0.01)
    diagonal = math.hypot(width, height)
    slenderness = max_dim / min_dim

    if diagonal < 72.0:
        return False
    if is_tealish(stroke_color or fill_color) and diagonal >= LONG_CONNECTOR_LENGTH_PT:
        return True
    if len(points) == 2 and diagonal >= 72.0:
        return True
    if slenderness >= 4.0 and max_dim >= 96.0:
        return True
    if diagonal >= 160.0 and total_length >= diagonal and min_dim <= 80.0:
        return True
    return False


def build_edge_candidates(
    drawing_paths: list[dict[str, Any]],
    text_blocks: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    return build_edge_analysis(drawing_paths, text_blocks)["edge_candidates"]


def build_edge_analysis(
    drawing_paths: list[dict[str, Any]],
    text_blocks: list[dict[str, Any]],
) -> dict[str, Any]:
    if not text_blocks:
        unresolved = [
            {
                "connector_id": connector["id"],
                "reason": "no_text_blocks",
                **connector_evidence(connector),
            }
            for connector in drawing_paths
            if connector.get("connector_candidate")
        ]
        return {"edge_candidates": [], "unresolved_connectors": unresolved, "debug": unresolved}

    edges: list[dict[str, Any]] = []
    unresolved: list[dict[str, Any]] = []
    debug: list[dict[str, Any]] = []
    for connector in drawing_paths:
        if not connector["connector_candidate"] or not connector["endpoints"]:
            continue

        match = match_connector_to_text(connector, text_blocks)
        if match is None:
            unresolved_item = {
                "connector_id": connector["id"],
                "reason": "no_nearby_text_blocks",
                **connector_evidence(connector),
            }
            unresolved.append(unresolved_item)
            debug.append(unresolved_item)
            continue

        start_match, end_match, method = match
        start_block, start_distance = start_match
        end_block, end_distance = end_match
        if start_block["id"] == end_block["id"]:
            unresolved_item = {
                "connector_id": connector["id"],
                "reason": "both_ends_matched_same_text_block",
                "text_block_id": start_block["id"],
                **connector_evidence(connector),
            }
            unresolved.append(unresolved_item)
            debug.append(unresolved_item)
            continue

        distance_score = start_distance + end_distance
        confidence = edge_confidence(distance_score, connector)
        edges.append(
            {
                "id": f"e_{len(edges) + 1:04d}",
                "connector_id": connector["id"],
                "from_text_block_id": start_block["id"],
                "to_text_block_id": end_block["id"],
                "from_text": start_block["text"],
                "to_text": end_block["text"],
                "endpoint_distances_pt": [round_float(start_distance), round_float(end_distance)],
                "distance_score": round_float(distance_score),
                "confidence": confidence,
                "semantic_hint": connector.get("semantic_hint"),
                "evidence": {
                    "method": method,
                    **connector_evidence(connector),
                },
            }
        )
        debug.append(
            {
                "connector_id": connector["id"],
                "result": "edge_candidate",
                "edge_id": edges[-1]["id"],
                "method": method,
                "from_text": start_block["text"],
                "to_text": end_block["text"],
                "distance_score": round_float(distance_score),
            }
        )

    return {"edge_candidates": edges, "unresolved_connectors": unresolved, "debug": debug}


def match_connector_to_text(
    connector: dict[str, Any],
    text_blocks: list[dict[str, Any]],
) -> tuple[tuple[dict[str, Any], float], tuple[dict[str, Any], float], str] | None:
    start = fitz.Point(connector["endpoints"][0])
    end = fitz.Point(connector["endpoints"][1])
    start_match = nearest_text_block(start, text_blocks)
    end_match = nearest_text_block(end, text_blocks)
    is_long = connector.get("semantic_hint") == "long_distance_connector"
    max_endpoint_distance = LONG_EDGE_MAX_DISTANCE_PT if is_long else EDGE_MAX_DISTANCE_PT

    if (
        start_match is not None
        and end_match is not None
        and start_match[1] <= max_endpoint_distance
        and end_match[1] <= max_endpoint_distance
    ):
        return start_match, end_match, "endpoint"

    if is_long:
        line_match = line_proximity_match(connector, text_blocks)
        if line_match is not None:
            return (*line_match, "line_proximity")

    return None


def line_proximity_match(
    connector: dict[str, Any],
    text_blocks: list[dict[str, Any]],
) -> tuple[tuple[dict[str, Any], float], tuple[dict[str, Any], float]] | None:
    points = connector_points(connector)
    if len(points) < 2:
        return None

    start = points[0]
    end = points[-1]
    axis_length = max(point_distance(start, end), 0.01)
    candidates: list[tuple[float, dict[str, Any], float]] = []

    for block in text_blocks:
        center = rect_center(text_block_rect(block))
        distance = point_to_polyline_distance(center, points)
        endpoint_distance = min(point_distance(center, start), point_distance(center, end))
        if distance <= LONG_LINE_TEXT_DISTANCE_PT or endpoint_distance <= LONG_EDGE_MAX_DISTANCE_PT:
            projection = ((center.x - start.x) * (end.x - start.x) + (center.y - start.y) * (end.y - start.y)) / axis_length
            candidates.append((projection, block, min(distance, endpoint_distance)))

    if len(candidates) < 2:
        return None

    candidates.sort(key=lambda item: item[0])
    first = candidates[0]
    last = candidates[-1]
    if first[1]["id"] == last[1]["id"]:
        return None
    return (first[1], first[2]), (last[1], last[2])


def nearest_text_block(point: fitz.Point, text_blocks: list[dict[str, Any]]) -> tuple[dict[str, Any], float] | None:
    best: tuple[dict[str, Any], float] | None = None
    for block in text_blocks:
        distance = point_to_rect_distance(point, text_block_rect(block))
        if best is None or distance < best[1]:
            best = (block, distance)
    return best


def text_block_rect(block: dict[str, Any]) -> fitz.Rect:
    return fitz.Rect(block.get("global_bbox") or block["bbox"])


def rect_center(rect: fitz.Rect) -> fitz.Point:
    return fitz.Point((rect.x0 + rect.x1) / 2, (rect.y0 + rect.y1) / 2)


def point_to_rect_distance(point: fitz.Point, rect: fitz.Rect) -> float:
    dx = max(rect.x0 - point.x, 0.0, point.x - rect.x1)
    dy = max(rect.y0 - point.y, 0.0, point.y - rect.y1)
    return math.hypot(dx, dy)


def point_distance(a: fitz.Point, b: fitz.Point) -> float:
    return math.hypot(a.x - b.x, a.y - b.y)


def point_to_segment_distance(point: fitz.Point, a: fitz.Point, b: fitz.Point) -> float:
    length_squared = (b.x - a.x) ** 2 + (b.y - a.y) ** 2
    if length_squared == 0:
        return point_distance(point, a)
    t = max(0.0, min(1.0, ((point.x - a.x) * (b.x - a.x) + (point.y - a.y) * (b.y - a.y)) / length_squared))
    projection = fitz.Point(a.x + t * (b.x - a.x), a.y + t * (b.y - a.y))
    return point_distance(point, projection)


def point_to_polyline_distance(point: fitz.Point, points: list[fitz.Point]) -> float:
    return min(point_to_segment_distance(point, a, b) for a, b in zip(points, points[1:]))


def connector_points(connector: dict[str, Any]) -> list[fitz.Point]:
    points = [fitz.Point(point) for point in connector.get("points") or []]
    if len(points) >= 2:
        return points
    return [fitz.Point(point) for point in connector.get("endpoints") or []]


def connector_evidence(connector: dict[str, Any]) -> dict[str, Any]:
    return {
        "semantic_hint": connector.get("semantic_hint"),
        "scale_hint": connector_scale_hint(connector),
        "connector_bbox": connector.get("bbox"),
        "connector_endpoints": connector.get("endpoints"),
        "connector_anchor_points": connector_anchor_points(connector),
        "color_hint": connector.get("color_hint"),
        "stroke_color": connector.get("stroke_color"),
        "total_length_pt": connector.get("total_length_pt"),
        "route_hint": connector_route_hint(connector),
    }


def connector_scale_hint(connector: dict[str, Any]) -> str | None:
    total_length = float(connector.get("total_length_pt") or 0.0)
    bbox = fitz.Rect(connector.get("bbox") or fitz.Rect())
    if total_length >= ULTRA_LONG_CONNECTOR_LENGTH_PT or math.hypot(bbox.width, bbox.height) >= ULTRA_LONG_CONNECTOR_LENGTH_PT:
        return "ultra_long_connector"
    if connector.get("semantic_hint") == "long_distance_connector":
        return "long_connector"
    return None


def connector_anchor_points(connector: dict[str, Any]) -> list[list[float]]:
    points = connector.get("points") or connector.get("endpoints") or []
    if len(points) <= 8:
        return [round_point(point) for point in points]
    anchors = [points[0], points[len(points) // 2], points[-1]]
    return [round_point(point) for point in anchors]


def connector_route_hint(connector: dict[str, Any]) -> str | None:
    points = connector_points(connector)
    if len(points) < 2:
        return None
    if len(points) == 2:
        return "straight_or_single_segment"

    orientations = [segment_orientation(a, b) for a, b in zip(points, points[1:])]
    compact_orientations: list[str] = []
    for orientation in orientations:
        if not compact_orientations or compact_orientations[-1] != orientation:
            compact_orientations.append(orientation)

    if len(points) <= 8 and all(item in {"horizontal", "vertical"} for item in compact_orientations):
        return "orthogonal_polyline: " + " -> ".join(compact_orientations)
    if len(points) <= 8:
        return "polyline: " + " -> ".join(compact_orientations)
    return "freehand_curve"


def segment_orientation(a: fitz.Point, b: fitz.Point) -> str:
    dx = abs(b.x - a.x)
    dy = abs(b.y - a.y)
    if dx >= dy * 3:
        return "horizontal"
    if dy >= dx * 3:
        return "vertical"
    return "diagonal"


def is_long_distance_connector(rect: fitz.Rect, total_length: float) -> bool:
    return math.hypot(rect.width, rect.height) >= LONG_CONNECTOR_LENGTH_PT or total_length >= LONG_CONNECTOR_LENGTH_PT


def round_color(color: Any) -> list[float] | None:
    if color is None:
        return None
    return [round_float(part) for part in color]


def color_hint(color: Any) -> str | None:
    if color is None:
        return None
    if is_tealish(color):
        return "teal"
    r, g, b = color
    if r > 0.8 and g > 0.35 and b < 0.3:
        return "orange"
    if max(color) < 0.2:
        return "dark"
    if max(color) - min(color) < 0.08:
        return "gray"
    return "other"


def is_tealish(color: Any) -> bool:
    if color is None:
        return False
    r, g, b = color
    return r < 0.25 and g > 0.45 and b > 0.35


def edge_confidence(distance_score: float, connector: dict[str, Any]) -> float:
    max_score = LONG_EDGE_MAX_DISTANCE_PT * 2 if connector.get("semantic_hint") == "long_distance_connector" else EDGE_MAX_DISTANCE_PT * 2
    return round_float(max(0.1, min(0.95, 1.0 - (distance_score / max_score) * 0.5)))


def build_prompt(manifest: dict[str, Any], emit_recognition_tasks: bool = False) -> str:
    page_lines = []
    for page in manifest["pages"]:
        page_lines.append(
            f"- Page {page['page_number']}: overview `{page['overview']}`, "
            f"{len(page['tiles'])} detail tiles, {len(page.get('agent_tiles', []))} agent tiles, "
            f"{len(page['text_blocks'])} text blocks, "
            f"{len(page['edge_candidates'])} edge candidates."
        )

    has_agent_tiles = any(page.get("agent_tiles") for page in manifest["pages"])
    edge_lines = build_prompt_edge_lines(manifest)
    if not edge_lines:
        edge_lines = ["- No graph edges are available yet. Use overview and tiles, and mark uncertain relationships."]

    long_connector_lines = build_prompt_long_connector_lines(manifest)
    if not long_connector_lines:
        long_connector_lines = ["- No long connector candidates were detected."]

    agent_tile_lines = []
    if has_agent_tiles:
        agent_tile_lines = [
            "",
            "## Agent Tiles",
            "- `agent_tiles/metadata.json` lists full-width overlapping visual slices for continuous reading.",
            "- Agent tiles are per-page slices with fixed stride and a shorter final tile when needed; they are not a stitched multi-page canvas.",
            "- Use `agent_tiles/*.jpg` after `overview.png` to follow long arrows and spatial flow across the whiteboard.",
            "- Use `pages/page_XXX/tiles/*.png` only when local handwriting or exact bbox detail needs confirmation.",
            "- Repeated content in overlap bands should be merged, not duplicated.",
        ]

    recognition_lines = []
    if emit_recognition_tasks:
        recognition_lines = [
            "",
            "## Recognition Task Mode",
            "- Use `recognition_tasks/page_XXX.json` to OCR/VLM each tile.",
            "- Do not summarize tiles. Return structured text blocks only.",
            "- Return tile-local `bbox` values in pixels. The pipeline can convert them to global PDF coordinates.",
            "- Prefer detail `tiles` for precise bbox. `agent_tiles` may also be used as `tile_id` sources when they are better for continuous reading.",
            "- Content may repeat across overlapping tiles; repeated text will be deduplicated later.",
            "- Fill the response shape described in `text_blocks.schema.json`, then run `goodnotes-prep attach-text OUT --text-blocks TEXT_BLOCKS.json`.",
        ]

    return "\n".join(
        [
            "# GoodNotes Whiteboard Agent Handoff",
            "",
            "You are a multimodal agent reading a preprocessed GoodNotes whiteboard folder.",
            "Follow this file as the instruction source; the user does not need to write another prompt.",
            "",
            "## Mandatory Read Order",
            "1. Read this `prompt.md` first.",
            "2. Read `graph.json` if it exists. Treat `nodes` and `edges` as the highest-priority structural evidence.",
            "3. Inspect each `overview.png` to understand the full whiteboard layout.",
            "4. If `agent_tiles/metadata.json` exists, open the full-width `agent_tiles/*.jpg` slices to read long-range visual flow.",
            "5. Read `manifest.json` for page sizes, tile coordinates, drawing paths, connector candidates, and warnings.",
            "6. Open high-resolution detail tile images only when text, handwriting, or exact local detail is unclear.",
            "",
            "## Evidence Priority",
            "- Preserve every `graph.json.edges[]` relationship explicitly in the final notes.",
            "- If an edge has `from_text` and `to_text`, write that relationship literally, for example: `from_text -> to_text`.",
            "- Long arrows must appear as explicit relationship lines in `结构关系`; do not collapse them into a vague overall connection.",
            "- Long-distance connectors are not decorative unless the visual evidence clearly proves otherwise.",
            "- Do not replace graph edges with broad summaries such as \"the line connects the overall structure\".",
            "- Use `overview.png` to verify global placement, `agent_tiles` to follow continuous long-range relations, and detail tiles to verify exact handwriting.",
            "- When graph evidence and visual interpretation conflict, report the conflict instead of silently choosing a smoother story.",
            "- Mark uncertain readings with `[unclear]`; do not invent missing text to make the notes sound complete.",
            "",
            "## Required Output",
            "Produce Markdown with these sections:",
            "1. `忠实转写`: preserve the visible whiteboard structure and important arrows/lines.",
            "2. `结构关系`: list graph edges and important visual relationships as `A -> B`.",
            "3. `整理版`: a cleaned-up explanation that does not remove or blur the graph edges.",
            "4. `Mermaid`: a mindmap or flowchart that includes the explicit graph edges.",
            "5. `不确定处`: list uncertain handwriting, ambiguous arrows, and any evidence conflicts.",
            "",
            "## Coordinate Notes",
            "- All `bbox` values are PDF page coordinates in points: `[x0, y0, x1, y1]`.",
            "- Tile image paths are relative to this output folder.",
            "- Each tile overlaps neighbors, so repeated content should be deduplicated by text and bbox.",
            "- `agent_tiles[].global_pdf_bbox` uses PDF page coordinates; returned text `bbox` values use tile-local pixels.",
            *agent_tile_lines,
            *recognition_lines,
            "",
            "## Graph Edges To Preserve",
            *edge_lines,
            "",
            "## Long Connectors To Review Separately",
            "- Treat each item below as a separate visual connector candidate. Do not merge multiple long lines into one narrative.",
            "- Resolved edges are structural evidence; unresolved connectors are visual evidence that still need endpoint text verification.",
            *long_connector_lines,
            "",
            "## Pages",
            *page_lines,
            "",
        ]
    )


def build_prompt_edge_lines(manifest: dict[str, Any]) -> list[str]:
    lines: list[str] = []
    for page in manifest["pages"]:
        for edge in page.get("edge_candidates", []):
            from_text = edge.get("from_text") or edge.get("from_text_block_id")
            to_text = edge.get("to_text") or edge.get("to_text_block_id")
            hint = edge.get("semantic_hint") or "connector"
            connector_id = edge.get("connector_id")
            confidence = edge.get("confidence")
            lines.append(
                f"- Page {page['page_number']}: `{from_text}` -> `{to_text}` "
                f"via `{connector_id}` ({hint}, confidence={confidence})."
            )
    return lines


def build_prompt_long_connector_lines(manifest: dict[str, Any]) -> list[str]:
    lines: list[str] = []
    for item in build_long_connectors(manifest):
        if item.get("scale_hint") != "ultra_long_connector":
            continue
        status = item["status"]
        relation = ""
        if status == "resolved_edge":
            relation = f" resolved `{item.get('from_text')}` -> `{item.get('to_text')}`"
        elif item.get("reason"):
            relation = f" unresolved `{item['reason']}`"
        lines.append(
            f"- Page {item['page_number']} `{item['connector_id']}`:{relation}; "
            f"{item.get('scale_hint')}, {item.get('color_hint') or 'unknown_color'}, {item.get('route_hint')}; "
            f"endpoints `{item.get('connector_endpoints')}`, bbox `{item.get('connector_bbox')}`."
        )
    return lines


def write_recognition_tasks(out_dir: Path, manifest: dict[str, Any]) -> None:
    tasks_dir = out_dir / "recognition_tasks"
    tasks_dir.mkdir(parents=True, exist_ok=True)
    write_json(out_dir / "text_blocks.schema.json", text_blocks_schema())

    for page in manifest["pages"]:
        task = {
            "page_number": page["page_number"],
            "overview": page["overview"],
            "instructions": [
                "Recognize handwritten and printed text visible in each tile.",
                "Return JSON only; do not summarize the image.",
                "Use tile-local pixel coordinates for bbox.",
                "Prefer detail tiles for precise bbox; agent_tiles are valid tile_id sources when continuous full-width context is needed.",
                "If a text item appears in multiple overlapping tiles, include it in each tile response; the pipeline will deduplicate.",
            ],
            "return_shape": {
                "page_number": page["page_number"],
                "text_blocks": [
                    {
                        "text": "recognized text",
                        "tile_id": "tile_0001",
                        "bbox": [0, 0, 100, 40],
                        "confidence": 0.8,
                    }
                ],
            },
            "agent_tiles": [
                {
                    "tile_id": tile["id"],
                    "image": tile["image"],
                    "global_pdf_bbox": tile["global_pdf_bbox"],
                    "pixel_width": tile["pixel_width"],
                    "pixel_height": tile["pixel_height"],
                }
                for tile in page.get("agent_tiles", [])
            ],
            "tiles": [
                {
                    "tile_id": tile["id"],
                    "image": tile["image"],
                    "global_pdf_bbox": tile["bbox"],
                    "pixel_width": tile["pixel_width"],
                    "pixel_height": tile["pixel_height"],
                }
                for tile in page["tiles"]
            ],
        }
        write_json(tasks_dir / f"page_{page['page_number']:03d}.json", task)


def text_blocks_schema() -> dict[str, Any]:
    return {
        "type": "object",
        "required": ["pages"],
        "properties": {
            "pages": {
                "type": "array",
                "items": {
                    "type": "object",
                    "required": ["page_number", "text_blocks"],
                    "properties": {
                        "page_number": {"type": "integer"},
                        "text_blocks": {
                            "type": "array",
                            "items": {
                                "type": "object",
                                "required": ["text", "tile_id", "bbox"],
                                "properties": {
                                    "id": {"type": "string"},
                                    "text": {"type": "string"},
                                    "tile_id": {"type": "string"},
                                    "bbox": {
                                        "type": "array",
                                        "items": {"type": "number"},
                                        "minItems": 4,
                                        "maxItems": 4,
                                    },
                                    "global_bbox": {
                                        "type": ["array", "null"],
                                        "items": {"type": "number"},
                                        "minItems": 4,
                                        "maxItems": 4,
                                    },
                                    "source": {"type": "string"},
                                    "confidence": {"type": ["number", "null"]},
                                },
                            },
                        },
                    },
                },
            }
        },
    }


def attach_text(out_dir: Path, text_blocks_path: Path) -> dict[str, Any]:
    out_dir = out_dir.expanduser().resolve()
    text_blocks_path = text_blocks_path.expanduser().resolve()
    manifest_path = out_dir / "manifest.json"

    if not manifest_path.is_file():
        raise FileNotFoundError(f"manifest not found: {manifest_path}")
    if not text_blocks_path.is_file():
        raise FileNotFoundError(f"text blocks JSON not found: {text_blocks_path}")

    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    payload = json.loads(text_blocks_path.read_text(encoding="utf-8"))
    incoming_by_page = collect_text_blocks_by_page(payload)

    for page in manifest["pages"]:
        page_index = page["page_index"]
        page_number = page["page_number"]
        incoming = incoming_by_page.get(page_number, []) + incoming_by_page.get(page_index, [])
        normalized = normalize_text_blocks_for_page(page, incoming)
        combined = page.get("text_blocks", []) + normalized
        page["text_blocks"] = assign_text_block_ids(page_number, dedupe_text_blocks(combined))
        edge_analysis = build_edge_analysis(page["drawing_paths"], page["text_blocks"])
        page["edge_candidates"] = edge_analysis["edge_candidates"]
        page["unresolved_connectors"] = edge_analysis["unresolved_connectors"]

        page_dir = out_dir / "pages" / f"page_{page_number:03d}"
        write_json(page_dir / "page.json", page)
        write_json(page_dir / "debug_edges.json", edge_analysis)

    write_json(manifest_path, manifest)
    graph = build_graph(manifest)
    write_json(out_dir / "graph.json", graph)
    write_text(out_dir / "prompt.md", build_prompt(manifest, (out_dir / "recognition_tasks").is_dir()))
    return graph


def collect_text_blocks_by_page(payload: Any) -> dict[int, list[dict[str, Any]]]:
    result: dict[int, list[dict[str, Any]]] = {}
    if isinstance(payload, list):
        result[1] = payload
        return result

    if not isinstance(payload, dict):
        raise ValueError("text blocks JSON must be an object or a list")

    if "pages" in payload:
        for page in payload["pages"]:
            page_number = int(page.get("page_number", page.get("page_index", 0) + 1))
            result.setdefault(page_number, []).extend(page.get("text_blocks", []))
        return result

    if "text_blocks" in payload:
        result[int(payload.get("page_number", 1))] = payload["text_blocks"]
        return result

    raise ValueError("text blocks JSON must contain `pages` or `text_blocks`")


def normalize_text_blocks_for_page(page: dict[str, Any], blocks: list[dict[str, Any]]) -> list[dict[str, Any]]:
    tiles_by_id = {tile["id"]: tile for tile in page["tiles"]}
    tiles_by_id.update({tile["id"]: tile for tile in page.get("agent_tiles", [])})
    normalized: list[dict[str, Any]] = []

    for block in blocks:
        text = str(block.get("text", "")).strip()
        if not text:
            continue

        tile_id = block.get("tile_id")
        local_bbox = block.get("bbox")
        global_bbox = block.get("global_bbox")

        if global_bbox is None:
            if not tile_id or tile_id not in tiles_by_id or local_bbox is None:
                continue
            global_bbox = tile_pixel_bbox_to_global(tiles_by_id[tile_id], local_bbox)
        else:
            global_bbox = round_rect(global_bbox)

        normalized.append(
            {
                "id": block.get("id"),
                "source_id": block.get("id"),
                "text": text,
                "bbox": round_rect(local_bbox) if local_bbox is not None else None,
                "tile_id": tile_id,
                "global_bbox": global_bbox,
                "source": block.get("source", "ai_text_blocks"),
                "confidence": block.get("confidence"),
            }
        )

    return normalized


def tile_pixel_bbox_to_global(tile: dict[str, Any], bbox: list[float]) -> list[float]:
    tile_bbox = tile["bbox"]
    scale_x = (tile_bbox[2] - tile_bbox[0]) / tile["pixel_width"]
    scale_y = (tile_bbox[3] - tile_bbox[1]) / tile["pixel_height"]
    return [
        round_float(tile_bbox[0] + float(bbox[0]) * scale_x),
        round_float(tile_bbox[1] + float(bbox[1]) * scale_y),
        round_float(tile_bbox[0] + float(bbox[2]) * scale_x),
        round_float(tile_bbox[1] + float(bbox[3]) * scale_y),
    ]


def dedupe_text_blocks(blocks: list[dict[str, Any]]) -> list[dict[str, Any]]:
    kept: list[dict[str, Any]] = []
    for block in blocks:
        if not block.get("text"):
            continue
        duplicate_index = find_duplicate_text_block(kept, block)
        if duplicate_index is None:
            kept.append(block)
        elif text_block_quality(block) > text_block_quality(kept[duplicate_index]):
            kept[duplicate_index] = block
    return kept


def find_duplicate_text_block(blocks: list[dict[str, Any]], block: dict[str, Any]) -> int | None:
    text_key = normalized_text_key(block["text"])
    rect = text_block_rect(block)
    center = rect_center(rect)
    for index, existing in enumerate(blocks):
        if normalized_text_key(existing["text"]) != text_key:
            continue
        existing_rect = text_block_rect(existing)
        if rect_iou(rect, existing_rect) >= 0.25 or point_distance(center, rect_center(existing_rect)) <= 48.0:
            return index
    return None


def normalized_text_key(text: str) -> str:
    return "".join(text.split()).lower()


def text_block_quality(block: dict[str, Any]) -> tuple[float, float]:
    confidence = block.get("confidence")
    confidence_value = float(confidence) if isinstance(confidence, int | float) else 0.0
    rect = text_block_rect(block)
    return confidence_value, rect.width * rect.height


def assign_text_block_ids(page_number: int, blocks: list[dict[str, Any]]) -> list[dict[str, Any]]:
    assigned: list[dict[str, Any]] = []
    for index, block in enumerate(blocks, start=1):
        item = dict(block)
        item["id"] = f"p{page_number:03d}_t{index:04d}"
        assigned.append(item)
    return assigned


def rect_iou(a: fitz.Rect, b: fitz.Rect) -> float:
    intersection = a & b
    if intersection.is_empty:
        return 0.0
    intersection_area = intersection.width * intersection.height
    union_area = a.width * a.height + b.width * b.height - intersection_area
    if union_area <= 0:
        return 0.0
    return intersection_area / union_area


def build_graph(manifest: dict[str, Any]) -> dict[str, Any]:
    nodes: list[dict[str, Any]] = []
    edges: list[dict[str, Any]] = []
    unresolved: list[dict[str, Any]] = []

    for page in manifest["pages"]:
        page_number = page["page_number"]
        for block in page.get("text_blocks", []):
            nodes.append(
                {
                    "id": block["id"],
                    "page_number": page_number,
                    "text": block["text"],
                    "bbox": block.get("global_bbox") or block.get("bbox"),
                    "source": block.get("source"),
                    "confidence": block.get("confidence"),
                }
            )
        for edge in page.get("edge_candidates", []):
            edge_item = dict(edge)
            edge_item["page_number"] = page_number
            edges.append(edge_item)
        for item in page.get("unresolved_connectors", []):
            unresolved_item = dict(item)
            unresolved_item["page_number"] = page_number
            unresolved.append(unresolved_item)

    return {
        "created_at": datetime.now(timezone.utc).isoformat(),
        "nodes": nodes,
        "edges": edges,
        "long_connectors": build_long_connectors(manifest),
        "unresolved_connectors": unresolved,
        "warnings": manifest.get("warnings", [])
        + [warning for page in manifest["pages"] for warning in page.get("warnings", [])],
    }


def build_long_connectors(manifest: dict[str, Any]) -> list[dict[str, Any]]:
    long_connectors: list[dict[str, Any]] = []
    for page in manifest["pages"]:
        page_number = page["page_number"]
        edges_by_connector = {edge["connector_id"]: edge for edge in page.get("edge_candidates", [])}
        unresolved_by_connector = {item["connector_id"]: item for item in page.get("unresolved_connectors", [])}

        for connector in page.get("drawing_paths", []):
            if connector.get("semantic_hint") != "long_distance_connector" or not connector.get("connector_candidate"):
                continue
            item = {
                "page_number": page_number,
                "connector_id": connector["id"],
                **connector_evidence(connector),
            }
            edge = edges_by_connector.get(connector["id"])
            unresolved = unresolved_by_connector.get(connector["id"])
            if edge is not None:
                item.update(
                    {
                        "status": "resolved_edge",
                        "edge_id": edge["id"],
                        "from_text": edge.get("from_text"),
                        "to_text": edge.get("to_text"),
                        "confidence": edge.get("confidence"),
                    }
                )
            elif unresolved is not None:
                item.update({"status": "unresolved", "reason": unresolved.get("reason")})
            else:
                item.update({"status": "unmatched"})
            long_connectors.append(item)
    long_connectors.sort(
        key=lambda item: (
            item.get("scale_hint") != "ultra_long_connector",
            item["page_number"],
            item["connector_id"],
        )
    )
    return long_connectors


def write_json(path: Path, payload: dict[str, Any]) -> None:
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def write_text(path: Path, text: str) -> None:
    path.write_text(text, encoding="utf-8")


def round_rect(rect: Any) -> list[float]:
    rect = fitz.Rect(rect)
    return [round_float(rect.x0), round_float(rect.y0), round_float(rect.x1), round_float(rect.y1)]


def round_point(point: Any) -> list[float]:
    point = fitz.Point(point)
    return [round_float(point.x), round_float(point.y)]


def round_float(value: float | int | None) -> float:
    if value is None:
        return 0.0
    return round(float(value), 3)
