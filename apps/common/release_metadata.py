import json
import logging
import re
from pathlib import Path

from django.conf import settings

logger = logging.getLogger(__name__)

BACKEND_SERVICE_NAME = "sloty-backend"
RELEASE_PATTERN = re.compile(r"^v\d+\.\d+\.\d+$")
COMMIT_PATTERN = re.compile(r"^[0-9a-f]{40}$")
DEPLOYED_AT_PATTERN = re.compile(r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}Z$")

EMPTY_VERSION_PAYLOAD = {
    "service": BACKEND_SERVICE_NAME,
    "release": None,
    "commit": None,
}


def _metadata_path() -> Path:
    configured = getattr(
        settings,
        "BACKEND_RELEASE_METADATA_PATH",
        "/etc/sloty/backend-release.json",
    )
    return Path(configured)


def _empty_payload():
    return dict(EMPTY_VERSION_PAYLOAD)


def get_backend_release_payload():
    """Return the deployed backend identity from the local metadata file.

    Missing, unreadable, or invalid metadata yields null release/commit. This
    function never queries Git, GitHub, or the database.
    """
    path = _metadata_path()
    try:
        raw = path.read_text(encoding="utf-8")
        data = json.loads(raw)
    except FileNotFoundError:
        return _empty_payload()
    except (OSError, UnicodeError, json.JSONDecodeError, TypeError, ValueError):
        logger.warning("Backend release metadata is missing or invalid.")
        return _empty_payload()

    if not isinstance(data, dict):
        logger.warning("Backend release metadata is missing or invalid.")
        return _empty_payload()

    release = data.get("release")
    commit = data.get("commit")
    if not isinstance(release, str) or RELEASE_PATTERN.fullmatch(release) is None:
        logger.warning("Backend release metadata is missing or invalid.")
        return _empty_payload()
    if not isinstance(commit, str) or COMMIT_PATTERN.fullmatch(commit.lower()) is None:
        logger.warning("Backend release metadata is missing or invalid.")
        return _empty_payload()

    payload = {
        "service": BACKEND_SERVICE_NAME,
        "release": release,
        "commit": commit.lower(),
    }
    deployed_at = data.get("deployed_at")
    if isinstance(deployed_at, str) and DEPLOYED_AT_PATTERN.fullmatch(deployed_at):
        payload["deployed_at"] = deployed_at
    return payload
