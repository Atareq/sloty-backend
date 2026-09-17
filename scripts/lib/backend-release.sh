# Shared release-metadata helpers for Sloty backend deployment.
# Sourced by scripts/deploy-sloty-backend and scripts/write-backend-release-metadata.

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

write_backend_release_metadata() {
  local commit_sha="${1:?}"
  local release_tag="${2:?}"
  local metadata_dir="${SLOTY_ETC_DIR:-/etc/sloty}"
  local metadata_path="${SLOTY_RELEASE_FILE:-${metadata_dir}/backend-release.json}"
  local tmp_file=""
  local deployed_at

  mkdir -p "$(dirname "$metadata_path")"
  tmp_file="$(mktemp "${metadata_path}.tmp.XXXXXX")"

  deployed_at="$(date -u +"%Y-%m-%dT%H:%M:%SZ")"

  python3 - "$tmp_file" "$release_tag" "$commit_sha" "$deployed_at" <<'PY'
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
