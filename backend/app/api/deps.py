"""Shared FastAPI dependencies: DB session, current user, project ownership."""

from __future__ import annotations

from collections.abc import Generator

from fastapi import Depends, HTTPException, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from sqlalchemy.orm import Session

from backend.app.core.security import InvalidTokenError, decode_token
from backend.app.services.usage_recorder import UsageContext, attach_project, bind
from db.auth_models import User
from db.platform_models import Project
from db.session import get_session

_bearer_scheme = HTTPBearer(auto_error=False)


async def bind_usage_context(
    credentials: HTTPAuthorizationCredentials | None = Depends(_bearer_scheme),
) -> None:
    """Attribute every LLM call made while serving this request to its caller.

    Deliberately ``async``: FastAPI runs ``def`` dependencies in a threadpool,
    where a ContextVar set would land on a throwaway copy of the context and
    never reach the endpoint. An ``async`` dependency runs in the request's own
    Task, and anyio then propagates that context *into* the threadpool where the
    sync endpoints run.

    It decodes the token itself instead of depending on
    :func:`get_current_user`, for two reasons: that dependency is sync (see
    above), and it hits the database — a cost this has no need for, since the
    user id is already inside the token. Unauthenticated and bad-token requests
    are a silent no-op so this can be installed app-wide, including on the login
    and health routes, without turning them into authenticated endpoints.
    """
    if credentials is None:
        return
    try:
        user_id = decode_token(credentials.credentials, expected_type="access")
    except InvalidTokenError:
        return
    bind(UsageContext(user_id=user_id))


def get_db() -> Generator[Session, None, None]:
    session = get_session()
    try:
        yield session
    finally:
        session.close()


def get_current_user(
    credentials: HTTPAuthorizationCredentials | None = Depends(_bearer_scheme),
    db: Session = Depends(get_db),
) -> User:
    if credentials is None:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Not authenticated",
            headers={"WWW-Authenticate": "Bearer"},
        )
    try:
        user_id = decode_token(credentials.credentials, expected_type="access")
    except InvalidTokenError as exc:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail=f"Invalid or expired token: {exc}",
            headers={"WWW-Authenticate": "Bearer"},
        ) from exc
    user = db.get(User, user_id)
    if user is None or not user.is_active:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="User not found")
    return user


def get_owned_project(
    project_id: str,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> Project:
    project = db.get(Project, project_id)
    if project is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Project not found")
    if project.owner_id is not None and project.owner_id != current_user.id:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Not your project")
    # Mutates the bound context rather than setting a ContextVar: this
    # dependency is sync, so it runs on a copy of the context where a `set`
    # would be discarded — but the copy holds the same object.
    attach_project(project.id)
    return project
