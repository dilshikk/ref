from __future__ import annotations

from typing import Any

from loader import db


class AuditLogRepository:
    async def create(
        self,
        *,
        actor_id: int | None,
        actor_type: str,
        action: str,
        entity_type: str | None = None,
        entity_id: str | int | None = None,
        old_value: Any = None,
        new_value: Any = None,
        payload: Any = None,
        ip: str | None = None,
        metadata: Any = None,
    ) -> None:
        await db.insert_audit_log(
            actor_id=actor_id,
            actor_type=actor_type,
            action=action,
            entity_type=entity_type,
            entity_id=entity_id,
            old_value=old_value,
            new_value=new_value,
            payload=payload,
            ip=ip,
            metadata=metadata,
        )


audit_log_repository = AuditLogRepository()
