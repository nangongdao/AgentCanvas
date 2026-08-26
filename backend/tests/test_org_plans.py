"""Named quota plans, organization binding, and overage policies (C7-2)."""

from __future__ import annotations

import asyncio
from datetime import UTC, datetime
from pathlib import Path

from fastapi.testclient import TestClient
from sqlalchemy import select

from app.core.config import Settings
from app.db.base import create_engine, create_session_factory
from app.db.migrations import upgrade_database
from app.db.models import CostAlert, Organization, Project, ProjectQuota
from app.main import create_app
from app.services.org_plans import OrgPlanService
from app.services.project_model_costs import ProjectModelCostMeter
from app.services.project_quotas import (
    ProjectQuotaExceeded,
    ProjectQuotaService,
)

FERNET_KEY = "BjzaAlRXaAJ8S_6Vj4_Yf6YmMBtMo2rHtE1L6T2HYXs="
ADMIN_TOKEN = "test-admin-token-org-plans"

_NOW = datetime.now(UTC)


def _settings(tmp_path: Path, **overrides) -> Settings:
    base = dict(
        data_dir=tmp_path,
        environment="test",
        auth_mode="disabled",
        secret_key=FERNET_KEY,
        local_embedding_dimensions=64,
        rate_limit_execution_requests=100,
        execution_max_concurrent=32,
        model_max_concurrent=32,
        log_level="WARNING",
    )
    base.update(overrides)
    return Settings(**base)


def _api_settings(tmp_path: Path) -> Settings:
    return _settings(
        tmp_path,
        auth_mode="token",
        admin_api_token=ADMIN_TOKEN,
        rate_limit_default_requests=1000,
    )


def _admin_headers() -> dict[str, str]:
    return {"Authorization": f"Bearer {ADMIN_TOKEN}"}


def _seed_org_with_projects(client: TestClient, *, name: str, projects: int) -> dict[str, str]:
    registered = client.post(
        "/api/auth/register",
        json={
            "email": f"{name.lower()}@example.test",
            "password": "plan-password",
            "display_name": name,
            "role": "admin",
        },
    )
    assert registered.status_code == 201, registered.text
    org = client.post("/api/organizations", json={"name": f"{name} Org"})
    assert org.status_code == 201, org.text
    org_id = org.json()["id"]
    project_ids = []
    for index in range(projects):
        project = client.post(
            f"/api/organizations/{org_id}/projects", json={"name": f"{name} Project {index}"}
        )
        assert project.status_code == 201, project.text
        project_ids.append(project.json()["id"])
    return {"org_id": org_id, "project_ids": project_ids}


def test_seed_plans_exist_and_system_guard(tmp_path: Path) -> None:
    settings = _api_settings(tmp_path)
    with TestClient(create_app(settings)) as client:
        listed = client.get("/api/org-plans", headers=_admin_headers())
        assert listed.status_code == 200, listed.text
        plans = {plan["slug"]: plan for plan in listed.json()}
        assert {"free", "pro", "enterprise"} <= set(plans)
        free = plans["free"]
        assert free["concurrent_execution_limit"] == 5
        assert free["model_cost_overage_policy"] == "hard"
        assert free["monthly_model_cost_usd_limit"] == "10.000000000000"
        pro = plans["pro"]
        assert pro["model_cost_overage_policy"] == "soft"
        assert pro["embedding_overage_policy"] == "hard"
        enterprise = plans["enterprise"]
        assert enterprise["model_cost_overage_policy"] == "soft"
        assert enterprise["embedding_overage_policy"] == "soft"
        assert all(plan["is_system"] for plan in (free, pro, enterprise))

        deleted = client.delete(f"/api/org-plans/{free['id']}", headers=_admin_headers())
        assert deleted.status_code == 409


def test_plan_crud_rbac_and_delete_guards(tmp_path: Path) -> None:
    settings = _api_settings(tmp_path)
    with TestClient(create_app(settings)) as client:
        ids = _seed_org_with_projects(client, name="PlanCrud", projects=1)

        # An organization admin who is not a platform admin cannot manage
        # plan definitions.
        org_admin = client.post(
            "/api/auth/register",
            json={
                "email": "plan-org-admin@example.test",
                "password": "plan-password",
                "display_name": "Plan Org Admin",
                "role": "editor",
            },
        )
        assert org_admin.status_code == 201, org_admin.text
        added = client.post(
            f"/api/organizations/{ids['org_id']}/members",
            json={"email": "plan-org-admin@example.test", "role": "admin"},
        )
        assert added.status_code == 201, added.text
        org_admin_login = client.post(
            "/api/auth/login",
            json={"email": "plan-org-admin@example.test", "password": "plan-password"},
        )
        assert org_admin_login.status_code == 200
        forbidden = client.post(
            "/api/org-plans",
            json={"slug": "custom", "name": "Custom"},
        )
        assert forbidden.status_code == 403
        forbidden_list = client.get("/api/org-plans")
        assert forbidden_list.status_code == 403

        # Return to the static platform-admin token for definition CRUD.
        created = client.post(
            "/api/org-plans",
            headers=_admin_headers(),
            json={
                "slug": "custom",
                "name": "Custom Plan",
                "concurrent_execution_limit": 7,
                "monthly_model_cost_usd_limit": "1.5",
                "model_cost_overage_policy": "soft",
            },
        )
        assert created.status_code == 201, created.text
        plan = created.json()
        assert plan["monthly_model_cost_usd_limit"] == "1.500000000000"
        assert plan["model_cost_overage_policy"] == "soft"
        assert plan["organization_count"] == 0

        duplicate = client.post(
            "/api/org-plans", headers=_admin_headers(), json={"slug": "custom", "name": "Dup"}
        )
        assert duplicate.status_code == 409

        updated = client.put(
            f"/api/org-plans/{plan['id']}",
            headers=_admin_headers(),
            json={"name": "Custom Plan v2", "storage_bytes_limit": 12345},
        )
        assert updated.status_code == 200, updated.text
        assert updated.json()["name"] == "Custom Plan v2"
        assert updated.json()["storage_bytes_limit"] == 12345

        # Non-platform-admin sessions cannot list plan definitions.
        assert client.get("/api/org-plans").status_code == 403

        # Bind the plan as the organization admin (binding is an org-level
        # action), then deletion must be refused while bound.
        bind = client.put(
            f"/api/organizations/{ids['org_id']}/plan",
            json={"plan_id": plan["id"]},
        )
        assert bind.status_code == 200, bind.text
        bound_delete = client.delete(f"/api/org-plans/{plan['id']}", headers=_admin_headers())
        assert bound_delete.status_code == 409

        # Unbind keeps limits; deletion now succeeds.
        unbind = client.put(f"/api/organizations/{ids['org_id']}/plan", json={"plan_id": None})
        assert unbind.status_code == 200
        assert unbind.json()["plan"] is None
        deleted = client.delete(f"/api/org-plans/{plan['id']}", headers=_admin_headers())
        assert deleted.status_code == 204

        missing = client.get("/api/org-plans/does-not-exist", headers=_admin_headers())
        assert missing.status_code == 404


def test_bind_applies_limits_and_policies_to_all_projects(tmp_path: Path) -> None:
    settings = _api_settings(tmp_path)
    with TestClient(create_app(settings)) as client:
        ids = _seed_org_with_projects(client, name="BindApply", projects=3)
        org_id = ids["org_id"]

        # Give one project a bespoke limit first: binding must overwrite it.
        bespoke = client.put(
            f"/api/projects/{ids['project_ids'][0]}/quotas",
            json={"concurrent_execution_limit": 999},
        )
        assert bespoke.status_code == 200, bespoke.text

        plans = {
            plan["slug"]: plan
            for plan in client.get("/api/org-plans", headers=_admin_headers()).json()
        }
        bind = client.put(
            f"/api/organizations/{org_id}/plan", json={"plan_id": plans["free"]["id"]}
        )
        assert bind.status_code == 200, bind.text
        assert bind.json()["plan"]["slug"] == "free"
        assert bind.json()["assigned_at"] is not None
        assert bind.json()["assigned_by"]

        for project_id in ids["project_ids"]:
            quota = client.get(f"/api/projects/{project_id}/quotas").json()
            assert quota["concurrent_execution_limit"] == 5
            assert quota["stdio_mcp_process_limit"] == 2
            assert quota["monthly_model_cost_usd_limit"] == "10.000000000000"
            assert quota["embedding_overage_policy"] == "hard"
            assert quota["model_cost_overage_policy"] == "hard"

        # Upgrade to enterprise: every project flips limits and policies.
        upgrade = client.put(
            f"/api/organizations/{org_id}/plan", json={"plan_id": plans["enterprise"]["id"]}
        )
        assert upgrade.status_code == 200
        for project_id in ids["project_ids"]:
            quota = client.get(f"/api/projects/{project_id}/quotas").json()
            assert quota["concurrent_execution_limit"] == 100
            assert quota["embedding_overage_policy"] == "soft"
            assert quota["model_cost_overage_policy"] == "soft"

        # Members can view the assignment; a non-member cannot.
        assignment = client.get(f"/api/organizations/{org_id}/plan")
        assert assignment.status_code == 200
        assert assignment.json()["plan"]["slug"] == "enterprise"

        # Unknown plan id is a 404, not a silent no-op.
        assert (
            client.put(f"/api/organizations/{org_id}/plan", json={"plan_id": "nope"}).status_code
            == 404
        )


def test_viewer_cannot_bind_plan(tmp_path: Path) -> None:
    settings = _api_settings(tmp_path)
    with TestClient(create_app(settings)) as client:
        ids = _seed_org_with_projects(client, name="ViewerBind", projects=1)
        viewer = client.post(
            "/api/auth/register",
            json={
                "email": "viewer-bind@example.test",
                "password": "plan-password",
                "display_name": "Viewer Bind",
                "role": "viewer",
            },
        )
        assert viewer.status_code == 201, viewer.text
        login = client.post(
            "/api/auth/login",
            json={"email": "viewer-bind@example.test", "password": "plan-password"},
        )
        assert login.status_code == 200
        plans = client.get("/api/org-plans", headers=_admin_headers()).json()
        free = next(plan for plan in plans if plan["slug"] == "free")
        bind = client.put(f"/api/organizations/{ids['org_id']}/plan", json={"plan_id": free["id"]})
        assert bind.status_code == 403


async def _seed_project_with_quota(
    session_factory,
    *,
    org_id: str,
    project_id: str,
    embedding_policy: str,
    model_cost_policy: str,
    embedding_limit: int,
    model_cost_limit: int,
) -> None:
    async with session_factory() as session:
        if await session.get(Organization, org_id) is None:
            session.add(Organization(id=org_id, name=f"Org {org_id}", slug=f"slug-{org_id}"))
            await session.flush()
        if await session.get(Project, project_id) is None:
            session.add(
                Project(
                    id=project_id,
                    name=f"Project {project_id}",
                    slug=f"slug-{project_id}",
                    organization_id=org_id,
                )
            )
            await session.flush()
        session.add(
            ProjectQuota(
                project_id=project_id,
                monthly_embedding_input_bytes_limit=embedding_limit,
                monthly_model_cost_units_limit=model_cost_limit,
                embedding_overage_policy=embedding_policy,
                model_cost_overage_policy=model_cost_policy,
            )
        )
        await session.commit()


async def test_charge_monthly_soft_vs_hard_and_alert_once(tmp_path: Path) -> None:
    settings = _settings(tmp_path)
    await upgrade_database(settings)
    engine = create_engine(settings)
    session_factory = create_session_factory(engine)
    try:
        await _seed_project_with_quota(
            session_factory,
            org_id="org-soft",
            project_id="prj-soft",
            embedding_policy="soft",
            model_cost_policy="soft",
            embedding_limit=100,
            model_cost_limit=1_000,
        )
        await _seed_project_with_quota(
            session_factory,
            org_id="org-hard",
            project_id="prj-hard",
            embedding_policy="hard",
            model_cost_policy="hard",
            embedding_limit=100,
            model_cost_limit=1_000,
        )

        # Hard policy still rejects.
        async with session_factory() as session:
            service = ProjectQuotaService(session)
            try:
                await service.charge_monthly("prj-hard", "embedding_input_bytes", 150)
            except ProjectQuotaExceeded:
                pass
            else:
                raise AssertionError("hard policy must reject the crossing charge")
            await session.rollback()

        # Soft policy allows the crossing charge and writes one alert.
        async with session_factory() as session:
            await ProjectQuotaService(session).charge_monthly(
                "prj-soft", "embedding_input_bytes", 60
            )
            await session.commit()
        async with session_factory() as session:
            await ProjectQuotaService(session).charge_monthly(
                "prj-soft", "embedding_input_bytes", 60
            )
            await session.commit()
        # Further over-limit charges do not add more alerts.
        async with session_factory() as session:
            await ProjectQuotaService(session).charge_monthly(
                "prj-soft", "embedding_input_bytes", 10
            )
            await session.commit()

        async with session_factory() as session:
            alerts = (
                (await session.execute(select(CostAlert).where(CostAlert.kind == "quota")))
                .scalars()
                .all()
            )
            assert len(alerts) == 1
            alert = alerts[0]
            assert alert.severity == "warning"
            assert alert.status == "open"
            assert alert.limit_value == "100"
            assert alert.actual_value == "120"
            usage = await ProjectQuotaService(session).snapshot("prj-soft")
            assert usage.embedding_input_bytes == 130
    finally:
        await engine.dispose()


async def test_model_cost_soft_preflight_and_record(tmp_path: Path) -> None:
    settings = _settings(tmp_path)
    await upgrade_database(settings)
    engine = create_engine(settings)
    session_factory = create_session_factory(engine)
    try:
        # Cost units are 1e-12 USD: at $1 per million tokens one prompt
        # token prices to 1_000_000 units, so a 1_000_000-unit ceiling is
        # exactly one token.
        await _seed_project_with_quota(
            session_factory,
            org_id="org-mc",
            project_id="prj-mc-soft",
            embedding_policy="hard",
            model_cost_policy="soft",
            embedding_limit=1000,
            model_cost_limit=1_000_000,
        )
        await _seed_project_with_quota(
            session_factory,
            org_id="org-mc",
            project_id="prj-mc-hard",
            embedding_policy="hard",
            model_cost_policy="hard",
            embedding_limit=1000,
            model_cost_limit=1_000_000,
        )

        class _PricedProvider:
            prompt_price_per_million_usd = "1"
            completion_price_per_million_usd = "1"

        def _usage(tokens: int):  # minimal duck-typed Usage
            from app.providers.base import Usage

            return Usage(prompt_tokens=tokens, completion_tokens=0, total_tokens=tokens)

        # Bring both projects to the ceiling, then verify divergence.
        async with session_factory() as session:
            await ProjectQuotaService(session).charge_monthly(
                "prj-mc-soft", "model_cost_units", 1_000_000, allow_overage=True
            )
            await ProjectQuotaService(session).charge_monthly(
                "prj-mc-hard", "model_cost_units", 1_000_000, allow_overage=True
            )
            await session.commit()

        # Hard: preflight rejects at the ceiling.
        hard_meter = ProjectModelCostMeter(session_factory, "prj-mc-hard")
        try:
            await hard_meter.preflight(_PricedProvider())  # type: ignore[arg-type]
        except ProjectQuotaExceeded:
            pass
        else:
            raise AssertionError("hard policy preflight must reject at the ceiling")

        # Soft: preflight passes and record charges the overage with an alert.
        soft_meter = ProjectModelCostMeter(session_factory, "prj-mc-soft")
        await soft_meter.preflight(_PricedProvider())  # type: ignore[arg-type]
        await soft_meter.record(_PricedProvider(), _usage(1))  # type: ignore[arg-type]

        async with session_factory() as session:
            usage = await ProjectQuotaService(session).snapshot("prj-mc-soft")
            assert usage.model_cost_units == 2_000_000
            alerts = (
                (
                    await session.execute(
                        select(CostAlert).where(
                            CostAlert.kind == "quota", CostAlert.execution_id.is_(None)
                        )
                    )
                )
                .scalars()
                .all()
            )
            assert len(alerts) == 1
            assert alerts[0].severity == "warning"
            assert alerts[0].limit_value == "1000000"
    finally:
        await engine.dispose()


def test_audit_rows_written_for_plan_changes(tmp_path: Path) -> None:
    settings = _api_settings(tmp_path)
    with TestClient(create_app(settings)) as client:
        ids = _seed_org_with_projects(client, name="PlanAudit", projects=1)
        created = client.post(
            "/api/org-plans",
            headers=_admin_headers(),
            json={"slug": "audited", "name": "Audited Plan"},
        )
        assert created.status_code == 201
        bind = client.put(
            f"/api/organizations/{ids['org_id']}/plan",
            json={"plan_id": created.json()["id"]},
        )
        assert bind.status_code == 200
        unbind = client.put(f"/api/organizations/{ids['org_id']}/plan", json={"plan_id": None})
        assert unbind.status_code == 200

        container = client.app.state.container

        async def _actions() -> list[str]:
            from app.db.models import AuditLog

            async with container.session_factory() as session:
                rows = (
                    (
                        await session.execute(
                            select(AuditLog)
                            .where(AuditLog.resource_id.in_([ids["org_id"], created.json()["id"]]))
                            .order_by(AuditLog.created_at.asc())
                        )
                    )
                    .scalars()
                    .all()
                )
                return [row.action for row in rows]

        actions = asyncio.run(_actions())
        assert "org_plan.created" in actions
        assert "organization.plan.bound" in actions
        assert "organization.plan.unbound" in actions


async def test_org_plan_service_batch_apply_semantics(tmp_path: Path) -> None:
    settings = _settings(tmp_path)
    await upgrade_database(settings)
    engine = create_engine(settings)
    session_factory = create_session_factory(engine)
    try:
        async with session_factory() as session:
            session.add(Organization(id="org-batch", name="Batch", slug="slug-batch"))
            await session.flush()
            for index in range(2):
                session.add(
                    Project(
                        id=f"prj-batch-{index}",
                        name=f"Batch {index}",
                        slug=f"slug-batch-{index}",
                        organization_id="org-batch",
                    )
                )
            from app.db.models import OrgPlan

            plan = OrgPlan(
                id="plan-batch",
                slug="batch",
                name="Batch Plan",
                concurrent_execution_limit=11,
                embedding_overage_policy="soft",
            )
            session.add(plan)
            await session.flush()
            org = await session.get(Organization, "org-batch")
            assert org is not None
            updated = await OrgPlanService(session).apply_to_org(
                org, plan, assigned_by="tester", now=_NOW
            )
            await session.commit()
            assert updated == 2

        async with session_factory() as session:
            org = await session.get(Organization, "org-batch")
            assert org is not None
            assert org.plan_id == "plan-batch"
            assert org.plan_assigned_by == "tester"
            quotas = {
                row.project_id: row
                for row in (await session.execute(select(ProjectQuota))).scalars().all()
            }
            for index in range(2):
                quota = quotas[f"prj-batch-{index}"]
                assert quota.concurrent_execution_limit == 11
                assert quota.embedding_overage_policy == "soft"
                assert quota.model_cost_overage_policy == "hard"
                assert quota.storage_bytes_limit is None

            service = OrgPlanService(session)
            await service.unbind_from_org(org)
            await session.commit()
            # Unbind keeps the last applied limits.
            assert org.plan_id is None
            assert quotas["prj-batch-0"].concurrent_execution_limit == 11
    finally:
        await engine.dispose()
