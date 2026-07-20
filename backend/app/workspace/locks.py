from __future__ import annotations

from threading import RLock

from app.workspace.models import ResourceName

_RESOURCE_LOCKS = {resource: RLock() for resource in ResourceName}


def resource_lock(name: ResourceName) -> RLock:
    try:
        resource = ResourceName(name)
    except (TypeError, ValueError) as exc:
        raise ValueError(f"unknown workspace resource: {name!r}") from exc
    return _RESOURCE_LOCKS[resource]
