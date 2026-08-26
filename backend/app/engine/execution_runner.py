"""Background workflow runners used by :class:`ExecutionEngine`."""

from __future__ import annotations

import asyncio
import logging
from collections.abc import Awaitable
from decimal import Decimal, InvalidOperation
from typing import Any

from app.core.logging import bind_log_context, execution_id_var
from app.core.model_budget import ModelCallBudget, ModelCallUsage
from app.db.models import CostAlert
from app.db.repositories import ExecutionRepo
from app.engine.dsl_traversal import iter_workflow_nodes
from app.engine.events import EventEmitter
from app.engine.execution_lease import WorkerLease
from app.engine.nodes import CompileContext, LoopLimitExceeded
from app.engine.subworkflow_resolver import SubworkflowCache
from app.schemas.dsl import NodeType, WorkflowDSL
from app.schemas.events import EventType

try:
    from langgraph.errors import GraphInterrupt
except ImportError:  # pragma: no cover - older langgraph shim

    class GraphInterrupt(BaseException):  # type: ignore[no-redef]
        """Fallback GraphInterrupt when langgraph.errors is unavailable."""


logger = logging.getLogger(__name__)


def _interrupt_request(source: Any) -> dict[str, Any] | None:
    """Extract the human request from LangGraph snapshot/interrupt containers."""
    seen: set[int] = set()

    def visit(value: Any) -> dict[str, Any] | None:
        marker = id(value)
        if marker in seen:
            return None
        seen.add(marker)
        if isinstance(value, dict):
            if "title" in value or "instruction" in value or "form_schema" in value:
                return dict(value)
            for nested in value.values():
                found = visit(nested)
                if found is not None:
                    return found
            return None
        if isinstance(value, (list, tuple)):
            for nested in value:
                found = visit(nested)
                if found is not None:
                    return found
            return None
        interrupt_value = getattr(value, "value", None)
        if interrupt_value is not None:
            found = visit(interrupt_value)
            if found is not None:
                return found
        for attribute in ("interrupts", "args"):
            nested = getattr(value, attribute, None)
            if nested:
                found = visit(nested)
                if found is not None:
                    return found
        return None

    return visit(source)


def _requires_durable_checkpoint(
    dsl: WorkflowDSL,
    debug: dict[str, Any] | None,
    subworkflows: SubworkflowCache | None = None,
) -> bool:
    """Use the shared saver only when this run can suspend and later resume."""
    workflows = (dsl, *(subworkflows.resolved_workflows() if subworkflows else ()))
    if any(
        node.type == NodeType.HUMAN
        for workflow in workflows
        for node in iter_workflow_nodes(workflow)
    ):
        return True
    if not isinstance(debug, dict):
        return False
    breakpoints = debug.get("breakpoints")
    return bool(debug.get("single_step")) or bool(
        isinstance(breakpoints, list) and breakpoints
    )


class ExecutionRunnerMixin:
    """Run and resume compiled graphs while the engine owns scheduling."""

    async def _observe_task(
        self: Any,
        execution_id: str,
        workflow_id: str,
        operation: str,
        awaitable: Awaitable[None],
    ) -> None:
        # Let the HTTP task flush its 201 response before graph compilation and
        # checkpoint work begin on the same event loop under burst traffic.
        await asyncio.sleep(0)
        with bind_log_context(execution_id=execution_id, workflow_id=workflow_id):
            if self.observability is None:
                await awaitable
                return
            with self.observability.span(
                operation,
                attributes={
                    "agentcanvas.execution.id": execution_id,
                    "agentcanvas.workflow.id": workflow_id,
                },
            ):
                await awaitable

    async def _persist_budget_alert(
        self: Any,
        execution_id: str,
        workflow_id: str,
        budget: ModelCallBudget | None,
    ) -> bool:
        """Persist a critical cost alert when a configured ceiling stopped a run."""
        if budget is None or budget.breach is None:
            return False
        breach = budget.breach
        async with self.session_factory() as session:
            session.add(
                CostAlert(
                    execution_id=execution_id,
                    workflow_id=workflow_id,
                    kind=breach.kind,
                    severity="critical",
                    limit_value=breach.limit_value,
                    actual_value=breach.actual_value,
                    message=breach.message,
                )
            )
            await session.commit()
        return True

    async def _persist_budget_warnings(
        self: Any,
        execution_id: str,
        workflow_id: str,
        budget: ModelCallBudget | None,
    ) -> int:
        """Persist soft warnings when a successful run crosses 80% of a ceiling."""
        if budget is None:
            return 0
        config = budget.gate.config
        usage = budget.snapshot()
        warnings: list[tuple[str, str, str, str]] = []
        if config.max_tokens_per_execution > 0 and (
            usage.total_tokens * 5 >= config.max_tokens_per_execution * 4
        ):
            warnings.append(
                (
                    "tokens",
                    str(config.max_tokens_per_execution),
                    str(usage.total_tokens),
                    (
                        f"token usage reached {usage.total_tokens} of "
                        f"{config.max_tokens_per_execution} tokens"
                    ),
                )
            )
        if (
            config.max_cost_usd_per_execution is not None
            and usage.cost_known
            and usage.estimated_cost_usd is not None
        ):
            try:
                limit = Decimal(config.max_cost_usd_per_execution)
                actual = Decimal(usage.estimated_cost_usd)
            except InvalidOperation:
                limit = None
                actual = None
            if limit is not None and actual is not None and actual >= limit * Decimal("0.8"):
                warnings.append(
                    (
                        "cost",
                        format(limit, "f"),
                        format(actual, "f"),
                        f"cost reached {format(actual, 'f')} of {format(limit, 'f')} USD",
                    )
                )
        if not warnings:
            return 0
        async with self.session_factory() as session:
            for kind, limit_value, actual_value, message in warnings:
                session.add(
                    CostAlert(
                        execution_id=execution_id,
                        workflow_id=workflow_id,
                        kind=kind,
                        severity="warning",
                        limit_value=limit_value,
                        actual_value=actual_value,
                        message=message,
                    )
                )
            await session.commit()
        return len(warnings)

    async def _persist_budget_alert_safely(
        self: Any,
        execution_id: str,
        workflow_id: str,
        budget: ModelCallBudget | None,
    ) -> None:
        """Persist a critical alert without ever changing the run's outcome."""
        try:
            await self._persist_budget_alert(execution_id, workflow_id, budget)
        except Exception:
            logger.exception("failed to persist budget alert for execution %s", execution_id)

    async def _persist_budget_warnings_safely(
        self: Any,
        execution_id: str,
        workflow_id: str,
        budget: ModelCallBudget | None,
    ) -> None:
        """Persist soft warnings without ever changing the run's outcome."""
        try:
            await self._persist_budget_warnings(execution_id, workflow_id, budget)
        except Exception:
            logger.exception("failed to persist budget warnings for execution %s", execution_id)

    @staticmethod
    def _terminal_cost(budget: ModelCallBudget | None) -> dict[str, Any] | None:
        """Include the final token/cost snapshot in terminal event payloads."""
        return budget.snapshot().to_dict() if budget is not None else None

    async def _interrupted_model_usage(
        self: Any,
        execution_id: str,
    ) -> ModelCallUsage | None:
        """Load the latest cumulative model usage persisted at a pause."""
        async with self.session_factory() as session:
            row = await ExecutionRepo(session).latest_event(
                execution_id,
                EventType.WORKFLOW_INTERRUPTED.value,
            )
        if row is None or not isinstance(row.payload_json, dict):
            return None
        return ModelCallUsage.from_dict(row.payload_json.get("cost"))

    @staticmethod
    def _debug_context_fields(debug: dict[str, Any] | None) -> dict[str, Any]:
        """Translate a queue debug payload into CompileContext fields.

        Returns ``debug_breakpoints`` (frozenset) and ``debug_single_step``
        so the compiler injects breakpoint nodes on eligible linear edges.
        """
        if not isinstance(debug, dict):
            return {"debug_breakpoints": frozenset(), "debug_single_step": False}
        breakpoints = debug.get("breakpoints")
        if not isinstance(breakpoints, list):
            breakpoints = []
        return {
            "debug_breakpoints": frozenset(str(item) for item in breakpoints),
            "debug_single_step": bool(debug.get("single_step")),
        }

    async def _run(
        self: Any,
        execution_id: str,
        workflow_id: str,
        dsl: WorkflowDSL,
        inputs: dict[str, Any],
        session_id: str | None = None,
        *,
        project_id: str | None = None,
        slot_acquired: bool = False,
        start_node_id: str | None = None,
        initial_node_outputs: dict[str, Any] | None = None,
        rerun_from_execution_id: str | None = None,
        reused_node_ids: tuple[str, ...] = (),
        lease: WorkerLease | None = None,
        debug: dict[str, Any] | None = None,
    ) -> None:
        token = execution_id_var.set(execution_id)
        emitter = EventEmitter(self.event_bus, execution_id)
        providers: dict[str, Any] = {}
        keep_model_budget = False
        budget: ModelCallBudget | None = None
        execution_mcp: Any | None = None
        try:
            providers = await self.load_workflow_providers(dsl)

            def get_provider(model_config_id: str) -> Any:
                try:
                    return providers[model_config_id]
                except KeyError as exc:
                    raise RuntimeError(f"Provider '{model_config_id}' was not preloaded") from exc

            budget = self._model_budget_for(execution_id, project_id, lease)
            # Live MCP sessions belong to the leased attempt, not the API process.
            # Resume/retry reconnect from durable server config rather than moving sessions.
            if self.mcp_manager is not None:
                execution_mcp = self.mcp_manager.for_execution(execution_id)
            subworkflow_cache = await self._resolve_subworkflow_cache(dsl)
            ctx = CompileContext(
                execution_id=execution_id,
                emitter=emitter,
                get_provider=get_provider,
                max_loop_iterations=dsl.settings.max_loop_iterations,
                project_id=project_id,
                mcp_manager=execution_mcp,
                rag_service=self.rag_service,
                memory_store=self.memory_store,
                settings=self.settings,
                observability=self.observability,
                model_budget=budget,
                resilience=self.resilience,
                resilience_config=self.resilience_config,
                secret_resolver=self.secret_resolver,
                sandbox=self.sandbox,
                subworkflow_loader=subworkflow_cache.lookup if subworkflow_cache else None,
                session_variable_store=self.session_variable_store,
                **self._debug_context_fields(debug),
            )
            graph = self.compiler.compile(
                dsl,
                ctx,
                checkpointer=(
                    await self._checkpointer(lease)
                    if _requires_durable_checkpoint(dsl, debug, subworkflow_cache)
                    else None
                ),
                start_node_id=start_node_id,
            )
            init_state: dict[str, Any] = {
                "inputs": inputs,
                "node_outputs": dict(initial_node_outputs or {}),
                "loop_counts": {},
                "messages": [],
                "route": None,
                "error": None,
                "final_output": None,
            }
            if session_id:
                init_state["inputs"] = {**inputs, "_session_id": session_id}
                # C3-4: expose the session's durable variables to templates as
                # {{session.<name>}} for this run (best-effort; a store failure
                # must not fail the execution).
                if self.session_variable_store is not None:
                    try:
                        snapshot = await self.session_variable_store.snapshot(session_id)
                    except Exception:  # noqa: BLE001
                        snapshot = None
                    if snapshot:
                        init_state["inputs"]["_session_vars"] = snapshot
            config = {
                "configurable": {"thread_id": execution_id},
                "recursion_limit": dsl.settings.recursion_limit,
            }

            final_state: dict[str, Any] = dict(init_state)
            interrupted = False
            interrupt_request: dict[str, Any] | None = None
            try:
                async with asyncio.timeout(dsl.settings.timeout_seconds):
                    async for update in graph.astream(
                        init_state, config=config, stream_mode="updates"
                    ):
                        if isinstance(update, dict):
                            if "__interrupt__" in update:
                                interrupted = True
                                interrupt_request = _interrupt_request(update["__interrupt__"])
                                break
                            for _nid, delta in update.items():
                                if isinstance(delta, dict):
                                    if "node_outputs" in delta and isinstance(
                                        delta["node_outputs"], dict
                                    ):
                                        final_state.setdefault("node_outputs", {}).update(
                                            delta["node_outputs"]
                                        )
                                    if "final_output" in delta:
                                        final_state["final_output"] = delta["final_output"]
                                    if "error" in delta and delta["error"]:
                                        final_state["error"] = delta["error"]
            except GraphInterrupt as exc:
                interrupted = True
                interrupt_request = _interrupt_request(exc)

            try:
                snapshot = graph.get_state(config)
            except Exception:  # noqa: BLE001 - best-effort snapshot inspection
                snapshot = None
            if snapshot is not None:
                if not interrupted and getattr(snapshot, "next", None):
                    interrupted = True
                if interrupt_request is None:
                    interrupt_request = _interrupt_request(snapshot)

            if interrupted:
                keep_model_budget = True
                payload: dict[str, Any] = {
                    "reason": "human_approval",
                    "thread_id": session_id or execution_id,
                }
                interrupted_cost = self._terminal_cost(budget)
                if interrupted_cost is not None:
                    payload["cost"] = interrupted_cost
                if interrupt_request is not None:
                    payload["request"] = interrupt_request
                    payload["node_id"] = interrupt_request.get("node_id")
                await self._set_status(
                    execution_id,
                    "waiting_approval",
                    lease=lease,
                    event_type=EventType.WORKFLOW_INTERRUPTED,
                    event_payload=payload,
                )
                return

            output = final_state.get("final_output") or self._fallback_output(
                final_state.get("node_outputs") or {}
            )
            finished_payload: dict[str, Any] = {"output": output}
            terminal_cost = self._terminal_cost(budget)
            if terminal_cost is not None:
                finished_payload["cost"] = terminal_cost
            await self._set_status(
                execution_id,
                "succeeded",
                output=output,
                lease=lease,
                event_type=EventType.WORKFLOW_FINISHED,
                event_payload=finished_payload,
            )
            await self._persist_budget_warnings_safely(execution_id, workflow_id, budget)
        except asyncio.CancelledError:
            cancelled_payload: dict[str, Any] = {}
            cancelled_cost = self._terminal_cost(budget)
            if cancelled_cost is not None:
                cancelled_payload["cost"] = cancelled_cost
            await self._set_status(
                execution_id,
                "cancelled",
                lease=lease,
                event_type=EventType.WORKFLOW_CANCELLED,
                event_payload=cancelled_payload,
            )
            raise
        except TimeoutError:
            message = f"workflow timed out after {dsl.settings.timeout_seconds}s"
            timeout_payload: dict[str, Any] = {"error": message}
            timeout_cost = self._terminal_cost(budget)
            if timeout_cost is not None:
                timeout_payload["cost"] = timeout_cost
            await self._set_status(
                execution_id,
                "failed",
                error=message,
                lease=lease,
                event_type=EventType.WORKFLOW_FAILED,
                event_payload=timeout_payload,
            )
        except LoopLimitExceeded as exc:
            loop_payload: dict[str, Any] = {"error": str(exc)}
            loop_cost = self._terminal_cost(budget)
            if loop_cost is not None:
                loop_payload["cost"] = loop_cost
            await self._set_status(
                execution_id,
                "failed",
                error=str(exc),
                lease=lease,
                event_type=EventType.WORKFLOW_FAILED,
                event_payload=loop_payload,
            )
        except Exception as exc:
            logger.exception("execution %s failed", execution_id)
            await self._persist_budget_alert_safely(execution_id, workflow_id, budget)
            error_payload: dict[str, Any] = {"error": str(exc)}
            error_cost = self._terminal_cost(budget)
            if error_cost is not None:
                error_payload["cost"] = error_cost
            await self._set_status(
                execution_id,
                "failed",
                error=str(exc),
                lease=lease,
                event_type=EventType.WORKFLOW_FAILED,
                event_payload=error_payload,
            )
        finally:
            await self.close_workflow_providers(providers)
            if execution_mcp is not None:
                try:
                    await execution_mcp.shutdown()
                except Exception:  # noqa: BLE001 - release best-effort on lease end
                    logger.exception(
                        "failed to release worker-owned MCP sessions for execution %s",
                        execution_id,
                    )
            if self.model_gate is not None and not keep_model_budget:
                self.model_gate.discard_budget(execution_id)
            self._release_execution_slot(slot_acquired)
            await self.event_bus.close_execution(execution_id)
            execution_id_var.reset(token)
