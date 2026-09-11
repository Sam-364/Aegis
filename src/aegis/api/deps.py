"""Dependencies: container access, authentication, authorization."""

from __future__ import annotations

import hmac
from collections.abc import Callable
from typing import Annotated

from fastapi import Depends, Header, Query, Request

from aegis.application.container import RuntimeContainer
from aegis.domain.base import Actor
from aegis.domain.enums import Role
from aegis.domain.errors import ForbiddenError, UnauthorizedError

LOCAL_ACTOR = Actor.human("local-operator", frozenset({Role.ADMIN}), display_name="Local operator")


def get_container(request: Request) -> RuntimeContainer:
    container: RuntimeContainer | None = getattr(request.app.state, "container", None)
    if container is None:
        raise UnauthorizedError("service is starting")
    return container


Container = Annotated[RuntimeContainer, Depends(get_container)]


def current_actor(
    request: Request,
    x_aegis_key: Annotated[str | None, Header()] = None,
    authorization: Annotated[str | None, Header()] = None,
    key: Annotated[str | None, Query()] = None,
) -> Actor:
    settings = get_container(request).settings
    if settings.api_auth_mode == "disabled":
        return LOCAL_ACTOR
    api_key = x_aegis_key
    if api_key is None and authorization and authorization.lower().startswith("bearer "):
        api_key = authorization.split(" ", 1)[1].strip()
    if api_key is None and key and request.url.path.endswith("/stream"):
        api_key = (
            key  # browsers' EventSource cannot set headers; allow the key on stream routes only
        )
    key = api_key
    if not key:
        raise UnauthorizedError("missing API key")
    for principal in settings.api_key_principals:
        if hmac.compare_digest(principal.key, key):
            return Actor.human(
                principal.name, frozenset({principal.role}), display_name=principal.name
            )
    raise UnauthorizedError("invalid API key")


CurrentActor = Annotated[Actor, Depends(current_actor)]


def require_role(role: Role) -> Callable[[Actor], Actor]:
    def dependency(actor: CurrentActor) -> Actor:
        if not actor.has_role(role):
            raise ForbiddenError(f"this action requires the {role.value} role")
        return actor

    return dependency


Operator = Annotated[Actor, Depends(require_role(Role.OPERATOR))]
Admin = Annotated[Actor, Depends(require_role(Role.ADMIN))]
