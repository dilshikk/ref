from __future__ import annotations

from typing import Any

from repositories.audit_log import audit_log_repository


class AuditLogService:
    async def record(
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
        await audit_log_repository.create(
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

    async def record_user_action(self, **kwargs: Any) -> None:
        await self.record(actor_type="user", **kwargs)

    async def record_admin_action(self, **kwargs: Any) -> None:
        await self.record(actor_type="admin", **kwargs)

    async def record_system_action(self, **kwargs: Any) -> None:
        await self.record(actor_type="system", **kwargs)


audit_log_service = AuditLogService()
