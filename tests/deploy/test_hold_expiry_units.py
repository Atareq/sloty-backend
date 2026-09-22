from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]


def test_hold_expiry_systemd_units_match_backend_conventions():
    service = (REPO_ROOT / "scripts/systemd/sloty-hold-expiry.service").read_text(
        encoding="utf-8"
    )
    timer = (REPO_ROOT / "scripts/systemd/sloty-hold-expiry.timer").read_text(
        encoding="utf-8"
    )

    assert "Type=oneshot" in service
    assert "User=sloty" in service
    assert "Group=sloty" in service
    assert "WorkingDirectory=/srv/sloty/backend" in service
    assert "EnvironmentFile=/etc/sloty/env/prod.env" in service
    assert "DJANGO_SETTINGS_MODULE=config.settings.production" in service
    assert "expire_hold_bookings" in service
    assert "backup.env" not in service
    assert "OnCalendar=*-*-* *:00/5:00" in timer
    assert "Persistent=true" in timer
    assert "Unit=sloty-hold-expiry.service" in timer
