# Security Policy

## Supported Versions

This project is in early development. Security reports should target the current `main` branch unless a released version states otherwise.

## Reporting a Vulnerability

Please report vulnerabilities privately through the project's issue tracker or maintainer contact once one is published.

Do not include real GoodNotes exports, private notes, credentials, or personal data in public issues.

## Data Handling

`goodnotes-pdf-prep` runs locally and does not call AI/OCR APIs. However, its generated output can contain sensitive material:

- rendered note images
- recognized handwriting
- local source file paths
- generated graph and connector data
- downstream model outputs

Never commit real generated output folders, private PDFs, `.goodnotes` files, or OCR/VLM result files unless they were intentionally sanitized for public release.

The default `.gitignore` excludes common local output paths and private GoodNotes files.
