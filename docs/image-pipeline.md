# Court Image Pipeline

## Ownership and delivery

`apps/images.Image` is universal and contains only immutable R2 object keys,
final display dimensions, and the canonical `created` timestamp. It does not
know Court. `apps/courts.CourtImage` is the explicit relationship and owns
display order; no generic relations are used.

The API returns derived URLs, never storage internals. Normal delivery is
browser → public Cloudflare image domain → R2. Django does not stream, resize,
or transform image bytes on reads.

## Upload contract

`POST /api/v1/clubs/{club_slug}/courts/{court_id}/images/` accepts
`multipart/form-data` with one `image` field. Clients cannot provide keys,
dimensions, variants, or URLs. The server decodes actual content rather than
trusting filename or Content-Type, accepts JPEG/PNG/WebP only, and rejects
corrupt data, oversized files, decompression-bomb warnings, excessive
dimensions, and excessive pixel counts with Sloty API errors.

The source exists only in memory during the request. Processing applies EXIF
orientation, removes source metadata by re-encoding, converts unusual modes,
and composites transparency on white. It generates exactly two permanent
WebP objects without upscaling:

| Variant | Maximum long edge | Quality | Intended use |
| --- | ---: | ---: | --- |
| display | 1920px | 84 | detail and gallery |
| thumbnail | 640px | 78 | cards, lists, previews |

The generated `images/{uuid}/display.webp` and
`images/{uuid}/thumbnail.webp` keys are immutable and independent of mutable
Court identity. Both R2 puts use `Content-Type: image/webp` and
`Cache-Control: public, max-age=31536000, immutable`.

## Failure and deletion behavior

The service processes, uploads display, uploads thumbnail, then creates `Image`
and `CourtImage` together in an atomic database transaction. A failed thumbnail
upload attempts display cleanup; a failed database write attempts both object
cleanups. Cleanup errors are logged and do not conceal the original failure.
At no point is a database Image committed with a missing R2 object.

New relationships append their position while the Court is locked. PUT to
`images/reorder/` requires the full current `CourtImage` ID set and atomically
rewrites positions to contiguous `0..N-1` values.

DELETE removes only the CourtImage bridge first. If other known Court bridges
still reference the Image, storage is untouched. Otherwise both objects are
deleted before the Image row; any R2 cleanup failure leaves the unattached row
for a future `cleanup_orphan_image(image_id=...)` retry rather than risking a
broken reference or harming another consumer.

## Operations and tests

The backend service loads these private production environment variables from
`/etc/sloty/env/prod.env`:

```text
R2_ENDPOINT
R2_ACCESS_KEY_ID
R2_SECRET_ACCESS_KEY
R2_BUCKET_IMAGE
IMAGES_PUBLIC_BASE_URL
```

Optional centralized input-limit variables are
`IMAGE_MAX_UPLOAD_BYTES`, `IMAGE_MAX_WIDTH`, `IMAGE_MAX_HEIGHT`, and
`IMAGE_MAX_PIXELS`. Do not expose R2 write
credentials, endpoint details, buckets, or user-uploaded filenames in API
responses. Production must provision the R2 bucket, least-privilege S3 API
credentials for that bucket, and a public Cloudflare/CDN custom-domain mapping
for `IMAGES_PUBLIC_BASE_URL`; this repository does not create those cloud
resources. PostgreSQL backup storage uses the same `R2_ENDPOINT`,
`R2_ACCESS_KEY_ID`, and `R2_SECRET_ACCESS_KEY` connection configuration with
its own `R2_BUCKET_BACKUP` in `backup.env`; image storage must never select
that bucket. The Cloudflare API token must have access to both named buckets.
Tests mock the storage boundary and must make zero R2 network calls.
