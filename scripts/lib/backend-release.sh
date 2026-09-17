# Shared release-metadata and version helpers for Sloty backend deployment.

RELEASE_TAG_PATTERN='^v[0-9]+\.[0-9]+\.[0-9]+$'
COMMIT_SHA_PATTERN='^[0-9a-fA-F]{40}$'

validate_backend_release_args() {
  local commit_sha="${1:-}"
  local release_tag="${2:-}"

  if [[ ! "$commit_sha" =~ $COMMIT_SHA_PATTERN ]]; then
    echo "Invalid commit SHA: expected exactly 40 hexadecimal characters." >&2
    return 1
  fi

  if [[ ! "$release_tag" =~ $RELEASE_TAG_PATTERN ]]; then
    echo "Invalid release tag: expected v<number>.<number>.<number>." >&2
    return 1
  fi
}

resolve_production_release_tag() {
  local sha="${1:?}"
  local existing_tag latest_tag version major minor patch

  existing_tag="$(
    git tag --points-at "$sha" --list 'v[0-9]*.[0-9]*.[0-9]*' \
      | sort -V \
      | tail -n 1
  )"
  if [[ -n "$existing_tag" ]]; then
    printf '%s\n' "$existing_tag"
    return 0
  fi

  latest_tag="$(
    git tag --list 'v[0-9]*.[0-9]*.[0-9]*' --sort=-v:refname \
      | head -n 1
  )"
  if [[ -z "$latest_tag" ]]; then
    printf '%s\n' "v0.1.0"
    return 0
  fi

  version="${latest_tag#v}"
  IFS='.' read -r major minor patch <<< "$version"
  printf 'v%s.%s.0\n' "$major" "$((minor + 1))"
}

release_tag_sha_status() {
  local release_tag="${1:?}"
  local commit_sha="${2:?}"
  local tag_sha

  if ! git rev-parse -q --verify "refs/tags/${release_tag}^{commit}" >/dev/null; then
    printf '%s\n' "missing"
    return 0
  fi

  tag_sha="$(git rev-list -n 1 "refs/tags/${release_tag}")"
  if [[ "$tag_sha" == "$commit_sha" ]]; then
    printf '%s\n' "match"
    return 0
  fi

  printf '%s\n' "conflict"
}

write_backend_release_metadata() {
  local commit_sha="${1:?}"
  local release_tag="${2:?}"
  local metadata_dir="${SLOTY_ETC_DIR:-/etc/sloty}"
  local metadata_path="${SLOTY_RELEASE_FILE:-${metadata_dir}/backend-release.json}"
  local python_bin="${SLOTY_BACKEND_PYTHON:-}"
  local tmp_file=""
  local deployed_at

  if [[ -z "$python_bin" || ! -x "$python_bin" ]]; then
    echo "Sloty virtualenv interpreter is required to write release metadata." >&2
    echo "Refusing to use system python/python3." >&2
    return 1
  fi

  mkdir -p "$(dirname "$metadata_path")"
  tmp_file="$(mktemp "${metadata_path}.tmp.XXXXXX")"

  deployed_at="$(date -u +"%Y-%m-%dT%H:%M:%SZ")"

  "$python_bin" - "$tmp_file" "$release_tag" "$commit_sha" "$deployed_at" <<'PY'
import json
import sys

path, release, commit, deployed_at = sys.argv[1:5]
payload = {
    "service": "sloty-backend",
    "release": release,
    "commit": commit.lower(),
    "deployed_at": deployed_at,
}
with open(path, "w", encoding="utf-8") as handle:
    json.dump(payload, handle, indent=4)
    handle.write("\n")
PY

  chmod 0640 "$tmp_file"
  if [[ "$(id -u)" -eq 0 ]] && getent group sloty >/dev/null 2>&1; then
    chown root:sloty "$tmp_file"
  fi

  if [[ -n "${SLOTY_DEPLOY_FAIL_BEFORE_METADATA_RENAME:-}" ]]; then
    rm -f "$tmp_file"
    echo "Refusing to rename release metadata (test interrupt)." >&2
    return 1
  fi

  mv -f "$tmp_file" "$metadata_path"
}
