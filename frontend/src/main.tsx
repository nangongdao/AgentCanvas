import React, { Suspense, lazy } from "react";
import ReactDOM from "react-dom/client";
import {
  Navigate,
  RouterProvider,
  createBrowserRouter,
  useLocation,
} from "react-router-dom";

import { AuthProvider } from "@/features/auth/AuthProvider";
import { PlatformShell } from "@/features/shell/PlatformShell";
import "./index.css";

const App = lazy(() => import("./App"));
const ChatPage = lazy(() =>
  import("@/features/chat/ChatPage").then((m) => ({ default: m.ChatPage })),
);
const KnowledgePage = lazy(() =>
  import("@/features/knowledge/KnowledgePage").then((m) => ({
    default: m.KnowledgePage,
  })),
);
const ModelsPage = lazy(() =>
  import("@/features/models/ModelsPage").then((m) => ({ default: m.ModelsPage })),
);
const McpCatalogPage = lazy(() =>
  import("@/features/mcp/McpCatalogPage").then((m) => ({ default: m.McpCatalogPage })),
);
const EvaluationPage = lazy(() =>
  import("@/features/evaluations/EvaluationPage").then((m) => ({
    default: m.EvaluationPage,
  })),
);
const CostGovernancePage = lazy(() =>
  import("@/features/cost/CostGovernancePage").then((m) => ({
    default: m.CostGovernancePage,
  })),
);
const MembersPage = lazy(() =>
  import("@/features/members/MembersPage").then((m) => ({
    default: m.MembersPage,
  })),
);
const InvitationAcceptPage = lazy(() =>
  import("@/features/members/InvitationAcceptPage").then((m) => ({
    default: m.InvitationAcceptPage,
  })),
);
const PlatformAdminPage = lazy(() =>
  import("@/features/admin/PlatformAdminPage").then((m) => ({
    default: m.PlatformAdminPage,
  })),
);
const AuditLogsPage = lazy(() =>
  import("@/features/audit/AuditLogsPage").then((m) => ({
    default: m.AuditLogsPage,
  })),
);
const AppsPage = lazy(() =>
  import("@/features/apps/AppsPage").then((m) => ({ default: m.AppsPage })),
);
const AppRuntimePage = lazy(() =>
  import("@/features/apps/AppRuntimePage").then((m) => ({
    default: m.AppRuntimePage,
  })),
);
const ProjectQuotasPage = lazy(() =>
  import("@/features/quotas/ProjectQuotasPage").then((m) => ({
    default: m.ProjectQuotasPage,
  })),
);
const OverviewPage = lazy(() =>
  import("@/features/overview/OverviewPage").then((m) => ({
    default: m.OverviewPage,
  })),
);
const MarketplacePage = lazy(() =>
  import("@/features/marketplace/MarketplacePage").then((m) => ({
    default: m.MarketplacePage,
  })),
);

function PageLoader({ children }: { children: React.ReactNode }) {
  return (
    <Suspense
      fallback={
        <div className="flex h-full w-full items-center justify-center bg-void text-ghost">
          <span className="animate-pulse">加载中…</span>
        </div>
      }
    >
      {children}
    </Suspense>
  );
}

function LegacyRedirect({ to }: { to: string }) {
  const location = useLocation();
  return <Navigate to={`${to}${location.search}${location.hash}`} replace />;
}

const router = createBrowserRouter([
  {
    element: <PlatformShell />,
    children: [
      {
        index: true,
        element: (
          <PageLoader>
            <OverviewPage />
          </PageLoader>
        ),
      },
      {
        path: "workflows/:workflowId",
        element: (
          <PageLoader>
            <App />
          </PageLoader>
        ),
      },
      {
        path: "knowledge",
        element: (
          <PageLoader>
            <KnowledgePage />
          </PageLoader>
        ),
      },
      {
        path: "marketplace",
        element: (
          <PageLoader>
            <MarketplacePage />
          </PageLoader>
        ),
      },
      {
        path: "evaluations",
        element: (
          <PageLoader>
            <EvaluationPage />
          </PageLoader>
        ),
      },
      {
        path: "cost",
        element: (
          <PageLoader>
            <CostGovernancePage />
          </PageLoader>
        ),
      },
      {
        path: "apps",
        element: (
          <PageLoader>
            <AppsPage />
          </PageLoader>
        ),
      },
      {
        path: "chat",
        element: (
          <PageLoader>
            <ChatPage />
          </PageLoader>
        ),
      },
      {
        path: "settings/models",
        element: (
          <PageLoader>
            <ModelsPage />
          </PageLoader>
        ),
      },
      {
        path: "settings/mcp",
        element: (
          <PageLoader>
            <McpCatalogPage />
          </PageLoader>
        ),
      },
      {
        path: "settings/quotas",
        element: (
          <PageLoader>
            <ProjectQuotasPage />
          </PageLoader>
        ),
      },
      {
        path: "settings/audit",
        element: (
          <PageLoader>
            <AuditLogsPage />
          </PageLoader>
        ),
      },
      {
        path: "settings/platform",
        element: (
          <PageLoader>
            <PlatformAdminPage />
          </PageLoader>
        ),
      },
      {
        path: "settings/members",
        element: (
          <PageLoader>
            <MembersPage />
          </PageLoader>
        ),
      },
      {
        path: "invitations/accept",
        element: (
          <PageLoader>
            <InvitationAcceptPage />
          </PageLoader>
        ),
      },
    ],
  },
  {
    path: "/apps/p/:slug",
    element: (
      <PageLoader>
        <AppRuntimePage />
      </PageLoader>
    ),
  },
  { path: "/models", element: <LegacyRedirect to="/settings/models" /> },
  { path: "/mcp/catalog", element: <LegacyRedirect to="/settings/mcp" /> },
  { path: "/quotas", element: <LegacyRedirect to="/settings/quotas" /> },
  { path: "/audit", element: <LegacyRedirect to="/settings/audit" /> },
  { path: "*", element: <Navigate to="/" replace /> },
]);

ReactDOM.createRoot(document.getElementById("root")!).render(
  <React.StrictMode>
    <AuthProvider>
      <RouterProvider router={router} />
    </AuthProvider>
  </React.StrictMode>,
);
