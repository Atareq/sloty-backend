# Images — Agent Guide

## Responsibility

`apps/images/` owns universal metadata plus the shared, Court-independent image
pipeline utilities. It stores R2 object keys and dimensions only; it never
knows consuming domain models.

## Model Contract

- `Image` is independent and uses `SlotyCreatedModel` (`created` only).
- `display_key` and `thumbnail_key` are unique object keys, not URLs.
- Entity ownership and arrangement belong to explicit bridges in consuming
  domains. `CourtImage` is owned by `apps/courts/`, not this app.
- Do not use generic relations, content types, or `object_id` for image links.

## Lifecycle

An Image attached through a consuming-domain bridge must be protected from
direct deletion. Removing a bridge detaches the Image only; later storage and
orphan-cleanup policy belongs to the consuming domain.

## Shared Pipeline Utilities

- `processing.py` accepts one decoded JPEG, PNG, or WebP source, rejects bad
  bytes/unsafe dimensions, normalizes EXIF orientation, strips source metadata,
  flattens transparency to white, and returns display + thumbnail WebP bytes.
  It does not know Court, HTTP, R2 credentials, or database relationships.
- Display is max 1920px at WebP quality 84; thumbnail is max 640px at quality
  78. Neither variant upscales a smaller source. Source files are never stored.
- `storage.py` is the sole R2/S3-compatible boundary. It uploads immutable
  `image/webp` objects with year-long immutable caching and derives CDN URLs at
  serialization time from `IMAGES_PUBLIC_BASE_URL`; URLs never enter the
  database. It selects only `R2_BUCKET_IMAGE`; it must never select backup
  storage. Its shared connection is `R2_ENDPOINT`, `R2_ACCESS_KEY_ID`, and
  `R2_SECRET_ACCESS_KEY`; R2 credential/configuration errors must not be
  exposed to clients.
- R2 bucket names are purpose-specific. Images use `R2_BUCKET_IMAGE`; backups
  use `R2_BUCKET_BACKUP`. Do not introduce an unqualified bucket setting or a
  compatibility alias; any future bucket needs its own purpose-specific name.
- Any future bridge creation/deletion service must preserve the Phase 2 order:
  process, upload display, upload thumbnail, then atomically persist the Image
  and bridge. Failed uploads/DB writes attempt object cleanup and never leave a
  database image referencing absent objects.
