# Images — Agent Guide

## Responsibility

`apps/images/` owns universal metadata for externally stored image variants. It
stores R2 object keys and dimensions only; it does not know consuming domain
models or manage storage, URLs, uploads, processing, or orphan cleanup.

## Model Contract

- `Image` is independent and uses `SlotyCreatedModel` (`created` only).
- `display_key` and `thumbnail_key` are unique object keys, not URLs.
- Entity ownership and arrangement belong to explicit bridges in consuming
  domains. `CourtImage` is owned by `apps/courts/`, not this app.
- Do not use generic relations, content types, or `object_id` for image links.

## Lifecycle

An Image attached through a consuming-domain bridge must be protected from
direct deletion. Removing a bridge detaches the Image only; later storage and
