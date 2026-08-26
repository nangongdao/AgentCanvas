"""Background runner for human-approved execution resumes."""

from __future__ import annotations

import asyncio
import logging
from typing import Any

from app.core.logging import execution_id_var
from app.core.model_budget import ModelCallBudget
from app.engine.events import EventEmitter
from app.engine.execution_lease import WorkerLease
from app.engine.execution_runner import ExecutionRunnerMixin, _interrupt_request
from app.engine.nodes import CompileContext, LoopLimitExceeded
from app.schemas.dsl import WorkflowDSL
from app.schemas.events import EventType

logger = logging.getLogger(__name__)


class ExecutionResumeRunnerMixin:
    async def _run_resume(
        self: Any,
        execution_id: str,
        workflow_id: str,
        dsl: WorkflowDSL,
        inputs: dict[str, Any],
        decision: dict[str, Any],
        *,
        project_id: str | None = None,
        slot_acquired: bool = False,
        lease: WorkerLease | None = None,
        debug: dict[str, Any] | None = None,
    ) -> None:
        """Continue a previously interrupted graph with the human decision."""
        from langgraph.types import Command

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

            interrupted_usage = await self._interrupted_model_usage(execution_id)
            budget = self._model_budget_for(
                execution_id,
                project_id,
                lease,
                initial_usage=interrupted_usage,
            )
            # Resume reconnects from durable MCP server config under a fresh
            # worker-owned manager; live sessions are not transferred across leases.
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
                **ExecutionRunnerMixin._debug_context_fields(debug),
            )
            graph = self.compiler.compile(dsl, ctx, checkpointer=await self._checkpointer(lease))
            config = {
                "configurable": {"thread_id": execution_id},
                "recursion_limit": dsl.settings.recursion_limit,
            }
            final_state: dict[str, Any] = {"node_outputs": {}}
            interrupted = False
            interrupt_request: dict[str, Any] | None = None
            async with asyncio.timeout(dsl.settings.timeout_seconds):
                async for update in graph.astream(
                    Command(resume=decision), config=config, stream_mode="updates"
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

            if interrupted:
                keep_model_budget = True
                payload: dict[str, Any] = {
                    "reason": "human_approval",
                    "thread_id": execution_id,
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
            resume_payload: dict[str, Any] = {"output": output, "resumed": decision}
            resume_cost = self._terminal_cost(budget)
            if resume_cost is not None:
                resume_payload["cost"] = resume_cost
            await self._set_status(
                execution_id,
                "succeeded",
                output=output,
                lease=lease,
                event_type=EventType.WORKFLOW_FINISHED,
                event_payload=resume_payload,
            )
            await self._persist_budget_warnings_safely(execution_id, workflow_id, budget)
        except asyncio.CancelledError:
            resume_cancelled_payload: dict[str, Any] = {}
            resume_cancelled_cost = self._terminal_cost(budget)
            if resume_cancelled_cost is not None:
                resume_cancelled_payload["cost"] = resume_cancelled_cost
            await self._set_status(
                execution_id,
                "cancelled",
                lease=lease,
                event_type=EventType.WORKFLOW_CANCELLED,
                event_payload=resume_cancelled_payload,
            )
            raise
        except TimeoutError:
            message = f"workflow timed out after {dsl.settings.timeout_seconds}s"
            resume_timeout_payload: dict[str, Any] = {"error": message}
            resume_timeout_cost = self._terminal_cost(budget)
            if resume_timeout_cost is not None:
                resume_timeout_payload["cost"] = resume_timeout_cost
            await self._set_status(
                execution_id,
                "failed",
                error=message,
                lease=lease,
                event_type=EventType.WORKFLOW_FAILED,
                event_payload=resume_timeout_payload,
            )
        except LoopLimitExceeded as exc:
            resume_loop_payload: dict[str, Any] = {"error": str(exc)}
            resume_loop_cost = self._terminal_cost(budget)
            if resume_loop_cost is not None:
                resume_loop_payload["cost"] = resume_loop_cost
            await self._set_status(
                execution_id,
                "failed",
                error=str(exc),
                lease=lease,
                event_type=EventType.WORKFLOW_FAILED,
                event_payload=resume_loop_payload,
            )
        except Exception as exc:
            logger.exception("execution %s resume failed", execution_id)
            await self._persist_budget_alert_safely(execution_id, workflow_id, budget)
            resume_error_payload: dict[str, Any] = {"error": str(exc)}
            resume_error_cost = self._terminal_cost(budget)
            if resume_error_cost is not None:
                resume_error_payload["cost"] = resume_error_cost
            await self._set_status(
                execution_id,
                "failed",
                error=str(exc),
                lease=lease,
                event_type=EventType.WORKFLOW_FAILED,
                event_payload=resume_error_payload,
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


__all__ = ["ExecutionResumeRunnerMixin"]
