"""Durable outbound workflow callback scanner and dispatcher."""

from __future__ import annotations

import asyncio
import hashlib
import hmac
import json
import logging
import uuid
from contextlib import suppress
from datetime import UTC, datetime, timedelta
from typing import Any

import httpx
from sqlalchemy import and_, or_, select, true
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.core.outbound_http import build_pinned_client, resolve_public_destination
from app.core.secret_providers import SecretResolver
from app.db.models import CostAlert, Execution, ExecutionEventRow
from app.db.repositories import (
    WorkflowCallbackCursorRepo,
    WorkflowCallbackDeliveryRepo,
    WorkflowCallbackRepo,
)

logger = logging.getLogger(__name__)

TERMINAL_EVENT_TYPES = {"workflow_finished", "workflow_failed", "workflow_cancelled"}
DEAD_LETTER_REASONS = {
    "ambiguous_worker_loss",
    "worker_dispatch_failure",
    "dead_letter",
}
CALLBACK_LEASE_GRACE_SECONDS = 5


def _utcnow() -> datetime:
    return datetime.now(UTC)


def _aware(value: datetime) -> datetime:
    return value.replace(tzinfo=UTC) if value.tzinfo is None else value


def _json_bytes(payload: dict[str, Any]) -> bytes:
    return json.dumps(payload, ensure_ascii=False, separators=(",", ":"), sort_keys=True).encode(
        "utf-8"
    )


def _event_name(event_type: str, payload: dict[str, Any]) -> str | None:
    if event_type not in TERMINAL_EVENT_TYPES:
        return None
    if event_type == "workflow_failed" and (
        payload.get("dead_letter") is True or payload.get("reason") in DEAD_LETTER_REASONS
    ):
        return "dead_letter"
    return event_type


def _callback_payload(
    *,
    event_type: str,
    source_type: str,
    source_id: str,
    workflow_id: str,
    project_id: str | None,
    occurred_at: datetime,
    execution: Execution | None = None,
    event_payload: dict[str, Any] | None = None,
    alert: CostAlert | None = None,
) -> dict[str, Any]:
    payload: dict[str, Any] = {
        "schema_version": "1.0",
        "event_type": event_type,
        "source_type": source_type,
        "source_id": source_id,
        "workflow_id": workflow_id,
        "project_id": project_id,
        "occurred_at": _aware(occurred_at).isoformat(),
    }
    if execution is not None:
        payload.update(
            {
                "execution_id": execution.id,
                "workflow_version_id": execution.workflow_version_id,
                "status": execution.status,
                "output": execution.output_json,
                "error": execution.error,
            }
        )
        payload["event"] = dict(event_payload or {})
    if alert is not None:
        payload["alert"] = {
            "id": alert.id,
            "execution_id": alert.execution_id,
            "kind": alert.kind,
            "severity": alert.severity,
            "status": alert.status,
            "limit": alert.limit_value,
            "actual": alert.actual_value,
            "message": alert.message,
        }
    return payload


class WorkflowCallbackDispatcher:
    """Scan durable source rows and deliver claimed outbox records."""

    def __init__(
        self,
        session_factory: async_sessionmaker[AsyncSession],
        secret_resolver: SecretResolver,
        *,
        poll_seconds: float = 1.0,
        batch_size: int = 100,
        lease_seconds: int = 30,
        default_timeout_seconds: int = 10,
        default_max_attempts: int = 5,
        default_retry_delay_seconds: int = 10,
        owner_id: str | None = None,
        transport: httpx.AsyncBaseTransport | None = None,
        shutdown_timeout_seconds: float = 5.0,
    ) -> None:
        self.session_factory = session_factory
        self.secret_resolver = secret_resolver
        self.poll_seconds = max(0.1, poll_seconds)
        self.batch_size = max(1, batch_size)
        self.lease_seconds = max(1, lease_seconds)
        self.default_timeout_seconds = max(1, default_timeout_seconds)
        self.default_max_attempts = max(1, default_max_attempts)
        self.default_retry_delay_seconds = max(1, default_retry_delay_seconds)
        self.owner_id = owner_id or f"callback-{uuid.uuid4().hex[:16]}"
        self._transport = transport
        self.shutdown_timeout_seconds = max(0.01, shutdown_timeout_seconds)
        self._task: asyncio.Task[None] | None = None
        self._stop = asyncio.Event()
        self._wake = asyncio.Event()
        # ``None`` means the dispatcher has not probed configuration yet.
        # Once an empty callback table is observed, execution-event kicks can
        # be coalesced until the normal poll tick instead of issuing one
        # ``has_active`` query per persisted event. The periodic scan still
        # discovers newly-created callbacks within ``poll_seconds``.
        self._has_active_callbacks: bool | None = None

    def start(self) -> None:
        if self._task is None or self._task.done():
            self._stop.clear()
            self._has_active_callbacks = None
            self._task = asyncio.create_task(self._run(), name="workflow-callback-dispatcher")

    async def stop(self) -> None:
        self._stop.set()
        self._wake.set()
        task = self._task
        self._task = None
        if task is not None and not task.done():
            try:
                await asyncio.wait_for(task, timeout=self.shutdown_timeout_seconds)
            except TimeoutError:
                task.cancel()
                with suppress(asyncio.CancelledError):
                    await task

    def kick(self, *, force: bool = False) -> None:
        if force or self._has_active_callbacks is not False:
            self._wake.set()

    async def _run(self) -> None:
        while not self._stop.is_set():
            try:
                await self.dispatch_once()
            except asyncio.CancelledError:
                raise
            except Exception:
                logger.exception("workflow callback dispatch cycle failed")
            self._wake.clear()
            with suppress(TimeoutError):
                await asyncio.wait_for(self._wake.wait(), timeout=self.poll_seconds)

    async def scan_once(self) -> int:
        enqueued = 0
        async with self.session_factory() as session:
            # Avoid cursor writes when no callback is configured. This is a
            # meaningful SQLite hot path during burst execution creation: the
            # scanner has nothing to deliver and should not contend with the
            # EventBus persistence transaction.
            has_active = await WorkflowCallbackRepo(session).has_active()
            self._has_active_callbacks = has_active
            if not has_active:
                await session.rollback()
                return 0
            cursor_repo = WorkflowCallbackCursorRepo(session)
            cursor = await cursor_repo.get_or_create("execution_event", lock=True)
            events = list(
                (
                    await session.execute(
                        select(ExecutionEventRow, Execution)
                        .join(Execution, Execution.id == ExecutionEventRow.execution_id)
                        .where(ExecutionEventRow.id > cursor.last_event_id)
                        .order_by(ExecutionEventRow.id)
                        .limit(self.batch_size)
                    )
                ).all()
            )
            for event_row, execution in events:
                event_type = _event_name(event_row.event_type, dict(event_row.payload_json or {}))
                if event_type is not None:
                    callback = await WorkflowCallbackRepo(session).get_for_workflow(
                        execution.workflow_id
                    )
                    if (
                        callback is not None
                        and callback.status == "active"
                        and _aware(event_row.ts) >= _aware(callback.activated_at)
                    ):
                        configured = {str(value) for value in (callback.event_types_json or [])}
                        if event_type in configured:
                            payload = _callback_payload(
                                event_type=event_type,
                                source_type="execution_event",
                                source_id=str(event_row.id),
                                workflow_id=execution.workflow_id,
                                project_id=callback.project_id,
                                occurred_at=event_row.ts,
                                execution=execution,
                                event_payload=dict(event_row.payload_json or {}),
                            )
                            inserted = await WorkflowCallbackDeliveryRepo(session).enqueue(
                                callback_id=callback.id,
                                workflow_id=execution.workflow_id,
                                project_id=callback.project_id,
                                source_type="execution_event",
                                source_id=str(event_row.id),
                                event_type=event_type,
                                payload=payload,
                            )
                            enqueued += int(inserted is not None)
                await cursor_repo.advance_events("execution_event", int(event_row.id))

            alert_cursor = await cursor_repo.get_or_create("cost_alert", lock=True)
            alert_filter = (
                CostAlert.created_at > alert_cursor.last_created_at
                if alert_cursor.last_created_at is not None
                else true()
            )
            if alert_cursor.last_created_at is not None:
                alert_filter = or_(
                    CostAlert.created_at > alert_cursor.last_created_at,
                    and_(
                        CostAlert.created_at == alert_cursor.last_created_at,
                        CostAlert.id > (alert_cursor.last_source_id or ""),
                    ),
                )
            alerts = list(
                (
                    await session.execute(
                        select(CostAlert)
                        .where(alert_filter)
                        .order_by(CostAlert.created_at, CostAlert.id)
                        .limit(self.batch_size)
                    )
                )
                .scalars()
                .all()
            )
            for alert in alerts:
                if alert.workflow_id:
                    callback = await WorkflowCallbackRepo(session).get_for_workflow(
                        alert.workflow_id
                    )
                    if (
                        callback is not None
                        and callback.status == "active"
                        and _aware(alert.created_at) >= _aware(callback.activated_at)
                    ):
                        event_type = (
                            "quota_alert" if alert.kind.startswith("quota") else "cost_alert"
                        )
                        if event_type in {
                            str(value) for value in (callback.event_types_json or [])
                        }:
                            payload = _callback_payload(
                                event_type=event_type,
                                source_type="cost_alert",
                                source_id=alert.id,
                                workflow_id=alert.workflow_id,
                                project_id=callback.project_id,
                                occurred_at=alert.created_at,
                                alert=alert,
                            )
                            inserted = await WorkflowCallbackDeliveryRepo(session).enqueue(
                                callback_id=callback.id,
                                workflow_id=alert.workflow_id,
                                project_id=callback.project_id,
                                source_type="cost_alert",
                                source_id=alert.id,
                                event_type=event_type,
                                payload=payload,
                            )
                            enqueued += int(inserted is not None)
                await cursor_repo.advance_alerts("cost_alert", alert.created_at, alert.id)
            await session.commit()
        return enqueued

    async def enqueue_quota_alert(
        self,
        *,
        workflow_id: str,
        project_id: str | None,
        metric: str,
        limit: int | float,
        usage: int | float,
        requested: int | float,
    ) -> int:
        """Persist a quota notification independently of the failed request transaction."""
        async with self.session_factory() as session:
            callback = await WorkflowCallbackRepo(session).get_for_workflow(workflow_id)
            if callback is None or callback.status != "active":
                return 0
            if "quota_alert" not in {str(value) for value in (callback.event_types_json or [])}:
                return 0
            resolved_project_id = project_id if project_id is not None else callback.project_id
            source_id = uuid.uuid4().hex
            now = _utcnow()
            payload = _callback_payload(
                event_type="quota_alert",
                source_type="quota_alert",
                source_id=source_id,
                workflow_id=workflow_id,
                project_id=resolved_project_id,
                occurred_at=now,
            )
            payload["quota"] = {
                "metric": metric,
                "limit": limit,
                "usage": usage,
                "requested": requested,
            }
            inserted = await WorkflowCallbackDeliveryRepo(session).enqueue(
                callback_id=callback.id,
                workflow_id=workflow_id,
                project_id=resolved_project_id,
                source_type="quota_alert",
                source_id=source_id,
                event_type="quota_alert",
                payload=payload,
            )
            await session.commit()
            return int(inserted is not None)

    async def enqueue_quota_alert_safely(
        self,
        *,
        workflow_id: str,
        project_id: str | None,
        metric: str,
        limit: int | float,
        usage: int | float,
        requested: int | float,
    ) -> int:
        try:
            enqueued = await self.enqueue_quota_alert(
                workflow_id=workflow_id,
                project_id=project_id,
                metric=metric,
                limit=limit,
                usage=usage,
                requested=requested,
            )
        except Exception:
            logger.exception("failed to enqueue quota callback for workflow %s", workflow_id)
            return 0
        if enqueued:
            self._has_active_callbacks = True
            self.kick(force=True)
        return enqueued

    async def dispatch_once(self) -> int:
        await self.scan_once()
        delivered = 0
        client = (
            httpx.AsyncClient(
                transport=self._transport,
                follow_redirects=False,
                trust_env=False,
            )
            if self._transport is not None
            else None
        )
        try:
            for _index in range(self.batch_size):
                async with self.session_factory() as session:
                    rows = await WorkflowCallbackDeliveryRepo(session).claim_due(
                        owner=self.owner_id,
                        limit=1,
                        lease_seconds=self.lease_seconds,
                    )
                    await session.commit()
                if not rows:
                    break
                delivered += int(await self._deliver_one(client, rows[0].id))
            return delivered
        finally:
            if client is not None:
                await client.aclose()

    async def _deliver_one(self, client: httpx.AsyncClient | None, delivery_id: str) -> bool:
        async with self.session_factory() as session:
            delivery_repo = WorkflowCallbackDeliveryRepo(session)
            delivery = await delivery_repo.get(delivery_id)
            if (
                delivery is None
                or delivery.status != "leased"
                or delivery.lease_owner != self.owner_id
            ):
                return False
            callback = await WorkflowCallbackRepo(session).get(delivery.callback_id)
            if callback is None or callback.status != "active":
                await delivery_repo.mark_failed(
                    delivery,
                    owner=self.owner_id,
                    retry=False,
                    next_attempt_at=_utcnow(),
                    status_code=None,
                    error="callback is disabled or missing",
                )
                await session.commit()
                return False
            timeout_seconds = max(
                1,
                callback.timeout_seconds or self.default_timeout_seconds,
            )
            renewed = await delivery_repo.renew_lease(
                delivery,
                owner=self.owner_id,
                lease_seconds=max(
                    self.lease_seconds,
                    timeout_seconds + CALLBACK_LEASE_GRACE_SECONDS,
                ),
            )
            if not renewed:
                await session.rollback()
                return False
            await session.commit()
            body = _json_bytes(dict(delivery.payload_json or {}))
            timestamp = str(int(_utcnow().timestamp()))
            signature = hmac.new(
                self.secret_resolver.decrypt(callback.secret_encrypted).encode("utf-8"),
                f"{timestamp}.".encode("ascii") + body,
                hashlib.sha256,
            ).hexdigest()
            headers = {
                "Content-Type": "application/json",
                "User-Agent": "AgentCanvas-Callback/1.0",
                "X-AgentCanvas-Event": delivery.event_type,
                "X-AgentCanvas-Delivery-ID": delivery.id,
                "X-AgentCanvas-Timestamp": timestamp,
                "X-AgentCanvas-Signature": f"sha256={signature}",
                "Idempotency-Key": delivery.id,
            }
            try:
                async with asyncio.timeout(timeout_seconds):
                    response = await self._post_callback(
                        client,
                        callback.url,
                        body=body,
                        headers=headers,
                        timeout_seconds=timeout_seconds,
                    )
                status_code = response.status_code
                error = "" if 200 <= status_code < 300 else f"callback returned HTTP {status_code}"
            except (httpx.HTTPError, OSError) as exc:
                status_code = None
                error = f"{type(exc).__name__}: {exc}"
            if status_code is not None and 200 <= status_code < 300:
                marked = await delivery_repo.mark_delivered(
                    delivery,
                    owner=self.owner_id,
                    status_code=status_code,
                )
                if marked:
                    callback.last_delivered_at = _utcnow()
                    await session.commit()
                return marked
            attempt = delivery.attempt_count + 1
            retry = attempt < max(1, callback.max_attempts or self.default_max_attempts)
            delay = min(
                3600,
                max(1, callback.retry_delay_seconds or self.default_retry_delay_seconds)
                * (2 ** max(0, attempt - 1)),
            )
            await delivery_repo.mark_failed(
                delivery,
                owner=self.owner_id,
                retry=retry,
                next_attempt_at=_utcnow() + timedelta(seconds=delay),
                status_code=status_code,
                error=error or "callback delivery failed",
            )
            await session.commit()
            return False

    async def _post_callback(
        self,
        client: httpx.AsyncClient | None,
        url: str,
        *,
        body: bytes,
        headers: dict[str, str],
        timeout_seconds: int,
    ) -> httpx.Response:
        if client is not None:
            return await client.post(
                url,
                content=body,
                headers=headers,
                timeout=timeout_seconds,
            )
        destination = await asyncio.to_thread(resolve_public_destination, url)
        async with build_pinned_client(destination) as pinned_client:
            return await pinned_client.post(
                url,
                content=body,
                headers=headers,
                timeout=timeout_seconds,
            )


__all__ = ["WorkflowCallbackDispatcher"]
