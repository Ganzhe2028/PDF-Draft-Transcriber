from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from .core import AgentTileSettings, PrepSettings, attach_text, prepare_pdf


def build_prepare_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="goodnotes-prep",
        description=(
            "Prepare a GoodNotes-exported PDF as an AI-friendly package with "
            "overview images, overlapping tiles, text blocks, drawing paths, "
            "and connection candidates."
        ),
    )
    parser.add_argument("source_pdf", type=Path, help="GoodNotes Editable PDF or source PDF.")
    parser.add_argument("--out", required=True, type=Path, help="Output directory. Must be empty or absent.")
    parser.add_argument(
        "--ocr-pdf",
        type=Path,
        default=None,
        help="Optional GoodNotes Flattened PDF with handwriting recognition enabled.",
    )
    parser.add_argument(
        "--emit-recognition-tasks",
        action="store_true",
        help="Write recognition_tasks/page_XXX.json and text_blocks.schema.json for AI/VLM text block backfill.",
    )
    parser.add_argument(
        "--emit-agent-tiles",
        action="store_true",
        help="Write full-width overlapping JPEG slices under agent_tiles/ for continuous multimodal-agent reading.",
    )
    parser.add_argument("--dpi", type=int, default=180, help="Tile render DPI. Default: 180.")
    parser.add_argument("--tile-px", type=int, default=1600, help="Tile width/height in pixels. Default: 1600.")
    parser.add_argument("--overlap-px", type=int, default=384, help="Tile overlap in pixels. Default: 384.")
    parser.add_argument(
        "--global-max-px",
        type=int,
        default=2400,
        help="Maximum long edge for overview images. Default: 2400.",
    )
    parser.add_argument("--agent-dpi", type=int, default=200, help="Agent tile render DPI. Default: 200.")
    parser.add_argument(
        "--agent-tile-height",
        type=int,
        default=2000,
        help="Full-width agent tile height in pixels. Default: 2000.",
    )
    parser.add_argument(
        "--agent-overlap",
        type=float,
        default=0.05,
        help="Agent tile overlap ratio between 0 and 1. Default: 0.05.",
    )
    parser.add_argument(
        "--agent-quality",
        type=int,
        default=90,
        help="Agent tile JPEG quality from 1 to 100. Default: 90.",
    )
    return parser


def build_attach_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="goodnotes-prep attach-text",
        description="Attach AI/VLM-recognized text blocks to a prepared output folder and build graph.json.",
    )
    parser.add_argument("output_dir", type=Path, help="Existing goodnotes-prep output directory.")
    parser.add_argument("--text-blocks", required=True, type=Path, help="JSON file containing recognized text blocks.")
    return parser


def main(argv: list[str] | None = None) -> int:
    argv = list(sys.argv[1:] if argv is None else argv)
    if argv and argv[0] == "attach-text":
        parser = build_attach_parser()
        args = parser.parse_args(argv[1:])
        graph = attach_text(args.output_dir, args.text_blocks)
        summary = {
            "output": str(args.output_dir),
            "nodes": len(graph["nodes"]),
            "edges": len(graph["edges"]),
            "unresolved_connectors": len(graph["unresolved_connectors"]),
            "warnings": len(graph["warnings"]),
        }
        print(json.dumps(summary, ensure_ascii=False, indent=2))
        return 0

    parser = build_prepare_parser()
    args = parser.parse_args(argv)

    settings = PrepSettings(
        dpi=args.dpi,
        tile_px=args.tile_px,
        overlap_px=args.overlap_px,
        global_max_px=args.global_max_px,
    )
    agent_tile_settings = AgentTileSettings(
        dpi=args.agent_dpi,
        tile_height_px=args.agent_tile_height,
        overlap=args.agent_overlap,
        quality=args.agent_quality,
    )
    manifest = prepare_pdf(
        args.source_pdf,
        args.out,
        args.ocr_pdf,
        settings,
        emit_recognition_tasks=args.emit_recognition_tasks,
        emit_agent_tiles=args.emit_agent_tiles,
        agent_tile_settings=agent_tile_settings,
    )

    summary = {
        "output": str(args.out),
        "pages": len(manifest["pages"]),
        "tiles": sum(len(page["tiles"]) for page in manifest["pages"]),
        "agent_tiles": sum(len(page.get("agent_tiles", [])) for page in manifest["pages"]),
        "text_blocks": sum(len(page["text_blocks"]) for page in manifest["pages"]),
        "connector_candidates": sum(
            sum(1 for item in page["drawing_paths"] if item["connector_candidate"])
            for page in manifest["pages"]
        ),
        "edge_candidates": sum(len(page["edge_candidates"]) for page in manifest["pages"]),
        "warnings": len(manifest["warnings"])
        + sum(len(page["warnings"]) for page in manifest["pages"]),
        "recognition_tasks": bool(args.emit_recognition_tasks),
        "agent_tiles_enabled": bool(args.emit_agent_tiles),
    }
    print(json.dumps(summary, ensure_ascii=False, indent=2))
    return 0
