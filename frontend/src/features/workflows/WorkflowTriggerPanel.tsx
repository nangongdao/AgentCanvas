import { useCallback, useEffect, useMemo, useState } from "react";
import { createPortal } from "react-dom";
import {
  CalendarClock,
  Check,
  Clipboard,
  Clock3,
  Code2,
  KeyRound,
  Power,
  RefreshCw,
  RotateCw,
  Send,
  ShieldCheck,
  Trash2,
  Webhook,
  X,
  Zap,
} from "lucide-react";

import { ApiError } from "@/api/client";
import {
  createCallback,
  createSchedule,
  createWebhook,
  disableCallback,
  disableSchedule,
  disableWebhook,
  disableWorkflowApi,
  getCallback,
  getWebhook,
  getWorkflowApi,
  listCallbackDeliveries,
  listSchedules,
  listServiceAccounts,
  publishWorkflowApi,
  rotateWebhook,
  rotateWorkflowApi,
  updateCallback,
  updateSchedule,
  type CallbackEventType,
  type ServiceAccountDTO,
  type WebhookTriggerDTO,
  type WorkflowApiDTO,
  type WorkflowCallbackDTO,
  type WorkflowCallbackDeliveryDTO,
  type WorkflowScheduleDTO,
} from "@/api/endpoints/triggers";
import { useDialogFocus } from "@/components/useDialogFocus";
import { cn } from "@/utils/cn";

type TriggerTab = "webhook" | "schedule" | "api" | "callback";

interface Props {
  workflowId: string | null;
  canEdit: boolean;
  canAdmin: boolean;
  currentVersion: number;
  onNotify: (message: string) => void;
}

const TABS: Array<{ id: TriggerTab; label: string; icon: typeof Webhook }> = [
  { id: "webhook", label: "Webhook", icon: Webhook },
  { id: "schedule", label: "计划", icon: Clock3 },
  { id: "api", label: "API", icon: Code2 },
  { id: "callback", label: "回调", icon: Send },
];

const CALLBACK_EVENTS: Array<{ id: CallbackEventType; label: string }> = [
  { id: "workflow_finished", label: "成功" },
  { id: "workflow_failed", label: "失败" },
  { id: "workflow_cancelled", label: "取消" },
  { id: "dead_letter", label: "Dead letter" },
  { id: "cost_alert", label: "成本告警" },
  { id: "quota_alert", label: "配额告警" },
];

function errorMessage(error: unknown): string {
  if (error instanceof ApiError) {
    return typeof error.detail === "string"
      ? error.detail
      : JSON.stringify(error.detail);
  }
  return error instanceof Error ? error.message : String(error);
}

function formatTime(value?: string | null): string {
  if (!value) return "暂无";
  const date = new Date(value);
  return Number.isNaN(date.getTime())
    ? "暂无"
    : date.toLocaleString("zh-CN", { hour12: false });
}

function Status({ value }: { value: string }) {
  const active = value === "active" || value === "delivered";
  return (
    <span
      className={cn(
        "inline-flex h-5 items-center gap-1 rounded-sm border px-1.5 font-mono text-[9px] uppercase",
        active
          ? "border-ok/35 bg-ok/10 text-ok"
          : value === "dead_letter" || value === "error"
            ? "border-bad/35 bg-bad/10 text-bad"
            : "border-line bg-void/60 text-ghost",
      )}
    >
      <span className={cn("h-1 w-1 rounded-full", active ? "bg-ok" : "bg-ghost/60")} />
      {value}
    </span>
  );
}

function CopyValue({ label, value }: { label: string; value: string }) {
  const [copied, setCopied] = useState(false);
  const copy = async () => {
    await navigator.clipboard.writeText(value);
    setCopied(true);
    window.setTimeout(() => setCopied(false), 1400);
  };
  return (
    <div className="border-b border-line/70 px-3 py-2 last:border-b-0">
      <div className="mb-1 flex items-center justify-between">
        <span className="font-mono text-[9px] uppercase text-warn">{label}</span>
        <button
          type="button"
          onClick={() => void copy()}
          className="flex h-6 w-6 items-center justify-center rounded-sm text-ghost hover:bg-line hover:text-ice"
          title={`复制${label}`}
        >
          {copied ? <Check size={12} className="text-ok" /> : <Clipboard size={12} />}
        </button>
      </div>
      <code className="block break-all font-mono text-[10px] leading-4 text-ice">{value}</code>
    </div>
  );
}

function ActionButton({
  children,
  onClick,
  disabled,
  tone = "default",
}: {
  children: React.ReactNode;
  onClick: () => void;
  disabled?: boolean;
  tone?: "default" | "primary" | "danger";
}) {
  return (
    <button
      type="button"
      disabled={disabled}
      onClick={onClick}
      className={cn(
        "flex h-8 items-center justify-center gap-1.5 rounded-md border px-3 text-xs transition disabled:opacity-35",
        tone === "primary"
          ? "border-pulse/40 bg-pulse/10 text-pulse hover:bg-pulse/20"
          : tone === "danger"
            ? "border-bad/30 bg-bad/5 text-bad hover:bg-bad/15"
            : "border-line bg-ink text-ice hover:border-ghost/50 hover:bg-line/60",
      )}
    >
      {children}
    </button>
  );
}

export function WorkflowTriggerPanel(props: Props) {
  const [open, setOpen] = useState(false);
  const [tab, setTab] = useState<TriggerTab>("webhook");
  const [loading, setLoading] = useState(false);
  const [action, setAction] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [webhook, setWebhook] = useState<WebhookTriggerDTO | null>(null);
  const [schedules, setSchedules] = useState<WorkflowScheduleDTO[]>([]);
  const [workflowApi, setWorkflowApi] = useState<WorkflowApiDTO | null>(null);
  const [callback, setCallback] = useState<WorkflowCallbackDTO | null>(null);
  const [deliveries, setDeliveries] = useState<WorkflowCallbackDeliveryDTO[]>([]);
  const [accounts, setAccounts] = useState<ServiceAccountDTO[]>([]);
  const [issued, setIssued] = useState<Array<{ label: string; value: string }>>([]);
  const [webhookAllowlist, setWebhookAllowlist] = useState("");
  const [scheduleName, setScheduleName] = useState("daily-run");
  const [scheduleCron, setScheduleCron] = useState("0 9 * * *");
  const [scheduleTimezone, setScheduleTimezone] = useState("Asia/Shanghai");
  const [scheduleInputs, setScheduleInputs] = useState("{}");
  const [serviceAccountId, setServiceAccountId] = useState("");
  const [callbackUrl, setCallbackUrl] = useState("");
  const [callbackEvents, setCallbackEvents] = useState<CallbackEventType[]>([
    "workflow_finished",
    "workflow_failed",
    "dead_letter",
  ]);
  const dialogRef = useDialogFocus<HTMLElement>({
    open,
    onClose: () => setOpen(false),
  });

  const load = useCallback(async () => {
    if (!props.workflowId) return;
    setLoading(true);
    setError(null);
    try {
      const [nextWebhook, nextSchedules, nextApi, nextCallback, nextDeliveries] =
        await Promise.all([
          getWebhook(props.workflowId),
          listSchedules(props.workflowId),
          getWorkflowApi(props.workflowId),
          getCallback(props.workflowId),
          listCallbackDeliveries(props.workflowId).catch((loadError) => {
            if (loadError instanceof ApiError && loadError.status === 404) return [];
            throw loadError;
          }),
        ]);
      setWebhook(nextWebhook);
      setWebhookAllowlist(nextWebhook?.ip_allowlist?.join("\n") ?? "");
      setSchedules(nextSchedules);
      setWorkflowApi(nextApi);
      setCallback(nextCallback);
      setCallbackUrl(nextCallback?.url ?? "");
      setCallbackEvents(
        (nextCallback?.event_types as CallbackEventType[] | undefined) ?? [
          "workflow_finished",
          "workflow_failed",
          "dead_letter",
        ],
      );
      setDeliveries(nextDeliveries);
      if (props.canAdmin) {
        const nextAccounts = (await listServiceAccounts()).filter(
          (account) => account.status === "active" && account.role !== "viewer",
        );
        setAccounts(nextAccounts);
        setServiceAccountId((current) => current || nextAccounts[0]?.id || "");
      }
    } catch (loadError) {
      setError(errorMessage(loadError));
    } finally {
      setLoading(false);
    }
  }, [props.canAdmin, props.workflowId]);

  useEffect(() => {
    if (open) void load();
  }, [load, open, props.currentVersion]);

  useEffect(() => {
    if (!open) setIssued([]);
  }, [open]);

  const runAction = async (name: string, operation: () => Promise<void>) => {
    setAction(name);
    setError(null);
    try {
      await operation();
      await load();
    } catch (operationError) {
      setError(errorMessage(operationError));
    } finally {
      setAction(null);
    }
  };

  const ipAllowlist = () =>
    webhookAllowlist
      .split(/[\n,]/)
      .map((value) => value.trim())
      .filter(Boolean);

  const createOrRotateWebhook = (rotate: boolean) => {
    if (!props.workflowId) return;
    void runAction(rotate ? "webhook-rotate" : "webhook-create", async () => {
      const issue = rotate
        ? await rotateWebhook(props.workflowId!, { ip_allowlist: ipAllowlist() })
        : await createWebhook(props.workflowId!, { ip_allowlist: ipAllowlist() });
      setIssued([
        { label: "Webhook token", value: issue.token },
        { label: "Signing secret", value: issue.secret },
      ]);
      props.onNotify(rotate ? "Webhook 凭据已轮换" : "Webhook 已创建");
    });
  };

  const addSchedule = () => {
    if (!props.workflowId) return;
    void runAction("schedule-create", async () => {
      let inputs: Record<string, unknown>;
      try {
        const parsed = JSON.parse(scheduleInputs) as unknown;
        if (!parsed || Array.isArray(parsed) || typeof parsed !== "object") throw new Error();
        inputs = parsed as Record<string, unknown>;
      } catch {
        throw new Error("计划输入必须是 JSON 对象");
      }
      await createSchedule(props.workflowId!, {
        name: scheduleName,
        cron_expression: scheduleCron,
        timezone: scheduleTimezone,
        inputs,
        enabled: true,
        misfire_policy: "skip",
        failure_policy: "retry",
        retry_delay_seconds: 60,
      });
      props.onNotify("定时计划已创建");
    });
  };

  const publishOrRotateApi = (rotate: boolean) => {
    if (!props.workflowId) return;
    void runAction(rotate ? "api-rotate" : "api-publish", async () => {
      const issue = rotate
        ? await rotateWorkflowApi(props.workflowId!, workflowApi?.service_account_id)
        : await publishWorkflowApi(props.workflowId!, serviceAccountId);
      setIssued([
        { label: "API token", value: issue.token },
        { label: "Endpoint", value: issue.endpoint },
      ]);
      props.onNotify(rotate ? "API key 已轮换" : "工作流 API 已发布");
    });
  };

  const saveCallback = (rotateSecret = false) => {
    if (!props.workflowId) return;
    void runAction(callback ? "callback-update" : "callback-create", async () => {
      const issue = callback
        ? await updateCallback(props.workflowId!, {
            url: callbackUrl,
            event_types: callbackEvents,
            status: "active",
            ...(rotateSecret ? { rotate_secret: true } : {}),
          })
        : await createCallback(props.workflowId!, {
            url: callbackUrl,
            event_types: callbackEvents,
          });
      if (issue.secret) setIssued([{ label: "Callback secret", value: issue.secret }]);
      props.onNotify(callback ? "事件回调已更新" : "事件回调已创建");
    });
  };

  const configuredCount = useMemo(
    () => Number(Boolean(webhook)) + Number(schedules.length > 0) + Number(Boolean(workflowApi)) + Number(Boolean(callback)),
    [callback, schedules.length, webhook, workflowApi],
  );

  if (!props.workflowId) return null;

  return (
    <>
      <button
        type="button"
        onClick={() => setOpen(true)}
        className="flex h-8 items-center gap-1.5 rounded-md border border-line bg-ink/80 px-2.5 text-xs text-ice transition hover:border-warn/40 hover:bg-warn/5 hover:text-warn"
        title="触发器"
      >
        <Zap size={13} />
        <span className="hidden xl:inline">触发器</span>
      </button>

      {open &&
        createPortal(
          <div className="fixed inset-0 z-50 bg-void/75 backdrop-blur-xs">
            <button
              type="button"
              aria-label="关闭触发器"
              className="absolute inset-0 h-full w-full cursor-default"
              onClick={() => setOpen(false)}
            />
            <section
              ref={dialogRef}
              tabIndex={-1}
              role="dialog"
              aria-modal="true"
              aria-labelledby="workflow-trigger-title"
              className="glass absolute right-0 flex h-full w-[min(620px,100vw)] animate-slide-in flex-col border-l border-line shadow-card"
            >
              <header className="flex min-h-16 items-center gap-3 border-b border-line px-4">
                <span className="flex h-9 w-9 items-center justify-center rounded-md border border-warn/30 bg-warn/10 text-warn">
                  <Zap size={17} />
                </span>
                <div className="min-w-0">
                  <h2 id="workflow-trigger-title" className="text-sm font-semibold text-ice">
                    触发中心
                  </h2>
                  <p className="font-mono text-[9px] uppercase text-ghost">
                    {configuredCount} configured · workflow v{props.currentVersion}
                  </p>
                </div>
                <button
                  type="button"
                  onClick={() => void load()}
                  className="ml-auto flex h-8 w-8 items-center justify-center rounded-md text-ghost hover:bg-line hover:text-ice"
                  title="刷新"
                >
                  <RefreshCw size={14} className={cn(loading && "animate-spin")} />
                </button>
                <button
                  type="button"
                  onClick={() => setOpen(false)}
                  className="flex h-8 w-8 items-center justify-center rounded-md text-ghost hover:bg-line hover:text-ice"
                  title="关闭"
                >
                  <X size={15} />
                </button>
              </header>

              <div className="grid grid-cols-4 border-b border-line" role="tablist">
                {TABS.map((item) => {
                  const Icon = item.icon;
                  return (
                    <button
                      key={item.id}
                      type="button"
                      role="tab"
                      aria-selected={tab === item.id}
                      onClick={() => {
                        setTab(item.id);
                        setIssued([]);
                        setError(null);
                      }}
                      className={cn(
                        "flex h-11 items-center justify-center gap-1.5 border-r border-line text-xs last:border-r-0",
                        tab === item.id
                          ? "bg-pulse/8 text-pulse shadow-[inset_0_-2px_0_#22d3ee]"
                          : "text-ghost hover:bg-line/40 hover:text-ice",
                      )}
                    >
                      <Icon size={13} /> {item.label}
                    </button>
                  );
                })}
              </div>

              {error && (
                <p className="border-b border-bad/30 bg-bad/10 px-4 py-2 text-xs text-bad">
                  {error}
                </p>
              )}
              {issued.length > 0 && (
                <div className="border-b border-warn/30 bg-warn/5">
                  <div className="flex items-center gap-2 border-b border-warn/20 px-3 py-2 text-[10px] text-warn">
                    <ShieldCheck size={12} /> 一次性凭据
                    <button
                      type="button"
                      onClick={() => setIssued([])}
                      className="ml-auto text-warn/70 hover:text-warn"
                      title="关闭凭据"
                    >
                      <X size={12} />
                    </button>
                  </div>
                  {issued.map((item) => <CopyValue key={item.label} {...item} />)}
                </div>
              )}

              <div className="min-h-0 flex-1 overflow-y-auto p-4">
                {tab === "webhook" && (
                  <WebhookSection
                    value={webhook}
                    allowlist={webhookAllowlist}
                    canEdit={props.canEdit}
                    action={action}
                    onAllowlist={setWebhookAllowlist}
                    onCreate={() => createOrRotateWebhook(false)}
                    onRotate={() => createOrRotateWebhook(true)}
                    onDisable={() => {
                      if (!props.workflowId || !window.confirm("停用此 Webhook？")) return;
                      void runAction("webhook-disable", async () => {
                        await disableWebhook(props.workflowId!);
                        props.onNotify("Webhook 已停用");
                      });
                    }}
                  />
                )}
                {tab === "schedule" && (
                  <ScheduleSection
                    rows={schedules}
                    canEdit={props.canEdit}
                    action={action}
                    name={scheduleName}
                    cron={scheduleCron}
                    timezone={scheduleTimezone}
                    inputs={scheduleInputs}
                    onName={setScheduleName}
                    onCron={setScheduleCron}
                    onTimezone={setScheduleTimezone}
                    onInputs={setScheduleInputs}
                    onCreate={addSchedule}
                    onToggle={(row) => {
                      if (!props.workflowId) return;
                      void runAction(`schedule:${row.id}`, async () => {
                        if (row.status === "active") {
                          await disableSchedule(props.workflowId!, row.id);
                        } else {
                          await updateSchedule(props.workflowId!, row.id, { enabled: true });
                        }
                      });
                    }}
                  />
                )}
                {tab === "api" && (
                  <ApiSection
                    value={workflowApi}
                    accounts={accounts}
                    serviceAccountId={serviceAccountId}
                    canEdit={props.canEdit}
                    canAdmin={props.canAdmin}
                    action={action}
                    onServiceAccount={setServiceAccountId}
                    onPublish={() => publishOrRotateApi(false)}
                    onRotate={() => publishOrRotateApi(true)}
                    onDisable={() => {
                      if (!props.workflowId || !window.confirm("停用工作流 API？")) return;
                      void runAction("api-disable", async () => {
                        await disableWorkflowApi(props.workflowId!);
                        props.onNotify("工作流 API 已停用");
                      });
                    }}
                  />
                )}
                {tab === "callback" && (
                  <CallbackSection
                    value={callback}
                    deliveries={deliveries}
                    url={callbackUrl}
                    events={callbackEvents}
                    canEdit={props.canEdit}
                    action={action}
                    onUrl={setCallbackUrl}
                    onEvents={setCallbackEvents}
                    onSave={() => saveCallback(false)}
                    onRotate={() => saveCallback(true)}
                    onDisable={() => {
                      if (!props.workflowId || !window.confirm("停用事件回调？")) return;
                      void runAction("callback-disable", async () => {
                        await disableCallback(props.workflowId!);
                        props.onNotify("事件回调已停用");
                      });
                    }}
                  />
                )}
              </div>
            </section>
          </div>,
          document.body,
        )}
    </>
  );
}

function WebhookSection(props: {
  value: WebhookTriggerDTO | null;
  allowlist: string;
  canEdit: boolean;
  action: string | null;
  onAllowlist: (value: string) => void;
  onCreate: () => void;
  onRotate: () => void;
  onDisable: () => void;
}) {
  return (
    <div className="space-y-4">
      <div className="flex items-center border-b border-line pb-3">
        <div>
          <p className="text-xs font-medium text-ice">Inbound Webhook</p>
          <p className="mt-1 font-mono text-[10px] text-ghost">
            {props.value ? `token ${props.value.token_prefix}… · v${props.value.published_version_number}` : "not configured"}
          </p>
        </div>
        <span className="ml-auto">{props.value && <Status value={props.value.status} />}</span>
      </div>
      <label className="block text-[10px] uppercase text-ghost">
        IP allowlist
        <textarea
          value={props.allowlist}
          onChange={(event) => props.onAllowlist(event.target.value)}
          disabled={!props.canEdit}
          rows={4}
          className="field-input mt-1 resize-none font-mono"
          placeholder="203.0.113.0/24"
        />
      </label>
      {props.value?.last_triggered_at && (
        <p className="font-mono text-[10px] text-ghost">最近触发 {formatTime(props.value.last_triggered_at)}</p>
      )}
      {props.canEdit && (
        <div className="flex flex-wrap gap-2">
          {!props.value ? (
            <ActionButton onClick={props.onCreate} disabled={props.action !== null} tone="primary">
              <Webhook size={13} /> 创建
            </ActionButton>
          ) : (
            <>
              <ActionButton onClick={props.onRotate} disabled={props.action !== null}>
                <RotateCw size={13} /> 轮换凭据
              </ActionButton>
              <ActionButton onClick={props.onDisable} disabled={props.action !== null} tone="danger">
                <Power size={13} /> 停用
              </ActionButton>
            </>
          )}
        </div>
      )}
    </div>
  );
}

function ScheduleSection(props: {
  rows: WorkflowScheduleDTO[];
  canEdit: boolean;
  action: string | null;
  name: string;
  cron: string;
  timezone: string;
  inputs: string;
  onName: (value: string) => void;
  onCron: (value: string) => void;
  onTimezone: (value: string) => void;
  onInputs: (value: string) => void;
  onCreate: () => void;
  onToggle: (row: WorkflowScheduleDTO) => void;
}) {
  return (
    <div className="space-y-4">
      {props.canEdit && (
        <div className="grid grid-cols-2 gap-2 border-b border-line pb-4">
          <input className="field-input" value={props.name} onChange={(e) => props.onName(e.target.value)} aria-label="计划名称" placeholder="计划名称" />
          <input className="field-input font-mono" value={props.cron} onChange={(e) => props.onCron(e.target.value)} aria-label="Cron" placeholder="0 9 * * *" />
          <input className="field-input" value={props.timezone} onChange={(e) => props.onTimezone(e.target.value)} aria-label="时区" placeholder="Asia/Shanghai" />
          <input className="field-input font-mono" value={props.inputs} onChange={(e) => props.onInputs(e.target.value)} aria-label="输入 JSON" placeholder="{}" />
          <div className="col-span-2">
            <ActionButton onClick={props.onCreate} disabled={props.action !== null} tone="primary">
              <CalendarClock size={13} /> 新建计划
            </ActionButton>
          </div>
        </div>
      )}
      <ul className="divide-y divide-line border-y border-line">
        {props.rows.map((row) => (
          <li key={row.id} className="flex items-start gap-3 py-3">
            <Clock3 size={14} className="mt-0.5 text-volt" />
            <div className="min-w-0 flex-1">
              <div className="flex items-center gap-2">
                <span className="truncate text-xs text-ice">{row.name}</span>
                <Status value={row.status} />
              </div>
              <p className="mt-1 font-mono text-[10px] text-ghost">{row.cron_expression} · {row.timezone}</p>
              <p className="mt-1 text-[10px] text-ghost/70">下次 {formatTime(row.next_run_at)} · 最近 {formatTime(row.last_run_at)}</p>
              {row.last_error && <p className="mt-1 truncate text-[10px] text-bad" title={row.last_error}>{row.last_error}</p>}
            </div>
            {props.canEdit && (
              <button type="button" onClick={() => props.onToggle(row)} disabled={props.action !== null} className="flex h-7 w-7 items-center justify-center rounded-md text-ghost hover:bg-line hover:text-ice disabled:opacity-35" title={row.status === "active" ? "停用" : "启用"}>
                <Power size={13} />
              </button>
            )}
          </li>
        ))}
        {props.rows.length === 0 && <li className="py-10 text-center font-mono text-[10px] text-ghost/55">NO SCHEDULES</li>}
      </ul>
    </div>
  );
}

function ApiSection(props: {
  value: WorkflowApiDTO | null;
  accounts: ServiceAccountDTO[];
  serviceAccountId: string;
  canEdit: boolean;
  canAdmin: boolean;
  action: string | null;
  onServiceAccount: (value: string) => void;
  onPublish: () => void;
  onRotate: () => void;
  onDisable: () => void;
}) {
  return (
    <div className="space-y-4">
      <div className="flex items-start border-b border-line pb-3">
        <div className="min-w-0 flex-1">
          <p className="text-xs font-medium text-ice">Workflow API</p>
          <p className="mt-1 break-all font-mono text-[10px] text-ghost">{props.value?.endpoint ?? "not published"}</p>
          {props.value && <p className="mt-1 font-mono text-[10px] text-ghost/70">key {props.value.token_prefix}… · v{props.value.published_version_number}</p>}
        </div>
        {props.value && <Status value={props.value.status} />}
      </div>
      {!props.value && (
        <label className="block text-[10px] uppercase text-ghost">
          Service account
          {props.canAdmin ? (
            <select className="field-input mt-1" value={props.serviceAccountId} onChange={(e) => props.onServiceAccount(e.target.value)}>
              {props.accounts.map((account) => <option key={account.id} value={account.id}>{account.name} · {account.role}</option>)}
            </select>
          ) : (
            <input className="field-input mt-1 font-mono" value={props.serviceAccountId} onChange={(e) => props.onServiceAccount(e.target.value)} placeholder="service account id" />
          )}
        </label>
      )}
      {props.value?.last_triggered_at && <p className="font-mono text-[10px] text-ghost">最近调用 {formatTime(props.value.last_triggered_at)}</p>}
      {props.canEdit && (
        <div className="flex flex-wrap gap-2">
          {!props.value ? (
            <ActionButton onClick={props.onPublish} disabled={!props.serviceAccountId || props.action !== null} tone="primary"><KeyRound size={13} /> 发布 API</ActionButton>
          ) : (
            <>
              <ActionButton onClick={props.onRotate} disabled={props.action !== null}><RotateCw size={13} /> 轮换 key</ActionButton>
              <ActionButton onClick={props.onDisable} disabled={props.action !== null} tone="danger"><Power size={13} /> 停用</ActionButton>
            </>
          )}
        </div>
      )}
    </div>
  );
}

function CallbackSection(props: {
  value: WorkflowCallbackDTO | null;
  deliveries: WorkflowCallbackDeliveryDTO[];
  url: string;
  events: CallbackEventType[];
  canEdit: boolean;
  action: string | null;
  onUrl: (value: string) => void;
  onEvents: (value: CallbackEventType[]) => void;
  onSave: () => void;
  onRotate: () => void;
  onDisable: () => void;
}) {
  const toggle = (event: CallbackEventType) => {
    props.onEvents(props.events.includes(event) ? props.events.filter((value) => value !== event) : [...props.events, event]);
  };
  return (
    <div className="space-y-4">
      <div className="flex items-center border-b border-line pb-3">
        <div>
          <p className="text-xs font-medium text-ice">Outbound Callback</p>
          <p className="mt-1 font-mono text-[10px] text-ghost">{props.value ? `${props.value.max_attempts} attempts · ${props.value.timeout_seconds}s timeout` : "not configured"}</p>
        </div>
        <span className="ml-auto">{props.value && <Status value={props.value.status} />}</span>
      </div>
      <label className="block text-[10px] uppercase text-ghost">
        Destination URL
        <input className="field-input mt-1 font-mono" value={props.url} onChange={(e) => props.onUrl(e.target.value)} disabled={!props.canEdit} placeholder="https://example.com/events" />
      </label>
      <fieldset disabled={!props.canEdit} className="grid grid-cols-2 gap-2 sm:grid-cols-3">
        {CALLBACK_EVENTS.map((event) => (
          <label key={event.id} className={cn("flex h-8 items-center gap-2 rounded-md border px-2 text-[10px]", props.events.includes(event.id) ? "border-pulse/35 bg-pulse/8 text-pulse" : "border-line bg-void/55 text-ghost")}>
            <input type="checkbox" checked={props.events.includes(event.id)} onChange={() => toggle(event.id)} className="accent-cyan-400" />
            {event.label}
          </label>
        ))}
      </fieldset>
      {props.canEdit && (
        <div className="flex flex-wrap gap-2">
          <ActionButton onClick={props.onSave} disabled={!props.url || props.events.length === 0 || props.action !== null} tone="primary"><Send size={13} /> {props.value ? "保存" : "创建"}</ActionButton>
          {props.value && <ActionButton onClick={props.onRotate} disabled={props.action !== null}><RotateCw size={13} /> 轮换 secret</ActionButton>}
          {props.value && <ActionButton onClick={props.onDisable} disabled={props.action !== null} tone="danger"><Trash2 size={13} /> 停用</ActionButton>}
        </div>
      )}
      <div className="border-t border-line pt-3">
        <div className="mb-2 flex items-center gap-2 font-mono text-[9px] uppercase text-ghost"><Clock3 size={11} /> Recent deliveries</div>
        <ul className="divide-y divide-line/70 border-y border-line">
          {props.deliveries.slice(0, 12).map((delivery) => (
            <li key={delivery.id} className="flex items-center gap-2 py-2 text-[10px]">
              <Status value={delivery.status} />
              <span className="min-w-0 flex-1 truncate font-mono text-ice/80">{delivery.event_type}</span>
              <span className="font-mono text-ghost/65">#{delivery.attempt_count}</span>
              <span className="font-mono text-ghost/55">{formatTime(delivery.delivered_at ?? delivery.created_at)}</span>
            </li>
          ))}
          {props.deliveries.length === 0 && <li className="py-8 text-center font-mono text-[10px] text-ghost/55">NO DELIVERIES</li>}
        </ul>
      </div>
    </div>
  );
}
