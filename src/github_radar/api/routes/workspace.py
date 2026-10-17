"""Private state joins existing, deterministic intelligence; never collects remote data."""

from __future__ import annotations

import re
from typing import Annotated, Literal
from urllib.parse import quote, urlsplit
from uuid import uuid4

from fastapi import APIRouter, Depends, Query
from pydantic import BaseModel, ConfigDict, Field, field_validator
from sqlalchemy import delete, select, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from github_radar.api.dataset import developer_dataset_and_reports
from github_radar.api.deps import get_session
from github_radar.api.errors import ConflictError, NotFoundError
from github_radar.api.pagination import page, parse_pagination
from github_radar.api.routes.developers import _relevance_value, _summary
from github_radar.api.serializers import iso
from github_radar.api.window import parse_window_days
from github_radar.domain import PublicContactMethods
from github_radar.storage.models import (
    DeveloperRow,
    RepositoryRow,
    WorkspaceDeveloperRow,
    WorkspaceRepositoryRow,
)
from github_radar.util import utcnow

router = APIRouter(prefix="/workspace", tags=["private workspace"])
Kind = Literal["developers", "repositories"]
SavedFilter = Literal["saved", "unsaved", "all"]
RelationshipStatus = Literal["interested", "contacted", "connected", "collaborating", "paused"]
WorkspaceRow = WorkspaceDeveloperRow | WorkspaceRepositoryRow
Session = Annotated[AsyncSession, Depends(get_session)]


class WorkspaceEdit(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)

    saved: bool
    notes: str = Field(max_length=10000)
    relationship_status: RelationshipStatus
    version: str | None = Field(pattern=r"^[0-9a-f]{32}$")

    @field_validator("notes")
    @classmethod
    def no_null_bytes(cls, value: str) -> str:
        if "\x00" in value:
            raise ValueError("Notes cannot contain null bytes")
        return value


def workspace_state(row: WorkspaceRow | None) -> dict[str, object]:
    return {
        "saved": row.saved if row else False,
        "notes": row.notes if row else "",
        "relationship_status": row.relationship_status if row else "interested",
        "version": row.version if row else None,
        "updated_at": iso(row.updated_at) if row else None,
    }


def matches(row: WorkspaceRow | None, saved: SavedFilter, status: str | None) -> bool:
    is_saved = row is not None and row.saved
    return (saved == "all" or is_saved == (saved == "saved")) and (
        status is None or (row is not None and row.relationship_status == status)
    )


def contact_links(login: str, contacts: PublicContactMethods) -> list[dict[str, str]]:
    links = [{"label": "GitHub profile", "url": "https://github.com/" + quote(login, safe="")}]
    website = contacts.website or ""
    try:
        parsed = urlsplit(website)
        if (
            parsed.scheme in ("http", "https")
            and parsed.hostname
            and not parsed.username
            and not parsed.password
            and not any(ord(char) < 32 for char in website)
        ):
            links.append({"label": "Public website", "url": website})
    except ValueError:
        pass
    twitter = (contacts.twitter or "").removeprefix("@")
    if re.fullmatch(r"[A-Za-z0-9_]{1,15}", twitter):
        links.append({"label": "Public X profile", "url": "https://x.com/" + twitter})
    return links


@router.get("/session")
async def workspace_session() -> dict[str, str]:
    return {"access": "local-admin"}


@router.get("/developers")
async def workspace_developers(
    session: Session,
    saved: SavedFilter = "saved",
    status: RelationshipStatus | None = None,
    topic: Annotated[str | None, Query(max_length=100)] = None,
    min_relevance: Annotated[float | None, Query(ge=0, le=1)] = None,
    window: Annotated[str, Query(pattern=r"^(1|7|30)d?$")] = "7d",
    limit: int = 25,
    offset: int = 0,
) -> dict[str, object]:
    limit, offset = parse_pagination(limit, offset)
    topic = topic.strip().lower() if topic and topic.strip() else None
    _dataset, reports = await developer_dataset_and_reports(
        session,
        window_days=parse_window_days(window),
    )
    states = {r.entity_id: r for r in (await session.scalars(select(WorkspaceDeveloperRow)))}
    kept = []
    for report in reports:
        relevance = _relevance_value(report, topic)
        if not matches(states.get(report.developer_id), saved, status):
            continue
        if topic and report.relevance_for(topic) is None:
            continue
        if min_relevance is not None and (relevance is None or relevance < min_relevance):
            continue
        kept.append(report)
    kept.sort(
        key=lambda r: (
            -value if (value := _relevance_value(r, topic)) is not None else 1,
            r.github_id,
        )
    )
    return page(
        [
            {
                "id": report.developer_id,
                **_summary(report, topic=topic),
                "topics": sorted(r.topic for r in report.topic_relevances),
                "contacts": contact_links(report.login, report.public_contacts),
                "public_email": report.public_contacts.public_email,
                "workspace": workspace_state(states.get(report.developer_id)),
            }
            for report in kept[offset : offset + limit]
        ],
        limit=limit,
        offset=offset,
        total=len(kept),
    )


@router.get("/repositories")
async def workspace_repositories(
    session: Session,
    saved: SavedFilter = "saved",
    status: RelationshipStatus | None = None,
    topic: Annotated[str | None, Query(max_length=100)] = None,
    limit: int = 25,
    offset: int = 0,
) -> dict[str, object]:
    limit, offset = parse_pagination(limit, offset)
    topic = topic.strip().lower() if topic and topic.strip() else None
    states = {r.entity_id: r for r in (await session.scalars(select(WorkspaceRepositoryRow)))}
    repos = (await session.scalars(select(RepositoryRow).order_by(RepositoryRow.github_id))).all()
    kept = [
        r
        for r in repos
        if matches(states.get(r.id), saved, status)
        and (not topic or topic in {t.name for t in r.topics})
    ]
    return page(
        [
            {
                "id": r.id,
                "full_name": r.full_name,
                "description": r.description,
                "primary_language": r.primary_language,
                "topics": sorted(t.name for t in r.topics),
                "contacts": [
                    {
                        "label": "GitHub repository",
                        "url": "https://github.com/"
                        + quote(r.owner_login, safe="")
                        + "/"
                        + quote(r.name, safe=""),
                    }
                ],
                "workspace": workspace_state(states.get(r.id)),
            }
            for r in kept[offset : offset + limit]
        ],
        limit=limit,
        offset=offset,
        total=len(kept),
    )


@router.put("/{kind}/{entity_id}")
async def edit_workspace(
    kind: Kind,
    entity_id: int,
    edit: WorkspaceEdit,
    session: Session,
) -> dict[str, object]:
    table: type[WorkspaceDeveloperRow] | type[WorkspaceRepositoryRow]
    table = WorkspaceDeveloperRow if kind == "developers" else WorkspaceRepositoryRow
    entity = DeveloperRow if kind == "developers" else RepositoryRow
    if (
        entity_id <= 0
        or entity_id > 9223372036854775807
        or not await session.get(entity, entity_id)
    ):
        raise NotFoundError("Tracked entity not found.")
    values = edit.model_dump(exclude={"version"})
    values.update(version=uuid4().hex, updated_at=utcnow())
    try:
        if edit.version is None:
            session.add(table(entity_id=entity_id, **values))
            await session.flush()
        else:
            changed = await session.scalar(
                update(table)
                .where(table.entity_id == entity_id, table.version == edit.version)
                .values(**values)
                .returning(table.entity_id)
            )
            if changed is None:
                raise ConflictError("This entry changed. Reload before saving your edits.")
        row = await session.get(table, entity_id, populate_existing=True)
        assert isinstance(row, (WorkspaceDeveloperRow, WorkspaceRepositoryRow))
        result = workspace_state(row)
        await session.commit()
        return result
    except IntegrityError as exc:
        await session.rollback()
        raise ConflictError("This entry changed. Reload before saving your edits.") from exc


@router.delete("/{kind}/{entity_id}")
async def delete_workspace(
    kind: Kind,
    entity_id: int,
    session: Session,
    version: Annotated[str, Query(pattern=r"^[0-9a-f]{32}$")],
) -> dict[str, bool]:
    table: type[WorkspaceDeveloperRow] | type[WorkspaceRepositoryRow]
    table = WorkspaceDeveloperRow if kind == "developers" else WorkspaceRepositoryRow
    if entity_id <= 0 or entity_id > 9223372036854775807:
        raise NotFoundError("Workspace entry not found.")
    changed = await session.scalar(
        delete(table)
        .where(table.entity_id == entity_id, table.version == version)
        .returning(table.entity_id)
    )
    if changed is None:
        raise ConflictError("This entry changed. Reload before deleting it.")
    await session.commit()
    return {"deleted": True}
