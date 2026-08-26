"""Bounded, tenant-aware search across command-palette resources."""

from __future__ import annotations

from typing import cast

from sqlalchemy import String, case, func, literal, or_, select, union_all
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.sql.elements import ColumnElement, SQLColumnExpression

from app.core.auth import Principal
from app.db.models import App, Document, KnowledgeBase, Workflow
from app.db.repositories.tenant import project_access_predicate
from app.schemas.search import GlobalSearchResultOut, SearchResultKind


def _escaped_like(value: str) -> str:
    return value.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")


def _matching(
    primary: SQLColumnExpression[str],
    term: str,
    *secondary: SQLColumnExpression[str],
) -> tuple[ColumnElement[bool], ColumnElement[int]]:
    lowered = tuple(func.lower(field) for field in (primary, *secondary))
    lowered_primary = lowered[0]
    escaped = _escaped_like(term)
    contains = or_(*(field.like(f"%{escaped}%", escape="\\") for field in lowered))
    rank = case(
        (lowered_primary == term, 0),
        (lowered_primary.like(f"{escaped}%", escape="\\"), 1),
        else_=2,
    )
    return cast(ColumnElement[bool], contains), cast(ColumnElement[int], rank)


def _null_id() -> ColumnElement[str | None]:
    return cast(ColumnElement[str | None], literal(None, type_=String(32)))


async def search_resources(
    session: AsyncSession,
    principal: Principal,
    query: str,
    *,
    limit: int,
) -> list[GlobalSearchResultOut]:
    """Search all supported resource names in one bounded SQL statement."""
    term = query.lower()

    workflow_match, workflow_rank = _matching(
        Workflow.name, term, func.coalesce(Workflow.description, "")
    )
    workflows = select(
        literal("workflow").label("kind"),
        Workflow.id.label("id"),
        Workflow.name.label("title"),
        func.coalesce(Workflow.description, "").label("subtitle"),
        Workflow.project_id.label("project_id"),
        _null_id().label("parent_id"),
        workflow_rank.label("relevance"),
        literal(0).label("kind_order"),
        Workflow.updated_at.label("updated_at"),
    ).where(
        Workflow.is_archived.is_(False),
        project_access_predicate(Workflow.project_id, principal, include_global=True),
        workflow_match,
    )

    app_match, app_rank = _matching(App.name, term, App.slug)
    apps = select(
        literal("app").label("kind"),
        App.id.label("id"),
        App.name.label("title"),
        (literal("slug: ") + App.slug).label("subtitle"),
        App.project_id.label("project_id"),
        _null_id().label("parent_id"),
        app_rank.label("relevance"),
        literal(1).label("kind_order"),
        App.updated_at.label("updated_at"),
    ).where(
        project_access_predicate(App.project_id, principal, include_global=False),
        app_match,
    )

    kb_match, kb_rank = _matching(
        KnowledgeBase.name, term, func.coalesce(KnowledgeBase.description, "")
    )
    knowledge_bases = select(
        literal("knowledge_base").label("kind"),
        KnowledgeBase.id.label("id"),
        KnowledgeBase.name.label("title"),
        func.coalesce(KnowledgeBase.description, "").label("subtitle"),
        KnowledgeBase.project_id.label("project_id"),
        _null_id().label("parent_id"),
        kb_rank.label("relevance"),
        literal(2).label("kind_order"),
        KnowledgeBase.updated_at.label("updated_at"),
    ).where(
        project_access_predicate(KnowledgeBase.project_id, principal, include_global=True),
        kb_match,
    )

    document_match, document_rank = _matching(Document.filename, term)
    documents = (
        select(
            literal("document").label("kind"),
            Document.id.label("id"),
            Document.filename.label("title"),
            KnowledgeBase.name.label("subtitle"),
            KnowledgeBase.project_id.label("project_id"),
            KnowledgeBase.id.label("parent_id"),
            document_rank.label("relevance"),
            literal(3).label("kind_order"),
            Document.updated_at.label("updated_at"),
        )
        .join(KnowledgeBase, KnowledgeBase.id == Document.kb_id)
        .where(
            project_access_predicate(KnowledgeBase.project_id, principal, include_global=True),
            document_match,
        )
    )

    candidates = union_all(workflows, apps, knowledge_bases, documents).subquery()
    statement = (
        select(candidates)
        .order_by(
            candidates.c.relevance,
            candidates.c.kind_order,
            candidates.c.updated_at.desc(),
            candidates.c.title,
            candidates.c.id,
        )
        .limit(limit)
    )
    rows = (await session.execute(statement)).mappings().all()
    return [
        GlobalSearchResultOut(
            kind=cast(SearchResultKind, row["kind"]),
            id=cast(str, row["id"]),
            title=cast(str, row["title"]),
            subtitle=cast(str, row["subtitle"] or ""),
            project_id=cast(str | None, row["project_id"]),
            parent_id=cast(str | None, row["parent_id"]),
        )
        for row in rows
    ]


__all__ = ["search_resources"]
