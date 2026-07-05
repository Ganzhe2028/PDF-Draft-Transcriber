from __future__ import annotations

import json
from pathlib import Path

import fitz
from PIL import Image

from goodnotes_prep.core import (
    AgentTileSettings,
    PrepSettings,
    attach_text,
    build_edge_analysis,
    build_agent_tile_bboxes,
    build_tile_bboxes,
    prepare_pdf,
    tile_pixel_bbox_to_global,
)


def test_tile_grid_covers_page_without_going_out_of_bounds() -> None:
    settings = PrepSettings(dpi=100, tile_px=500, overlap_px=100, global_max_px=800)
    bboxes = build_tile_bboxes(1000, 700, settings)

    assert bboxes[0][0] == 0
    assert bboxes[0][1] == 0
    assert max(box[2] for box in bboxes) == 1000
    assert max(box[3] for box in bboxes) == 700

    for x0, y0, x1, y1 in bboxes:
        assert 0 <= x0 < x1 <= 1000
        assert 0 <= y0 < y1 <= 700


def test_agent_tile_bboxes_are_full_width_and_overlapping() -> None:
    settings = AgentTileSettings(dpi=72, tile_height_px=300, overlap=0.25, quality=90)
    bboxes = build_agent_tile_bboxes(1200, 800, settings)

    assert bboxes[0] == [0.0, 0.0, 1200, 300.0]
    assert [box[1] for box in bboxes] == [0.0, 225.0, 450.0, 675.0]
    assert bboxes[-1][3] == 800
    assert bboxes[-1][3] - bboxes[-1][1] == 125.0
    assert [bboxes[index][3] - bboxes[index + 1][1] for index in range(len(bboxes) - 1)] == [75.0, 75.0, 75.0]
    assert all(box[0] == 0.0 and box[2] == 1200 for box in bboxes)
    assert bboxes[0][3] - bboxes[1][1] == 75.0

    for x0, y0, x1, y1 in bboxes:
        assert 0 <= x0 < x1 <= 1200
        assert 0 <= y0 < y1 <= 800


def test_unresolved_long_connector_keeps_evidence() -> None:
    analysis = build_edge_analysis(
        [
            {
                "id": "d_long",
                "bbox": [0, 0, 3000, 800],
                "endpoints": [[3000, 0], [0, 800]],
                "points": [[3000, 0], [3000, 800], [0, 800]],
                "connector_candidate": True,
                "semantic_hint": "long_distance_connector",
                "color_hint": "dark",
                "stroke_color": [0.1, 0.1, 0.1],
                "total_length_pt": 3800,
            }
        ],
        [],
    )

    unresolved = analysis["unresolved_connectors"][0]
    assert unresolved["connector_id"] == "d_long"
    assert unresolved["scale_hint"] == "ultra_long_connector"
    assert unresolved["connector_endpoints"] == [[3000, 0], [0, 800]]
    assert unresolved["connector_anchor_points"] == [[3000.0, 0.0], [3000.0, 800.0], [0.0, 800.0]]
    assert unresolved["route_hint"] == "orthogonal_polyline: vertical -> horizontal"


def test_prepare_pdf_outputs_manifest_tiles_text_and_edges(tmp_path: Path) -> None:
    source_pdf = tmp_path / "synthetic.pdf"
    make_synthetic_pdf(source_pdf)

    out_dir = tmp_path / "out"
    manifest = prepare_pdf(
        source_pdf,
        out_dir,
        settings=PrepSettings(dpi=72, tile_px=400, overlap_px=100, global_max_px=800),
    )

    page = manifest["pages"][0]
    assert (out_dir / "manifest.json").is_file()
    assert (out_dir / "prompt.md").is_file()
    assert (out_dir / page["overview"]).is_file()
    assert (out_dir / "pages/page_001/page.json").is_file()
    assert all((out_dir / tile["image"]).is_file() for tile in page["tiles"])
    assert len(page["text_blocks"]) >= 3
    assert any(path["connector_candidate"] for path in page["drawing_paths"])
    assert len(page["edge_candidates"]) >= 2
    assert (out_dir / "graph.json").is_file()
    assert (out_dir / "pages/page_001/debug_edges.json").is_file()

    saved_manifest = json.loads((out_dir / "manifest.json").read_text(encoding="utf-8"))
    assert saved_manifest["pages"][0]["page_number"] == 1


def test_prepare_pdf_emits_agent_tiles_metadata_and_prompt(tmp_path: Path) -> None:
    source_pdf = tmp_path / "synthetic.pdf"
    make_synthetic_pdf(source_pdf)

    out_dir = tmp_path / "out"
    manifest = prepare_pdf(
        source_pdf,
        out_dir,
        settings=PrepSettings(dpi=72, tile_px=400, overlap_px=100, global_max_px=800),
        emit_recognition_tasks=True,
        emit_agent_tiles=True,
        agent_tile_settings=AgentTileSettings(dpi=72, tile_height_px=300, overlap=0.25, quality=85),
    )

    page = manifest["pages"][0]
    metadata = json.loads((out_dir / "agent_tiles/metadata.json").read_text(encoding="utf-8"))
    task = json.loads((out_dir / "recognition_tasks/page_001.json").read_text(encoding="utf-8"))
    prompt = (out_dir / "prompt.md").read_text(encoding="utf-8")

    assert len(page["agent_tiles"]) == 4
    assert metadata["tile_count"] == 4
    assert metadata["settings"]["enabled"] is True
    assert all((out_dir / tile["image"]).is_file() for tile in page["agent_tiles"])
    assert all(not Path(tile["image"]).is_absolute() for tile in page["agent_tiles"])
    assert page["agent_tiles"][0]["global_pdf_bbox"] == [0.0, 0.0, 1200.0, 300.0]
    assert page["agent_tiles"][1]["overlap_top_px"] == 75
    assert page["agent_tiles"][-1]["pixel_height"] == 125
    assert task["agent_tiles"][0]["tile_id"] == "agent_tile_000"
    assert "agent_tiles/metadata.json" in prompt
    assert "Long arrows must appear as explicit relationship lines" in prompt

    for tile in page["agent_tiles"]:
        with Image.open(out_dir / tile["image"]) as image:
            assert image.format == "JPEG"
            assert image.width == tile["pixel_width"] == 1200
            assert image.height == tile["pixel_height"]
            assert image.height <= 300


def test_attach_text_accepts_agent_tile_local_bbox(tmp_path: Path) -> None:
    source_pdf = tmp_path / "synthetic.pdf"
    make_synthetic_pdf(source_pdf)

    out_dir = tmp_path / "out"
    prepare_pdf(
        source_pdf,
        out_dir,
        settings=PrepSettings(dpi=72, tile_px=400, overlap_px=100, global_max_px=800),
        emit_agent_tiles=True,
        agent_tile_settings=AgentTileSettings(dpi=72, tile_height_px=300, overlap=0.25, quality=85),
    )

    text_blocks_path = tmp_path / "text_blocks.json"
    text_blocks_path.write_text(
        json.dumps(
            {
                "pages": [
                    {
                        "page_number": 1,
                        "text_blocks": [
                            {
                                "text": "Agent tile text",
                                "tile_id": "agent_tile_000",
                                "bbox": [100, 90, 220, 140],
                                "confidence": 0.8,
                            }
                        ],
                    }
                ]
            }
        ),
        encoding="utf-8",
    )

    graph = attach_text(out_dir, text_blocks_path)

    node = next(node for node in graph["nodes"] if node["text"] == "Agent tile text")
    assert node["bbox"] == [100.0, 90.0, 220.0, 140.0]


def test_agent_tiles_have_unique_ids_across_pages(tmp_path: Path) -> None:
    source_pdf = tmp_path / "two-page.pdf"
    make_two_page_pdf(source_pdf)

    out_dir = tmp_path / "out"
    manifest = prepare_pdf(
        source_pdf,
        out_dir,
        settings=PrepSettings(dpi=72, tile_px=400, overlap_px=100, global_max_px=800),
        emit_recognition_tasks=True,
        emit_agent_tiles=True,
        agent_tile_settings=AgentTileSettings(dpi=72, tile_height_px=250, overlap=0.2, quality=85),
    )

    all_agent_tiles = [tile for page in manifest["pages"] for tile in page["agent_tiles"]]
    ids = [tile["id"] for tile in all_agent_tiles]
    page_1_task = json.loads((out_dir / "recognition_tasks/page_001.json").read_text(encoding="utf-8"))
    page_2_task = json.loads((out_dir / "recognition_tasks/page_002.json").read_text(encoding="utf-8"))

    assert len(ids) == len(set(ids))
    assert all(tile["page_number"] == 1 for tile in manifest["pages"][0]["agent_tiles"])
    assert all(tile["page_number"] == 2 for tile in manifest["pages"][1]["agent_tiles"])
    assert [tile["tile_id"] for tile in page_1_task["agent_tiles"]] == [
        tile["id"] for tile in manifest["pages"][0]["agent_tiles"]
    ]
    assert [tile["tile_id"] for tile in page_2_task["agent_tiles"]] == [
        tile["id"] for tile in manifest["pages"][1]["agent_tiles"]
    ]


def test_blank_pdf_emits_warnings_but_still_outputs_package(tmp_path: Path) -> None:
    source_pdf = tmp_path / "blank.pdf"
    doc = fitz.open()
    doc.new_page(width=500, height=300)
    doc.save(source_pdf)
    doc.close()

    out_dir = tmp_path / "blank_out"
    manifest = prepare_pdf(
        source_pdf,
        out_dir,
        settings=PrepSettings(dpi=72, tile_px=300, overlap_px=60, global_max_px=500),
    )

    page = manifest["pages"][0]
    assert (out_dir / "manifest.json").is_file()
    assert page["warnings"]
    assert any("No extractable text layer" in warning for warning in page["warnings"])
    assert any("No vector drawing paths" in warning for warning in page["warnings"])


def test_prepare_pdf_emits_recognition_tasks(tmp_path: Path) -> None:
    source_pdf = tmp_path / "synthetic.pdf"
    make_synthetic_pdf(source_pdf)

    out_dir = tmp_path / "out"
    prepare_pdf(
        source_pdf,
        out_dir,
        settings=PrepSettings(dpi=72, tile_px=400, overlap_px=100, global_max_px=800),
        emit_recognition_tasks=True,
    )

    task = json.loads((out_dir / "recognition_tasks/page_001.json").read_text(encoding="utf-8"))
    schema = json.loads((out_dir / "text_blocks.schema.json").read_text(encoding="utf-8"))
    prompt = (out_dir / "prompt.md").read_text(encoding="utf-8")

    assert task["page_number"] == 1
    assert task["tiles"][0]["tile_id"] == "tile_0001"
    assert schema["required"] == ["pages"]
    assert "Recognition Task Mode" in prompt
    assert "Evidence Priority" in prompt


def test_tile_pixel_bbox_to_global_converts_tile_local_pixels() -> None:
    tile = {
        "bbox": [100.0, 200.0, 500.0, 600.0],
        "pixel_width": 1000,
        "pixel_height": 1000,
    }

    assert tile_pixel_bbox_to_global(tile, [250, 100, 750, 900]) == [200.0, 240.0, 400.0, 560.0]


def test_attach_text_generates_graph_dedupes_and_resolves_long_edge(tmp_path: Path) -> None:
    source_pdf = tmp_path / "synthetic.pdf"
    make_synthetic_pdf(source_pdf)

    out_dir = tmp_path / "out"
    prepare_pdf(
        source_pdf,
        out_dir,
        settings=PrepSettings(dpi=72, tile_px=400, overlap_px=100, global_max_px=800),
        emit_recognition_tasks=True,
    )

    text_blocks_path = tmp_path / "text_blocks.json"
    text_blocks_path.write_text(
        json.dumps(
            {
                "pages": [
                    {
                        "page_number": 1,
                        "text_blocks": [
                            {
                                "text": "Root",
                                "tile_id": "tile_0001",
                                "bbox": [80, 80, 200, 125],
                                "confidence": 0.6,
                            },
                            {
                                "text": "Root",
                                "tile_id": "tile_0001",
                                "bbox": [82, 82, 202, 127],
                                "confidence": 0.9,
                            },
                            {
                                "text": "Branch A",
                                "global_bbox": [760, 460, 930, 510],
                                "confidence": 0.9,
                            },
                            {
                                "text": "Branch B",
                                "global_bbox": [820, 130, 1000, 180],
                                "confidence": 0.9,
                            },
                            {
                                "text": "Question: why does it work?",
                                "global_bbox": [875, 620, 1060, 675],
                                "confidence": 0.95,
                            },
                            {
                                "text": "Underlying theory",
                                "global_bbox": [105, 650, 290, 705],
                                "confidence": 0.95,
                            },
                        ],
                    }
                ]
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )

    graph = attach_text(out_dir, text_blocks_path)
    node_texts = [node["text"] for node in graph["nodes"]]
    long_edges = [edge for edge in graph["edges"] if edge.get("semantic_hint") == "long_distance_connector"]

    assert node_texts.count("Root") == 1
    assert "Question: why does it work?" in node_texts
    assert "Underlying theory" in node_texts
    assert any(
        edge["from_text"] == "Underlying theory" and edge["to_text"] == "Question: why does it work?"
        or edge["from_text"] == "Question: why does it work?" and edge["to_text"] == "Underlying theory"
        for edge in long_edges
    )
    assert graph["long_connectors"]
    assert any(item["status"] == "resolved_edge" for item in graph["long_connectors"])
    prompt = (out_dir / "prompt.md").read_text(encoding="utf-8")
    assert "Graph Edges To Preserve" in prompt
    assert "Long Connectors To Review Separately" in prompt
    assert (
        "`Question: why does it work?` -> `Underlying theory`" in prompt
        or "`Underlying theory` -> `Question: why does it work?`" in prompt
    )
    assert (out_dir / "graph.json").is_file()
    assert (out_dir / "pages/page_001/debug_edges.json").is_file()


def test_output_directory_must_be_empty(tmp_path: Path) -> None:
    source_pdf = tmp_path / "synthetic.pdf"
    make_synthetic_pdf(source_pdf)
    out_dir = tmp_path / "out"
    out_dir.mkdir()
    (out_dir / "existing.txt").write_text("keep", encoding="utf-8")

    try:
        prepare_pdf(source_pdf, out_dir, settings=PrepSettings(dpi=72, tile_px=400, overlap_px=100))
    except FileExistsError as exc:
        assert "must be empty" in str(exc)
    else:
        raise AssertionError("expected FileExistsError")


def make_synthetic_pdf(path: Path) -> None:
    doc = fitz.open()
    page = doc.new_page(width=1200, height=800)

    page.insert_textbox(fitz.Rect(80, 80, 200, 125), "Root", fontsize=18)
    page.insert_textbox(fitz.Rect(760, 460, 930, 510), "Branch A", fontsize=18)
    page.insert_textbox(fitz.Rect(820, 130, 1000, 180), "Branch B", fontsize=18)

    page.draw_line(fitz.Point(200, 102), fitz.Point(760, 485), color=(0, 0, 0), width=2)
    page.draw_line(fitz.Point(200, 100), fitz.Point(820, 155), color=(0, 0, 0), width=2)
    page.draw_line(fitz.Point(760, 485), fitz.Point(745, 480), color=(0, 0, 0), width=2)
    page.draw_line(fitz.Point(760, 485), fitz.Point(752, 497), color=(0, 0, 0), width=2)
    page.draw_line(fitz.Point(290, 680), fitz.Point(875, 650), color=(0, 0.7, 0.6), width=2)

    doc.save(path)
    doc.close()


def make_two_page_pdf(path: Path) -> None:
    doc = fitz.open()
    page_1 = doc.new_page(width=600, height=500)
    page_1.insert_textbox(fitz.Rect(50, 50, 220, 100), "Page 1", fontsize=18)
    page_2 = doc.new_page(width=600, height=500)
    page_2.insert_textbox(fitz.Rect(50, 50, 220, 100), "Page 2", fontsize=18)
    doc.save(path)
    doc.close()
