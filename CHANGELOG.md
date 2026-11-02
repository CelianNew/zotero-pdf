# Changelog

All notable changes to this skill are documented here. The format follows
[Keep a Changelog](https://keepachangelog.com/en/1.1.0/), and releases use
[Semantic Versioning](https://semver.org/).

## [1.0.1] - 2026-11-02

### Added

- Resolve one existing parent item by DOI, exact title, and optional author guards.
- Inspect bibliographic evidence, tag vocabulary, collection paths, and child attachments before writing.
- Verify a stored PDF through Zotero by file header, MD5, and optional byte count.
- Classify from full text, HTML, or metadata with an explicit evidence level.
- Add bounded `429` handling that reuses the current authorization key, plus one
  automatic `401` reauthorization attempt.
- Add a detailed troubleshooting reference for authorization, metadata conflicts, missing PDFs, and interrupted uploads.

### Changed

- Treat the `zotero` companion skill, Computer Use, and Zotero browser connector as optional.
- Document the full dependency set, local API setup, first-write GUI requirement, and no-PDF fallback behavior.
- Recommend **Always Allow** for multi-step attachment flows while keeping the security decision with the user.
- Stream PDF uploads instead of loading the complete file into memory.

### Fixed

- Respect single-use versus remembered Zotero write authorizations.
- Parse response headers case-insensitively.
- Normalize DOI URL prefixes and title whitespace before matching.
- Avoid additional authorization prompts when cleaning up an interrupted attachment.
- Prevent attachment verification output from exposing a local absolute file path.

## [1.0.0] - 2026-11-02

### Added

- Attach a verified PDF to an existing Zotero 10 item through the local API.
- Inspect item tags and collections plus the full library classification vocabulary.
- Append evidence-based tags and collection membership.
- Preview classification changes with `--dry-run`.
- Verify writes with optimistic concurrency and post-write read-back.
- Locate Zotero and authorization windows with Computer Use while leaving security decisions to the user.
- English and Simplified Chinese documentation.
