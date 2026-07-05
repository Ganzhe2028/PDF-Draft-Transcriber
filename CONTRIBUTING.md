# Contributing

Contributions are welcome after the project is published.

## Development

```bash
uv sync
uv run pytest
```

## Privacy Rules

Do not add real GoodNotes exports, rendered note images, generated output folders, personal paths, or model outputs from private notes.

Use synthetic PDFs or clearly sanitized examples for tests and documentation.

## Pull Request Checklist

- Tests pass with `uv run pytest`.
- New CLI behavior is documented in `README.md`.
- Changes to output schemas or agent handoff behavior are reflected in `agents.md`, `context.md`, and `PRD.md`.
- No generated `outputs/`, `work/`, private PDFs, or `.goodnotes` files are included.
