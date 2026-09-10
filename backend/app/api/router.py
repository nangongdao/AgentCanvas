"""API router aggregation."""

from fastapi import APIRouter

from app.api.routes import (
    admin,
    app_runtime,
    apps,
    audit_logs,
    auth,
    chat,
    cost_alerts,
    evaluations,
    executions,
    knowledge,
    mcp,
    mcp_catalog,
    mcp_catalog_rollout,
    meta,
    oidc,
    org_invitations,
    org_plans,
    organizations,
    overview,
    project_quotas,
    resource_transfer,
    search,
    service_accounts,
    templates,
    usage,
    webhooks,
    workflow_api,
    workflow_callbacks,
    workflow_collaboration,
    workflow_copilot,
    workflow_reviews,
    workflow_schedules,
    workflow_transfer,
    workflow_versions,
    workflows,
)

api_router = APIRouter()
api_router.include_router(audit_logs.router)
api_router.include_router(admin.router)
api_router.include_router(auth.router)
api_router.include_router(apps.router)
api_router.include_router(app_runtime.router)
api_router.include_router(oidc.router)
api_router.include_router(templates.router)
api_router.include_router(usage.router)
api_router.include_router(workflow_transfer.router)
api_router.include_router(workflow_collaboration.router)
api_router.include_router(workflow_copilot.router)
api_router.include_router(workflow_reviews.router)
api_router.include_router(workflows.router)
api_router.include_router(workflow_versions.router)
api_router.include_router(workflow_schedules.router)
api_router.include_router(workflow_api.router)
api_router.include_router(executions.router)
api_router.include_router(organizations.router)
api_router.include_router(org_plans.router)
api_router.include_router(overview.router)
api_router.include_router(org_invitations.router)
api_router.include_router(resource_transfer.router)
api_router.include_router(project_quotas.router)
api_router.include_router(search.router)
api_router.include_router(service_accounts.router)
api_router.include_router(knowledge.router)
api_router.include_router(mcp.router)
api_router.include_router(mcp_catalog.router)
api_router.include_router(mcp_catalog_rollout.router)
api_router.include_router(chat.router)
api_router.include_router(evaluations.router)
api_router.include_router(cost_alerts.router)
api_router.include_router(meta.router)
api_router.include_router(webhooks.router)
api_router.include_router(workflow_callbacks.router)
