"""One route registry feeding request guards and rate-limit middleware."""

from __future__ import annotations

from dataclasses import dataclass

from app.core.config import Settings
from app.core.ratelimit import RateLimitConfig, RateLimitRule
from app.core.request_policy import RequestPolicyConfig, RequestPolicyRule


@dataclass(frozen=True)
class OperationPolicy:
    name: str
    path_regex: str
    methods: frozenset[str]
    request: RequestPolicyConfig
    rate_limit: RateLimitConfig


def operation_policies(settings: Settings) -> tuple[OperationPolicy, ...]:
    body = settings.request_body_max_bytes
    window = settings.rate_limit_window_seconds
    return (
        OperationPolicy(
            "login",
            r"^/api/auth/(?:login|register|oidc/(?:start|callback))$",
            frozenset({"GET", "POST"}),
            RequestPolicyConfig(
                max_body_bytes=body,
                max_concurrent=settings.login_max_concurrent,
                timeout_seconds=settings.login_timeout_seconds,
            ),
            RateLimitConfig(settings.rate_limit_login_requests, window),
        ),
        OperationPolicy(
            "auth_refresh",
            r"^/api/auth/refresh$",
            frozenset({"POST"}),
            RequestPolicyConfig(
                max_body_bytes=body,
                max_concurrent=settings.login_max_concurrent,
                timeout_seconds=settings.login_timeout_seconds,
            ),
            RateLimitConfig(settings.rate_limit_login_requests, window),
        ),
        OperationPolicy(
            "execution_start",
            r"^/api/(?:workflows/[^/]+/run|executions/[^/]+/rerun)$",
            frozenset({"POST"}),
            RequestPolicyConfig(
                max_body_bytes=body,
                max_concurrent=settings.execution_max_concurrent,
                timeout_seconds=settings.execution_request_timeout_seconds,
            ),
            RateLimitConfig(settings.rate_limit_execution_requests, window),
        ),
        OperationPolicy(
            "webhook_trigger",
            r"^/api/hooks/[^/]+/[^/]+$",
            frozenset({"POST"}),
            RequestPolicyConfig(
                max_body_bytes=body,
                max_concurrent=settings.execution_max_concurrent,
                timeout_seconds=settings.execution_request_timeout_seconds,
            ),
            RateLimitConfig(settings.rate_limit_webhook_requests, window),
        ),
        OperationPolicy(
            "workflow_api",
            r"^/api/workflow-apis/[^/]+/execute$",
            frozenset({"POST"}),
            RequestPolicyConfig(
                max_body_bytes=body,
                max_concurrent=settings.execution_max_concurrent,
                timeout_seconds=settings.execution_request_timeout_seconds,
            ),
            RateLimitConfig(settings.rate_limit_workflow_api_requests, window),
        ),
        OperationPolicy(
            "chat_send",
            r"^/api/chat/sessions/[^/]+/send$",
            frozenset({"POST"}),
            RequestPolicyConfig(
                max_body_bytes=body,
                max_concurrent=settings.chat_max_concurrent,
            ),
            RateLimitConfig(settings.rate_limit_chat_requests, window),
        ),
        # C8-2: public app runtime is an unauthenticated attack surface. Give it
        # its own request-body cap, concurrency guard, and a strict per-client
        # bucket so session-churn or send spam cannot drain the shared default.
        # The per-client bucket is decoupled from the per-session send budget
        # (C3-5) so the two limits cannot starve each other.
        # The copilot is an editor-triggered LLM call, so it shares the
        # interactive chat budget rather than the execution budget: a burst of
        # drafting must not be able to starve interactive chat, or vice versa.
        OperationPolicy(
            "workflow_copilot",
            r"^/api/workflows/copilot/draft$",
            frozenset({"POST"}),
            RequestPolicyConfig(
                max_body_bytes=body,
                max_concurrent=settings.chat_max_concurrent,
            ),
            RateLimitConfig(settings.rate_limit_chat_requests, window),
        ),
        OperationPolicy(
            "app_runtime_session",
            r"^/api/apps/p/[^/]+/sessions$",
            frozenset({"POST"}),
            RequestPolicyConfig(
                max_body_bytes=settings.request_body_max_bytes,
                max_concurrent=settings.app_runtime_max_concurrent,
                timeout_seconds=settings.app_runtime_timeout_seconds,
            ),
            RateLimitConfig(settings.rate_limit_app_runtime_send_requests, window),
        ),
        OperationPolicy(
            "app_runtime_send",
            r"^/api/apps/p/[^/]+/sessions/[^/]+/send$",
            frozenset({"POST"}),
            RequestPolicyConfig(
                max_body_bytes=settings.request_body_max_bytes,
                max_concurrent=settings.app_runtime_max_concurrent,
                timeout_seconds=settings.app_runtime_timeout_seconds,
            ),
            RateLimitConfig(settings.rate_limit_app_runtime_send_requests, window),
        ),
        OperationPolicy(
            "app_runtime_feedback",
            r"^/api/apps/p/[^/]+/sessions/[^/]+/messages/[^/]+/feedback$",
            frozenset({"POST"}),
            RequestPolicyConfig(
                max_body_bytes=settings.request_body_max_bytes,
                max_concurrent=settings.app_runtime_max_concurrent,
                timeout_seconds=settings.app_runtime_timeout_seconds,
            ),
            RateLimitConfig(settings.rate_limit_app_runtime_send_requests, window),
        ),
        OperationPolicy(
            "document_upload",
            r"^/api/knowledge-bases/[^/]+/documents$",
            frozenset({"POST"}),
            RequestPolicyConfig(
                max_body_bytes=settings.upload_request_max_bytes,
                max_concurrent=settings.upload_max_concurrent,
                timeout_seconds=settings.upload_timeout_seconds,
            ),
            RateLimitConfig(settings.rate_limit_upload_requests, window),
        ),
        OperationPolicy(
            "document_ingest",
            r"^/api/knowledge-bases/[^/]+/documents/[^/]+/ingest$",
            frozenset({"POST"}),
            RequestPolicyConfig(
                max_body_bytes=body,
                max_concurrent=settings.ingest_max_concurrent,
                timeout_seconds=settings.ingest_timeout_seconds,
            ),
            RateLimitConfig(settings.rate_limit_ingest_requests, window),
        ),
        OperationPolicy(
            "retrieval",
            r"^/api/knowledge-bases/[^/]+/retrieve$",
            frozenset({"POST"}),
            RequestPolicyConfig(
                max_body_bytes=body,
                max_concurrent=settings.retrieval_max_concurrent,
                timeout_seconds=settings.retrieval_timeout_seconds,
            ),
            RateLimitConfig(settings.rate_limit_retrieval_requests, window),
        ),
        OperationPolicy(
            "mcp_probe",
            r"^/api/mcp/servers/[^/]+/(?:health|test|tools)$",
            frozenset({"GET", "POST"}),
            RequestPolicyConfig(
                max_body_bytes=body,
                max_concurrent=settings.mcp_max_concurrent,
                timeout_seconds=settings.mcp_timeout_seconds,
            ),
            RateLimitConfig(settings.rate_limit_mcp_requests, window),
        ),
        OperationPolicy(
            "model_discovery",
            r"^/api/models/discover$",
            frozenset({"POST"}),
            RequestPolicyConfig(
                max_body_bytes=body,
                max_concurrent=settings.discovery_max_concurrent,
                timeout_seconds=settings.discovery_timeout_seconds,
            ),
            RateLimitConfig(settings.rate_limit_discovery_requests, window),
        ),
    )


def request_policy_rules(settings: Settings) -> tuple[RequestPolicyRule, ...]:
    return tuple(
        RequestPolicyRule.regex(
            policy.name,
            policy.path_regex,
            policy.request,
            methods=set(policy.methods),
        )
        for policy in operation_policies(settings)
    )


def rate_limit_rules(settings: Settings) -> tuple[RateLimitRule, ...]:
    return tuple(
        RateLimitRule.regex(
            policy.name,
            policy.path_regex,
            policy.rate_limit,
            methods=set(policy.methods),
        )
        for policy in operation_policies(settings)
    )
