"""Error handling example for AgentCanvas Python SDK."""

from agentcanvas import (
    AgentCanvasClient,
    AgentCanvasError,
    AuthenticationError,
    CreateWorkflowInput,
    NotFoundError,
    RateLimitError,
    ServerError,
    TimeoutError,
    ValidationError,
)


def main() -> None:
    """Demonstrate error handling patterns."""
    print("=== Error Handling Example ===\n")

    # 1. Authentication error
    print("1. Testing authentication error...")
    try:
        client = AgentCanvasClient(
            api_key="invalid-key",
            base_url="http://localhost:8000",
        )
        client.list_workflows()
    except AuthenticationError as e:
        print(f"✓ Caught AuthenticationError: {e}")
        print(f"  Status code: {e.status_code}")

    # 2. Not found error
    print("\n2. Testing not found error...")
    with AgentCanvasClient(
        api_key="your-api-key-here",
        base_url="http://localhost:8000",
    ) as client:
        try:
            client.get_workflow("nonexistent-id")
        except NotFoundError as e:
            print(f"✓ Caught NotFoundError: {e}")
            print(f"  Status code: {e.status_code}")

        # 3. Validation error
        print("\n3. Testing validation error...")
        try:
            client.create_workflow(
                CreateWorkflowInput(
                    name="",  # Empty name should fail validation
                    nodes=[],
                    edges=[],
                )
            )
        except ValidationError as e:
            print(f"✓ Caught ValidationError: {e}")
            print(f"  Status code: {e.status_code}")

        # 4. Timeout error
        print("\n4. Testing timeout error...")
        timeout_client = AgentCanvasClient(
            api_key="your-api-key-here",
            base_url="http://localhost:8000",
            timeout=0.001,  # Very short timeout
        )
        try:
            timeout_client.list_workflows()
        except TimeoutError as e:
            print(f"✓ Caught TimeoutError: {e}")
        finally:
            timeout_client.close()

        # 5. Generic error handling
        print("\n5. Generic error handling pattern...")
        try:
            # Some operation that might fail
            client.get_workflow("some-id")
        except NotFoundError:
            print("✓ Resource not found - creating new one")
        except AuthenticationError:
            print("✓ Authentication failed - check credentials")
        except ValidationError as e:
            print(f"✓ Validation failed: {e}")
        except RateLimitError:
            print("✓ Rate limit exceeded - retry later")
        except ServerError as e:
            print(f"✓ Server error: {e}")
        except TimeoutError:
            print("✓ Request timed out - retry")
        except AgentCanvasError as e:
            print(f"✓ Unexpected error: {e}")

        # 6. Retry pattern
        print("\n6. Retry pattern with exponential backoff...")
        import time

        max_retries = 3
        for attempt in range(max_retries):
            try:
                client.list_workflows()
                print(f"✓ Success on attempt {attempt + 1}")
                break
            except (TimeoutError, ServerError) as e:
                if attempt < max_retries - 1:
                    wait_time = 2 ** attempt
                    print(f"  Attempt {attempt + 1} failed: {e}")
                    print(f"  Retrying in {wait_time}s...")
                    time.sleep(wait_time)
                else:
                    print(f"✗ All {max_retries} attempts failed")
                    raise

        print("\n=== Example completed ===")


if __name__ == "__main__":
    main()
