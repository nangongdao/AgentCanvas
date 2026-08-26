"""Seed workflows for demos and the P6 acceptance walkthrough.

Seven canonical workflows cover the headline capabilities:
* ``demo-linear``       - start -> agent -> end (P1 minimal loop)
* ``demo-conditions``   - start -> agent -> condition -> (branch A/B) -> end (P2)
* ``demo-supervisor``   - supervisor routing to two worker agents + finish (P2)
* ``demo-iteration``    - bounded concurrent array processing through a child graph (C2)
* ``demo-http``         - outbound HTTP GET with JSON extraction (C2-3)
* ``demo-code``         - sandboxed Python transform of an input array (C2-2)
* ``demo-switch``       - multi-way switch with three named exits (C2-4)
* ``demo-subworkflow``  - parent embedding a child published workflow (C2-5)

Running ``python -m app.services.seeds`` (or ``uv run``) idempotently upserts
all seven into the database. Safe to call on every container start.
"""

from __future__ import annotations

import logging
from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.db.models import Workflow
from app.db.repositories import WorkflowRepo, WorkflowVersionRepo

logger = logging.getLogger(__name__)

SEED_WORKFLOWS: tuple[dict[str, Any], ...] = (
    {
        "id": "demo-linear",
        "name": "Demo · Linear Q&A",
        "description": "start -> agent -> end;最小流式问答闭环。",
        "dsl": {
            "version": "1.0",
            "name": "Demo · Linear Q&A",
            "variables": [{"name": "user_query", "type": "string", "required": True}],
            "settings": {"max_loop_iterations": 20, "timeout_seconds": 120, "recursion_limit": 50},
            "nodes": [
                {
                    "id": "start",
                    "type": "start",
                    "config": {
                        "input_schema": [{"name": "user_query", "type": "string", "required": True}]
                    },
                    "position": {"x": 0, "y": 0},
                },
                {
                    "id": "agent",
                    "type": "agent",
                    "config": {
                        "system_prompt": "你是简洁的助手,用两句话回答。",
                        "user_prompt": "{{input.user_query}}",
                    },
                    "position": {"x": 300, "y": 0},
                },
                {
                    "id": "end",
                    "type": "end",
                    "config": {"output_template": {"answer": "{{nodes.agent.output}}"}},
                    "position": {"x": 600, "y": 0},
                },
            ],
            "edges": [
                {"id": "e1", "source": "start", "target": "agent"},
                {"id": "e2", "source": "agent", "target": "end"},
            ],
        },
    },
    {
        "id": "demo-conditions",
        "name": "Demo · Condition Branch",
        "description": "agent 后按 user_query 是否包含「代码」分流到不同专家节点。",
        "dsl": {
            "version": "1.0",
            "name": "Demo · Condition Branch",
            "variables": [{"name": "user_query", "type": "string", "required": True}],
            "settings": {"max_loop_iterations": 20, "timeout_seconds": 120, "recursion_limit": 50},
            "nodes": [
                {
                    "id": "start",
                    "type": "start",
                    "config": {
                        "input_schema": [{"name": "user_query", "type": "string", "required": True}]
                    },
                    "position": {"x": 0, "y": 0},
                },
                {
                    "id": "triage",
                    "type": "agent",
                    "config": {
                        "system_prompt": "你是分流助手。",
                        "user_prompt": "{{input.user_query}}",
                    },
                    "position": {"x": 280, "y": 0},
                },
                {
                    "id": "branch",
                    "type": "condition",
                    "config": {
                        "branches": [
                            {
                                "id": "code",
                                "label": "代码",
                                "group": {
                                    "op": "and",
                                    "rules": [
                                        {
                                            "left": "{{input.user_query}}",
                                            "operator": "contains",
                                            "right": "代码",
                                        }
                                    ],
                                },
                            },
                        ],
                        "default_branch": "else",
                    },
                    "position": {"x": 560, "y": 0},
                },
                {
                    "id": "coder",
                    "type": "agent",
                    "config": {
                        "system_prompt": "你是代码专家,给出关键思路与一段示例代码。",
                        "user_prompt": "{{input.user_query}}",
                    },
                    "position": {"x": 840, "y": -120},
                },
                {
                    "id": "general",
                    "type": "agent",
                    "config": {
                        "system_prompt": "你是通用助手,简洁回答。",
                        "user_prompt": "{{input.user_query}}",
                    },
                    "position": {"x": 840, "y": 120},
                },
                {"id": "end", "type": "end", "config": {}, "position": {"x": 1120, "y": 0}},
            ],
            "edges": [
                {"id": "e1", "source": "start", "target": "triage"},
                {"id": "e2", "source": "triage", "target": "branch"},
                {"id": "e3", "source": "branch", "target": "coder", "source_handle": "code"},
                {"id": "e4", "source": "branch", "target": "general", "source_handle": "else"},
                {"id": "e5", "source": "coder", "target": "end"},
                {"id": "e6", "source": "general", "target": "end"},
            ],
        },
    },
    {
        "id": "demo-supervisor",
        "name": "Demo · Supervisor Routing",
        "description": "supervisor 根据 user_query 路由到 writer 或 reviewer,完成后 FINISH。",
        "dsl": {
            "version": "1.0",
            "name": "Demo · Supervisor Routing",
            "variables": [{"name": "user_query", "type": "string", "required": True}],
            "settings": {"max_loop_iterations": 20, "timeout_seconds": 180, "recursion_limit": 50},
            "nodes": [
                {
                    "id": "start",
                    "type": "start",
                    "config": {
                        "input_schema": [{"name": "user_query", "type": "string", "required": True}]
                    },
                    "position": {"x": 0, "y": 0},
                },
                {
                    "id": "supervisor",
                    "type": "agent",
                    "config": {
                        "agent_mode": "supervisor",
                        "system_prompt": "你是 supervisor。可用 worker: writer（撰写）、reviewer（审校）。任务完成则回复 FINISH。仅返回 worker id 或 FINISH。",
                        "user_prompt": "{{input.user_query}}",
                        "workers": ["writer", "reviewer"],
                    },
                    "position": {"x": 280, "y": 0},
                },
                {
                    "id": "writer",
                    "type": "agent",
                    "config": {
                        "system_prompt": "你是撰稿人,产出简短草稿。",
                        "user_prompt": "{{nodes.supervisor.output}}",
                    },
                    "position": {"x": 560, "y": -120},
                },
                {
                    "id": "reviewer",
                    "type": "agent",
                    "config": {
                        "system_prompt": "你是审校,给出修订要点。",
                        "user_prompt": "{{nodes.supervisor.output}}",
                    },
                    "position": {"x": 560, "y": 120},
                },
                {"id": "end", "type": "end", "config": {}, "position": {"x": 840, "y": 0}},
            ],
            "edges": [
                {"id": "e1", "source": "start", "target": "supervisor"},
                {"id": "e2", "source": "supervisor", "target": "writer"},
                {"id": "e3", "source": "supervisor", "target": "reviewer"},
                {"id": "e4", "source": "supervisor", "target": "end"},
                {"id": "e5", "source": "writer", "target": "supervisor"},
                {"id": "e6", "source": "reviewer", "target": "supervisor"},
            ],
        },
    },
    {
        "id": "demo-iteration",
        "name": "Demo · Batch Iteration",
        "description": "Process an input array through a bounded child Agent graph.",
        "dsl": {
            "version": "1.0",
            "name": "Demo · Batch Iteration",
            "variables": [{"name": "items", "type": "array", "required": True}],
            "settings": {
                "max_loop_iterations": 20,
                "timeout_seconds": 180,
                "recursion_limit": 100,
            },
            "nodes": [
                {
                    "id": "start",
                    "type": "start",
                    "config": {
                        "input_schema": [
                            {"name": "items", "type": "array", "required": True}
                        ]
                    },
                    "position": {"x": 0, "y": 0},
                },
                {
                    "id": "each",
                    "type": "iteration",
                    "config": {
                        "items": "{{input.items}}",
                        "item_variable": "item",
                        "index_variable": "index",
                        "batch_size": 10,
                        "concurrency_limit": 4,
                        "failure_strategy": "collect_error",
                        "recursion_limit": 50,
                        "subgraph": {
                            "nodes": [
                                {
                                    "id": "item_start",
                                    "type": "start",
                                    "config": {},
                                    "position": {"x": 0, "y": 0},
                                },
                                {
                                    "id": "item_agent",
                                    "type": "agent",
                                    "config": {
                                        "model_config_id": "default",
                                        "system_prompt": "Process one array item and return a concise result.",
                                        "user_prompt": "Item {{input.index}}: {{input.item}}",
                                    },
                                    "position": {"x": 260, "y": 0},
                                },
                                {
                                    "id": "item_end",
                                    "type": "end",
                                    "config": {
                                        "output_template": {
                                            "index": "{{input.index}}",
                                            "result": "{{nodes.item_agent.output}}",
                                        }
                                    },
                                    "position": {"x": 520, "y": 0},
                                },
                            ],
                            "edges": [
                                {
                                    "id": "item_1",
                                    "source": "item_start",
                                    "target": "item_agent",
                                },
                                {
                                    "id": "item_2",
                                    "source": "item_agent",
                                    "target": "item_end",
                                },
                            ],
                        },
                    },
                    "position": {"x": 300, "y": 0},
                },
                {
                    "id": "end",
                    "type": "end",
                    "config": {"output_template": {"batch": "{{nodes.each.output}}"}},
                    "position": {"x": 600, "y": 0},
                },
            ],
            "edges": [
                {"id": "e1", "source": "start", "target": "each"},
                {"id": "e2", "source": "each", "target": "end"},
            ],
        },
    },
    {
        "id": "demo-http",
        "name": "Demo · HTTP Fetch",
        "description": "Outbound HTTP GET with JSON extraction; the canonical C2-3 integration node.",
        "dsl": {
            "version": "1.0",
            "name": "Demo · HTTP Fetch",
            "variables": [],
            "settings": {
                "max_loop_iterations": 20,
                "timeout_seconds": 120,
                "recursion_limit": 50,
            },
            "nodes": [
                {
                    "id": "start",
                    "type": "start",
                    "config": {"input_schema": []},
                    "position": {"x": 0, "y": 0},
                },
                {
                    "id": "fetch",
                    "type": "http",
                    "config": {
                        "method": "GET",
                        "url": "https://jsonplaceholder.typicode.com/todos/1",
                        "timeout_seconds": 20.0,
                        "retry": {"max_attempts": 2, "retry_on_status": [408, 429, 502, 503, 504]},
                        "expected_status": [200],
                        "response": {
                            "extract_json": True,
                            "json_path": "",
                            "include_headers": False,
                            "text_fallback": True,
                        },
                        "allow_private_network": False,
                    },
                    "position": {"x": 300, "y": 0},
                },
                {
                    "id": "end",
                    "type": "end",
                    "config": {"output_template": {"todo": "{{nodes.fetch.output}}"}},
                    "position": {"x": 600, "y": 0},
                },
            ],
            "edges": [
                {"id": "e1", "source": "start", "target": "fetch"},
                {"id": "e2", "source": "fetch", "target": "end"},
            ],
        },
    },
    {
        "id": "demo-code",
        "name": "Demo · Code Transform",
        "description": "Sandboxed Python transforms an input number; the canonical C2-2 code node.",
        "dsl": {
            "version": "1.0",
            "name": "Demo · Code Transform",
            "variables": [{"name": "value", "type": "number", "required": True}],
            "settings": {
                "max_loop_iterations": 20,
                "timeout_seconds": 120,
                "recursion_limit": 50,
            },
            "nodes": [
                {
                    "id": "start",
                    "type": "start",
                    "config": {
                        "input_schema": [{"name": "value", "type": "number", "required": True}]
                    },
                    "position": {"x": 0, "y": 0},
                },
                {
                    "id": "transform",
                    "type": "code",
                    "config": {
                        "language": "python",
                        "source": "output = {\"doubled\": inputs[\"n\"] * 2, \"label\": str(inputs[\"n\"])}",
                        "inputs": {"n": "{{input.value}}"},
                        "timeout_seconds": 10.0,
                        "memory_limit_mb": 64,
                        "process_count": 8,
                        "allow_network": False,
                        "allow_filesystem": [],
                    },
                    "position": {"x": 300, "y": 0},
                },
                {
                    "id": "end",
                    "type": "end",
                    "config": {"output_template": {"result": "{{nodes.transform.output}}"}},
                    "position": {"x": 600, "y": 0},
                },
            ],
            "edges": [
                {"id": "e1", "source": "start", "target": "transform"},
                {"id": "e2", "source": "transform", "target": "end"},
            ],
        },
    },
    {
        "id": "demo-switch",
        "name": "Demo · Multi-Way Switch",
        "description": "Three-way switch routing by input size with an explicit merge strategy.",
        "dsl": {
            "version": "1.0",
            "name": "Demo · Multi-Way Switch",
            "variables": [{"name": "n", "type": "number", "required": True}],
            "settings": {
                "max_loop_iterations": 20,
                "timeout_seconds": 120,
                "recursion_limit": 50,
            },
            "nodes": [
                {
                    "id": "start",
                    "type": "start",
                    "config": {
                        "input_schema": [{"name": "n", "type": "number", "required": True}]
                    },
                    "position": {"x": 0, "y": 0},
                },
                {
                    "id": "switch",
                    "type": "switch",
                    "config": {
                        "branches": [
                            {
                                "id": "small",
                                "label": "小",
                                "group": {
                                    "op": "and",
                                    "rules": [
                                        {"left": "{{input.n}}", "operator": "lt", "right": 10}
                                    ],
                                },
                            },
                            {
                                "id": "medium",
                                "label": "中",
                                "group": {
                                    "op": "and",
                                    "rules": [
                                        {"left": "{{input.n}}", "operator": "lt", "right": 100}
                                    ],
                                },
                            },
                        ],
                        "default_branch": "large",
                        "merge_strategy": "first",
                    },
                    "position": {"x": 300, "y": 0},
                },
                {
                    "id": "agent_small",
                    "type": "agent",
                    "config": {
                        "system_prompt": "你是简洁助手。",
                        "user_prompt": "小数字 {{input.n}} 的处理结果。",
                    },
                    "position": {"x": 600, "y": -120},
                },
                {
                    "id": "agent_medium",
                    "type": "agent",
                    "config": {
                        "system_prompt": "你是简洁助手。",
                        "user_prompt": "中数字 {{input.n}} 的处理结果。",
                    },
                    "position": {"x": 600, "y": 0},
                },
                {
                    "id": "agent_large",
                    "type": "agent",
                    "config": {
                        "system_prompt": "你是简洁助手。",
                        "user_prompt": "大数字 {{input.n}} 的处理结果。",
                    },
                    "position": {"x": 600, "y": 120},
                },
                {
                    "id": "end",
                    "type": "end",
                    "config": {},
                    "position": {"x": 900, "y": 0},
                },
            ],
            "edges": [
                {"id": "e1", "source": "start", "target": "switch"},
                {"id": "e2", "source": "switch", "target": "agent_small", "source_handle": "small"},
                {"id": "e3", "source": "switch", "target": "agent_medium", "source_handle": "medium"},
                {"id": "e4", "source": "switch", "target": "agent_large", "source_handle": "large"},
                {"id": "e5", "source": "agent_small", "target": "end"},
                {"id": "e6", "source": "agent_medium", "target": "end"},
                {"id": "e7", "source": "agent_large", "target": "end"},
            ],
        },
    },
    {
        "id": "demo-subworkflow",
        "name": "Demo · Subworkflow Embed",
        "description": "Parent workflow embedding a child published workflow by id (C2-5).",
        "dsl": {
            "version": "1.0",
            "name": "Demo · Subworkflow Embed",
            "variables": [{"name": "user_query", "type": "string", "required": True}],
            "settings": {
                "max_loop_iterations": 20,
                "timeout_seconds": 180,
                "recursion_limit": 100,
            },
            "nodes": [
                {
                    "id": "start",
                    "type": "start",
                    "config": {
                        "input_schema": [{"name": "user_query", "type": "string", "required": True}]
                    },
                    "position": {"x": 0, "y": 0},
                },
                {
                    "id": "sub",
                    "type": "subworkflow",
                    "config": {
                        "workflow_id": "demo-linear",
                        "version_id": "",
                        "input_mapping": {"user_query": "{{input.user_query}}"},
                        "output_mapping": {},
                        "recursion_limit": 50,
                    },
                    "position": {"x": 300, "y": 0},
                },
                {
                    "id": "end",
                    "type": "end",
                    "config": {"output_template": {"child": "{{nodes.sub.output}}"}},
                    "position": {"x": 600, "y": 0},
                },
            ],
            "edges": [
                {"id": "e1", "source": "start", "target": "sub"},
                {"id": "e2", "source": "sub", "target": "end"},
            ],
        },
    },
)


async def seed_workflows(session_factory: async_sessionmaker[AsyncSession]) -> int:
    """Idempotently upsert the seed workflows. Returns number of new rows."""
    created = 0
    async with session_factory() as session:
        repo = WorkflowRepo(session)
        for seed in SEED_WORKFLOWS:
            existing = await repo.get(str(seed["id"]))
            if existing is None:
                row = Workflow(
                    id=str(seed["id"]),
                    name=str(seed["name"]),
                    description=str(seed.get("description", "")),
                    dsl_json=seed["dsl"],  # type: ignore[arg-type]
                    version=1,
                )
                session.add(row)
                await session.flush()
                await WorkflowVersionRepo(session).create_snapshot(row)
                created += 1
                logger.info("seeded workflow '%s'", seed["id"])
        await session.commit()
    return created


__all__ = ["SEED_WORKFLOWS", "seed_workflows"]
