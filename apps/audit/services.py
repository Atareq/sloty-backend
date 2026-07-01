from apps.audit.models import AuditLog


def record_audit_log(
    *,
    club,
    actor,
    action,
    entity_type,
    entity_id,
    court=None,
    before_data=None,
    after_data=None,
    metadata=None,
):
    return AuditLog.objects.create(
        club=club,
        court=court,
        actor=actor,
        action=action,
        entity_type=entity_type,
        entity_id=entity_id,
        before_data=before_data or {},
        after_data=after_data or {},
        metadata=metadata or {},
    )
