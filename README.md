# goodnotes-pdf-prep

`goodnotes-pdf-prep` is a local CLI that turns a GoodNotes-exported PDF into an AI-friendly evidence package for multimodal agents.

It is designed for large whiteboard-style notes where direct PDF upload often loses handwriting detail, long arrows, and far-apart visual relationships.

The tool does not call any AI or OCR API. It prepares the evidence package; a downstream multimodal model or OCR system can then read the generated images and return structured text blocks.

## Features

- Render a full-page overview image for global layout.
- Render overlapping detail tiles with global PDF coordinates.
- Render full-width `agent_tiles` for continuous reading of long connectors.
- Extract existing PDF text layers when available.
- Extract vector drawing paths and connector candidates from editable PDFs.
- Generate `graph.json`, `manifest.json`, `prompt.md`, recognition tasks, and a text block schema.
- Attach externally recognized text blocks and rebuild graph edge candidates.

## Install

```bash
uv sync
```

Run the CLI from the project checkout:

```bash
uv run goodnotes-prep --help
```

## Basic Usage

Prepare a PDF:

```bash
uv run goodnotes-prep SOURCE.pdf --out OUT_DIR
```

Recommended mode for large GoodNotes whiteboards:

```bash
uv run goodnotes-prep SOURCE.pdf \
  --out OUT_DIR \
  --ocr-pdf FLATTENED_OCR.pdf \
  --emit-recognition-tasks \
  --emit-agent-tiles
```

Then give the generated `OUT_DIR` folder to a multimodal agent and ask it to follow `prompt.md`.

If a model returns structured text blocks, attach them:

```bash
uv run goodnotes-prep attach-text OUT_DIR --text-blocks TEXT_BLOCKS.json
```

## Output

The output folder contains:

- `manifest.json`: page index, image paths, tile coordinates, text blocks, drawing paths, warnings.
- `graph.json`: nodes, edge candidates, unresolved connectors, and long connector review items.
- `prompt.md`: instructions for downstream multimodal agents.
- `pages/page_XXX/overview.png`: low-resolution global page overview.
- `pages/page_XXX/tiles/*.png`: overlapping high-resolution detail tiles.
- `agent_tiles/*.jpg`: full-width slices for reading long-range visual flow.
- `recognition_tasks/page_XXX.json`: OCR/VLM task package.
- `text_blocks.schema.json`: expected text block backfill shape.

## Text Block Backfill

External OCR/VLM output should follow this shape:

```json
{
  "pages": [
    {
      "page_number": 1,
      "text_blocks": [
        {
          "text": "recognized text",
          "tile_id": "tile_0001",
          "bbox": [0, 0, 100, 40],
          "confidence": 0.8
        }
      ]
    }
  ]
}
```

`bbox` is tile-local pixel coordinates. The pipeline converts it to global PDF coordinates during `attach-text`.

## Privacy

Generated output folders can contain your actual notes, rendered images, local file paths, and derived graph data. Do not commit generated outputs or real GoodNotes exports to a public repository.

This repository's `.gitignore` excludes local outputs and private GoodNotes files by default.

## Development

```bash
uv run pytest
```

Project planning docs:

- `agents.md`: development and agent operating notes.
- `context.md`: public project context and design decisions.
- `PRD.md`: product requirements.

## License

MIT
