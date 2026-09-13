"""Provider management example for AgentCanvas Python SDK."""

from agentcanvas import (
    AgentCanvasClient,
    CreateProviderInput,
    NotFoundError,
    UpdateProviderInput,
)


def main() -> None:
    """Demonstrate provider management operations."""
    with AgentCanvasClient(
        api_key="your-api-key-here",
        base_url="http://localhost:8000",
    ) as client:
        print("=== Provider Management Example ===\n")

        # 1. Create OpenAI provider
        print("Creating OpenAI provider...")
        openai_provider = client.create_provider(
            CreateProviderInput(
                name="OpenAI",
                type="openai",
                api_key="sk-your-openai-key",
                models=["gpt-4", "gpt-3.5-turbo"],
            )
        )
        print(f"✓ Provider created: {openai_provider.id}")
        print(f"  Type: {openai_provider.type}")
        print(f"  Models: {', '.join(openai_provider.models)}")

        # 2. Create Anthropic provider
        print("\nCreating Anthropic provider...")
        anthropic_provider = client.create_provider(
            CreateProviderInput(
                name="Anthropic",
                type="anthropic",
                api_key="sk-ant-your-key",
                models=["claude-3-opus-20240229", "claude-3-sonnet-20240229"],
            )
        )
        print(f"✓ Provider created: {anthropic_provider.id}")
        print(f"  Type: {anthropic_provider.type}")

        # 3. List all providers
        print("\nListing all providers...")
        providers = client.list_providers()
        print(f"✓ Found {len(providers)} providers:")
        for provider in providers:
            print(f"  - {provider.name} ({provider.type})")

        # 4. Get specific provider
        print(f"\nGetting provider {openai_provider.id}...")
        retrieved = client.get_provider(openai_provider.id)
        print(f"✓ Provider: {retrieved.name}")
        print(f"  Created: {retrieved.created_at}")
        print(f"  Updated: {retrieved.updated_at}")

        # 5. Update provider models
        print(f"\nUpdating provider {openai_provider.id}...")
        updated = client.update_provider(
            openai_provider.id,
            UpdateProviderInput(
                models=["gpt-4", "gpt-3.5-turbo", "gpt-4-turbo-preview"]
            ),
        )
        print("✓ Provider updated")
        print(f"  New models: {', '.join(updated.models)}")

        # 6. Delete providers
        print(f"\nDeleting provider {openai_provider.id}...")
        client.delete_provider(openai_provider.id)
        print("✓ OpenAI provider deleted")

        print(f"\nDeleting provider {anthropic_provider.id}...")
        client.delete_provider(anthropic_provider.id)
        print("✓ Anthropic provider deleted")

        # 7. Verify deletion
        print("\nVerifying deletion...")
        try:
            client.get_provider(openai_provider.id)
            print("✗ Provider still exists (unexpected)")
        except NotFoundError:
            print("✓ Provider successfully deleted")

        print("\n=== Example completed ===")


if __name__ == "__main__":
    main()
