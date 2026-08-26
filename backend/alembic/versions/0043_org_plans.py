"""Named quota plans bound to organizations (C7-2).

An ``org_plans`` row bundles the five project quota limits plus the overage
policy for the two monthly metered metrics (embedding input bytes and model
cost): ``hard`` rejects the charge that would cross the limit, ``soft``
records the overage, writes one durable cost alert at the crossing, and lets
the request continue. Realtime limits (concurrent executions, storage,
stdio MCP processes) always reject — they gate resources that cannot be
safely over-allocated.

Binding a plan to an organization copies the plan's limits and policies onto
every project quota row of that organization in one transaction, so an
upgrade or downgrade is exactly a batch quota adjustment. Unbinding keeps
the last applied limits in place. Three immutable system plans (free, pro,
enterprise) are seeded idempotently as examples.
"""

from __future__ import annotations

import sqlalchemy as sa

from alembic import op

revision = "0043_org_plans"
down_revision = "0042_usage_daily_facts"
branch_labels = None
depends_on = None

SEED_PLAN_FREE_ID = "seedplanfree00000000000000000"
SEED_PLAN_PRO_ID = "seedplanpro000000000000000000"
SEED_PLAN_ENTERPRISE_ID = "seedplanenterprise00000000000"

# 1 GiB / 25 GiB / 200 GiB and 50 MiB / 1 GiB / 10 GiB.
_SEED_STORAGE_BYTES = {
    SEED_PLAN_FREE_ID: 1_073_741_824,
    SEED_PLAN_PRO_ID: 26_843_545_600,
    SEED_PLAN_ENTERPRISE_ID: 214_748_364_800,
}
_SEED_EMBEDDING_BYTES = {
    SEED_PLAN_FREE_ID: 52_428_800,
    SEED_PLAN_PRO_ID: 1_073_741_824,
    SEED_PLAN_ENTERPRISE_ID: 10_737_418_240,
}
# Model cost limits are 1e-12 USD integer units: $10 / $200 / $2000.
_SEED_MODEL_COST_UNITS = {
    SEED_PLAN_FREE_ID: 10_000_000_000_000,
    SEED_PLAN_PRO_ID: 200_000_000_000_000,
    SEED_PLAN_ENTERPRISE_ID: 2_000_000_000_000_000,
}
_SEED_CONCURRENT_EXECUTIONS = {
    SEED_PLAN_FREE_ID: 5,
    SEED_PLAN_PRO_ID: 25,
    SEED_PLAN_ENTERPRISE_ID: 100,
}
_SEED_STDIO_PROCESSES = {
    SEED_PLAN_FREE_ID: 2,
    SEED_PLAN_PRO_ID: 8,
    SEED_PLAN_ENTERPRISE_ID: 32,
}
# free: everything hard; pro: model cost soft; enterprise: both soft.
_SEED_EMBEDDING_POLICY = {
    SEED_PLAN_FREE_ID: "hard",
    SEED_PLAN_PRO_ID: "hard",
    SEED_PLAN_ENTERPRISE_ID: "soft",
}
_SEED_MODEL_COST_POLICY = {
    SEED_PLAN_FREE_ID: "hard",
    SEED_PLAN_PRO_ID: "soft",
    SEED_PLAN_ENTERPRISE_ID: "soft",
}


def upgrade() -> None:
    op.create_table(
        "org_plans",
        sa.Column("id", sa.String(32), primary_key=True),
        sa.Column("slug", sa.String(64), nullable=False, unique=True),
        sa.Column("name", sa.String(120), nullable=False),
        sa.Column("description", sa.Text(), nullable=False, server_default=""),
        sa.Column("concurrent_execution_limit", sa.BigInteger(), nullable=True),
        sa.Column("storage_bytes_limit", sa.BigInteger(), nullable=True),
        sa.Column("monthly_embedding_input_bytes_limit", sa.BigInteger(), nullable=True),
        sa.Column("monthly_model_cost_units_limit", sa.BigInteger(), nullable=True),
        sa.Column("stdio_mcp_process_limit", sa.BigInteger(), nullable=True),
        sa.Column(
            "embedding_overage_policy",
            sa.String(8),
            nullable=False,
            server_default="hard",
        ),
        sa.Column(
            "model_cost_overage_policy",
            sa.String(8),
            nullable=False,
            server_default="hard",
        ),
        sa.Column("is_system", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("CURRENT_TIMESTAMP"),
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("CURRENT_TIMESTAMP"),
        ),
        sa.CheckConstraint(
            "embedding_overage_policy IN ('hard', 'soft')",
            name="ck_org_plans_embedding_overage_policy",
        ),
        sa.CheckConstraint(
            "model_cost_overage_policy IN ('hard', 'soft')",
            name="ck_org_plans_model_cost_overage_policy",
        ),
        sa.CheckConstraint(
            "concurrent_execution_limit IS NULL OR concurrent_execution_limit >= 0",
            name="ck_org_plans_concurrent_execution_limit",
        ),
        sa.CheckConstraint(
            "storage_bytes_limit IS NULL OR storage_bytes_limit >= 0",
            name="ck_org_plans_storage_bytes_limit",
        ),
        sa.CheckConstraint(
            "monthly_embedding_input_bytes_limit IS NULL "
            "OR monthly_embedding_input_bytes_limit >= 0",
            name="ck_org_plans_monthly_embedding_input_bytes_limit",
        ),
        sa.CheckConstraint(
            "monthly_model_cost_units_limit IS NULL OR monthly_model_cost_units_limit >= 0",
            name="ck_org_plans_monthly_model_cost_units_limit",
        ),
        sa.CheckConstraint(
            "stdio_mcp_process_limit IS NULL OR stdio_mcp_process_limit >= 0",
            name="ck_org_plans_stdio_mcp_process_limit",
        ),
    )

    # Batch mode keeps SQLite viable for the column additions.
    with op.batch_alter_table("organizations") as batch:
        batch.add_column(sa.Column("plan_id", sa.String(32), nullable=True))
        batch.create_foreign_key(
            "fk_organizations_plan",
            "org_plans",
            ["plan_id"],
            ["id"],
            ondelete="SET NULL",
        )
        batch.add_column(sa.Column("plan_assigned_at", sa.DateTime(timezone=True), nullable=True))
        batch.add_column(sa.Column("plan_assigned_by", sa.String(200), nullable=True))

    with op.batch_alter_table("project_quotas") as batch:
        batch.add_column(
            sa.Column(
                "embedding_overage_policy",
                sa.String(8),
                nullable=False,
                server_default="hard",
            )
        )
        batch.create_check_constraint(
            "ck_quota_embedding_overage_policy",
            "embedding_overage_policy IN ('hard', 'soft')",
        )
        batch.add_column(
            sa.Column(
                "model_cost_overage_policy",
                sa.String(8),
                nullable=False,
                server_default="hard",
            )
        )
        batch.create_check_constraint(
            "ck_quota_model_cost_overage_policy",
            "model_cost_overage_policy IN ('hard', 'soft')",
        )

    for plan_id, slug, name, description in (
        (
            SEED_PLAN_FREE_ID,
            "free",
            "Free",
            "Example free tier: tight hard limits for evaluation projects.",
        ),
        (
            SEED_PLAN_PRO_ID,
            "pro",
            "Pro",
            "Example professional tier: higher limits, soft model-cost overage.",
        ),
        (
            SEED_PLAN_ENTERPRISE_ID,
            "enterprise",
            "Enterprise",
            "Example enterprise tier: large limits, soft monthly overage.",
        ),
    ):
        op.execute(
            sa.text(
                "INSERT INTO org_plans (id, slug, name, description, "
                "concurrent_execution_limit, storage_bytes_limit, "
                "monthly_embedding_input_bytes_limit, monthly_model_cost_units_limit, "
                "stdio_mcp_process_limit, embedding_overage_policy, "
                "model_cost_overage_policy, is_system) "
                "SELECT :id, :slug, :name, :description, :concurrent, :storage, "
                ":embedding, :model_cost, :stdio, :embedding_policy, "
                ":model_cost_policy, TRUE "
                "WHERE NOT EXISTS (SELECT 1 FROM org_plans WHERE id = :id)"
            ).bindparams(
                id=plan_id,
                slug=slug,
                name=name,
                description=description,
                concurrent=_SEED_CONCURRENT_EXECUTIONS[plan_id],
                storage=_SEED_STORAGE_BYTES[plan_id],
                embedding=_SEED_EMBEDDING_BYTES[plan_id],
                model_cost=_SEED_MODEL_COST_UNITS[plan_id],
                stdio=_SEED_STDIO_PROCESSES[plan_id],
                embedding_policy=_SEED_EMBEDDING_POLICY[plan_id],
                model_cost_policy=_SEED_MODEL_COST_POLICY[plan_id],
            )
        )


def downgrade() -> None:
    with op.batch_alter_table("project_quotas") as batch:
        batch.drop_constraint("ck_quota_model_cost_overage_policy", type_=None)
        batch.drop_column("model_cost_overage_policy")
        batch.drop_constraint("ck_quota_embedding_overage_policy", type_=None)
        batch.drop_column("embedding_overage_policy")
    with op.batch_alter_table("organizations") as batch:
        batch.drop_column("plan_assigned_by")
        batch.drop_column("plan_assigned_at")
        batch.drop_constraint("fk_organizations_plan", type_=None)
        batch.drop_column("plan_id")
    op.drop_table("org_plans")
