"""Basic usage example for AgentCanvas Python SDK."""

from agentcanvas import (
    AgentCanvasClient,
    CreateWorkflowInput,
    PaginationParams,
    StartExecutionInput,
    UpdateWorkflowInput,
)


def main() -> None:
    """Demonstrate basic SDK usage."""
    # Initialize client
    client = AgentCanvasClient(
        api_key="your-api-key-here",
        base_url="http://localhost:8000",
        timeout=30.0,
    )

    try:
        # 1. Create a simple workflow
        print("Creating workflow...")
        workflow = client.create_workflow(
            CreateWorkflowInput(
                name="Example Workflow",
                description="A simple example workflow",
                nodes=[
                    {
                        "id": "start",
                        "type": "start",
                        "position": {"x": 100, "y": 100},
                        "data": {"label": "Start"},
                    },
                    {
                        "id": "llm",
                        "type": "llm",
                        "position": {"x": 300, "y": 100},
                        "data": {
                            "label": "LLM Node",
                            "provider": "openai",
                            "model": "gpt-4",
                            "prompt": "Process: {{input}}",
                        },
                    },
                    {
                        "id": "end",
                        "type": "end",
                        "position": {"x": 500, "y": 100},
                        "data": {"label": "End"},
                    },
                ],
                edges=[
                    {
                        "id": "e1",
                        "source": "start",
                        "target": "llm",
                        "sourceHandle": "output",
                        "targetHandle": "input",
                    },
                    {
                        "id": "e2",
                        "source": "llm",
                        "target": "end",
                        "sourceHandle": "output",
                        "targetHandle": "input",
                    },
                ],
            )
        )
        print(f"✓ Workflow created: {workflow.id}")

        # 2. List workflows
        print("\nListing workflows...")
        workflows = client.list_workflows(PaginationParams(skip=0, limit=5))
        print(f"✓ Found {len(workflows)} workflows")

        # 3. Get workflow details
        print(f"\nGetting workflow {workflow.id}...")
        retrieved = client.get_workflow(workflow.id)
        print(f"✓ Workflow: {retrieved.name}")
        print(f"  Nodes: {len(retrieved.nodes)}")
        print(f"  Edges: {len(retrieved.edges)}")

        # 4. Update workflow
        print("\nUpdating workflow...")
        updated = client.update_workflow(
            workflow.id,
            UpdateWorkflowInput(
                name="Updated Example Workflow",
                description="Updated description",
            ),
        )
        print(f"✓ Workflow updated: {updated.name}")

        # 5. Start execution
        print("\nStarting execution...")
        execution = client.start_execution(
            StartExecutionInput(
                workflow_id=workflow.id,
                inputs={"input": "Hello, AgentCanvas!"},
            )
        )
        print(f"✓ Execution started: {execution.id}")
        print(f"  Status: {execution.status}")

        # 6. Get execution details
        print(f"\nGetting execution {execution.id}...")
        exec_details = client.get_execution(execution.id)
        print(f"✓ Execution status: {exec_details.status}")

        # 7. List executions
        print("\nListing executions...")
        executions = client.list_executions(PaginationParams(limit=5))
        print(f"✓ Found {len(executions)} executions")

        # 8. Cancel execution (if still running)
        if exec_details.status in ["queued", "running"]:
            print(f"\nCancelling execution {execution.id}...")
            client.cancel_execution(execution.id)
            print("✓ Execution cancelled")

        # 9. Delete workflow
        print(f"\nDeleting workflow {workflow.id}...")
        client.delete_workflow(workflow.id)
        print("✓ Workflow deleted")

    finally:
        # Clean up
        client.close()
        print("\n✓ Client closed")


if __name__ == "__main__":
    # Alternative: use context manager for automatic cleanup
    print("=== AgentCanvas Python SDK Example ===\n")

    with AgentCanvasClient(
        api_key="your-api-key-here",
        base_url="http://localhost:8000",
    ) as client:
        workflows = client.list_workflows(PaginationParams(limit=3))
        print(f"Context manager: Found {len(workflows)} workflows")

    print("\n=== Running full example ===\n")
    main()
