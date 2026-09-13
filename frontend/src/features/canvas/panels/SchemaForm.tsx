import { useEffect, useMemo, useState } from "react";
import { Braces, Plus, Trash2 } from "lucide-react";

import type { JsonSchema } from "@/api/endpoints/meta";
import { cn } from "@/utils/cn";

const LABELS: Record<string, string> = {
  context_nodes: "RAG 上下文节点",
  input_schema: "输入字段",
  model_config_id: "模型配置",
  fallback_model_config_ids: "备用模型链",
  load_balance: "负载均衡",
  agent_mode: "Agent 模式",
  system_prompt: "系统提示词",
  user_prompt: "用户提示词",
  tools: "MCP 工具",
  max_tool_rounds: "最大工具轮次",
  params: "模型参数",
  workers: "工作节点",
  memory: "记忆",
  output: "输出",
  server_id: "MCP 服务",
  tool_name: "工具",
  arguments: "调用参数",
  timeout_seconds: "超时秒数",
  retry: "重试",
  max_attempts: "最大尝试次数",
  branches: "条件分支",
  default_branch: "默认分支",
  kb_id: "知识库",
  query: "检索查询",
  top_k: "返回数量",
  score_threshold: "分数阈值",
  output_format: "输出格式",
  title: "标题",
  instruction: "说明",
  form_schema: "审批表单",
  timeout_hours: "等待小时数",
  output_template: "输出模板",
  name: "名称",
  type: "类型",
  required: "必填",
  default: "默认值",
  id: "标识",
  label: "标签",
  group: "规则组",
  op: "组合方式",
  rules: "规则",
  left: "左值",
  operator: "操作符",
  right: "右值",
  enabled: "启用",
  window: "窗口大小",
  format: "格式",
  // HTTP Request node (C2-3)
  method: "HTTP 方法",
  url: "请求地址",
  headers: "请求头",
  body: "请求体",
  auth: "认证",
  token_ref: "令牌引用",
  header_name: "请求头名",
  value_prefix: "值前缀",
  username: "用户名",
  password_ref: "密码引用",
  expected_status: "预期状态码",
  response: "响应映射",
  extract_json: "解析 JSON",
  json_path: "JSON 路径",
  include_headers: "包含响应头",
  text_fallback: "文本回退",
  allow_private_network: "允许内网",
  retry_on_status: "重试状态码",
  // Code node (C2-2)
  language: "语言",
  source: "源代码",
  inputs: "输入变量",
  memory_limit_mb: "内存上限 (MB)",
  process_count: "进程数上限",
  allow_network: "允许网络",
  allow_filesystem: "允许文件系统",
  // Switch node (C2-4)
  merge_strategy: "合并策略",
  // Subworkflow node (C2-5)
  workflow_id: "工作流 ID",
  version_id: "版本 ID",
  input_mapping: "输入映射",
  output_mapping: "输出映射",
  recursion_limit: "递归上限",
};

function isRecord(value: unknown): value is Record<string, unknown> {
  return typeof value === "object" && value !== null && !Array.isArray(value);
}

function labelFor(name: string, schema: JsonSchema): string {
  return LABELS[name] ?? schema.title ?? name.replaceAll("_", " ");
}

function resolveSchema(schema: JsonSchema, root: JsonSchema): JsonSchema {
  if (schema.$ref?.startsWith("#/$defs/")) {
    const key = schema.$ref.slice("#/$defs/".length);
    return resolveSchema(root.$defs?.[key] ?? schema, root);
  }
  if (schema.anyOf) {
    const concrete = schema.anyOf.find((option) => option.type !== "null");
    if (concrete) return { ...schema, ...resolveSchema(concrete, root), anyOf: undefined };
  }
  return schema;
}

function schemaType(schema: JsonSchema): string | undefined {
  if (Array.isArray(schema.type)) return schema.type.find((type) => type !== "null");
  if (schema.type) return schema.type;
  if (schema.properties) return "object";
  return undefined;
}

function defaultValue(schema: JsonSchema, root: JsonSchema): unknown {
  const resolved = resolveSchema(schema, root);
  if (resolved.default !== undefined) return structuredClone(resolved.default);
  const type = schemaType(resolved);
  if (type === "object") {
    return Object.fromEntries(
      Object.entries(resolved.properties ?? {}).map(([key, child]) => [
        key,
        defaultValue(child, root),
      ]),
    );
  }
  if (type === "array") return [];
  if (type === "boolean") return false;
  if (type === "integer" || type === "number") return 0;
  if (type === "string") return "";
  return null;
}

interface SchemaFormProps {
  schema: JsonSchema;
  value: Record<string, unknown>;
  onChange: (value: Record<string, unknown>) => void;
  hiddenKeys?: string[];
}

export function SchemaForm({ schema, value, onChange, hiddenKeys = [] }: SchemaFormProps) {
  const hidden = useMemo(() => new Set(hiddenKeys), [hiddenKeys]);
  return (
    <ObjectFields
      schema={resolveSchema(schema, schema)}
      root={schema}
      value={value}
      onChange={onChange}
      hidden={hidden}
    />
  );
}

function ObjectFields(props: {
  schema: JsonSchema;
  root: JsonSchema;
  value: Record<string, unknown>;
  onChange: (value: Record<string, unknown>) => void;
  hidden?: Set<string>;
}) {
  const required = new Set(props.schema.required ?? []);
  return (
    <div className="space-y-4">
      {Object.entries(props.schema.properties ?? {})
        .filter(([name]) => !props.hidden?.has(name))
        .map(([name, childSchema]) => (
          <SchemaField
            key={name}
            name={name}
            schema={childSchema}
            root={props.root}
            value={props.value[name]}
            required={required.has(name)}
            onChange={(next) => props.onChange({ ...props.value, [name]: next })}
          />
        ))}
    </div>
  );
}

function SchemaField(props: {
  name: string;
  schema: JsonSchema;
  root: JsonSchema;
  value: unknown;
  required?: boolean;
  onChange: (value: unknown) => void;
}) {
  const schema = resolveSchema(props.schema, props.root);
  const type = schemaType(schema);
  const label = labelFor(props.name, schema);
  const value = props.value ?? schema.default;

  if (schema.enum) {
    return (
      <FieldShell label={label} required={props.required} description={schema.description}>
        <select
          className="field-input h-9"
          value={String(value ?? "")}
          onChange={(event) => props.onChange(event.target.value)}
        >
          {schema.enum.map((option) => (
            <option key={String(option)} value={String(option)}>
              {String(option)}
            </option>
          ))}
        </select>
      </FieldShell>
    );
  }

  if (type === "boolean") {
    return (
      <label className="flex min-h-9 items-center justify-between gap-3 border-b border-line/70 pb-3">
        <span className="text-xs text-ice">
          {label} {props.required && <span className="text-warn">*</span>}
        </span>
        <input
          type="checkbox"
          checked={Boolean(value)}
          onChange={(event) => props.onChange(event.target.checked)}
          className="h-4 w-4 accent-cyan-400"
        />
      </label>
    );
  }

  if (type === "number" || type === "integer") {
    return (
      <FieldShell label={label} required={props.required} description={schema.description}>
        <input
          type="number"
          className="field-input h-9 font-mono"
          value={typeof value === "number" ? value : ""}
          min={schema.minimum}
          max={schema.maximum}
          step={type === "integer" ? 1 : "any"}
          onChange={(event) => {
            const parsed = Number(event.target.value);
            props.onChange(Number.isFinite(parsed) ? parsed : 0);
          }}
        />
      </FieldShell>
    );
  }

  if (type === "string") {
    const multiline = ["system_prompt", "user_prompt", "instruction"].includes(
      props.name,
    );
    return (
      <FieldShell label={label} required={props.required} description={schema.description}>
        {multiline ? (
          <textarea
            className="field-input min-h-24 resize-y leading-5"
            value={String(value ?? "")}
            onChange={(event) => props.onChange(event.target.value)}
          />
        ) : (
          <input
            className="field-input h-9"
            value={String(value ?? "")}
            onChange={(event) => props.onChange(event.target.value)}
          />
        )}
      </FieldShell>
    );
  }

  if (type === "array") {
    return (
      <ArrayField
        label={label}
        schema={schema}
        root={props.root}
        value={Array.isArray(value) ? value : []}
        onChange={props.onChange}
      />
    );
  }

  if (type === "object" && schema.properties) {
    return (
      <fieldset className="border-l border-line pl-3">
        <legend className="mb-3 font-mono text-[9px] uppercase tracking-[0.2em] text-ghost">
          {label}
        </legend>
        <ObjectFields
          schema={schema}
          root={props.root}
          value={isRecord(value) ? value : {}}
          onChange={props.onChange}
        />
      </fieldset>
    );
  }

  return (
    <FieldShell label={label} required={props.required} description={schema.description}>
      <JsonEditor value={value} onChange={props.onChange} />
    </FieldShell>
  );
}

function ArrayField(props: {
  label: string;
  schema: JsonSchema;
  root: JsonSchema;
  value: unknown[];
  onChange: (value: unknown) => void;
}) {
  const itemSchema = resolveSchema(props.schema.items ?? {}, props.root);
  return (
    <fieldset className="border-l border-line pl-3">
      <legend className="flex w-full items-center justify-between gap-3 pb-2 font-mono text-[9px] uppercase tracking-[0.2em] text-ghost">
        {props.label}
        <button
          type="button"
          onClick={() => props.onChange([...props.value, defaultValue(itemSchema, props.root)])}
          className="flex h-7 items-center gap-1 rounded-md px-2 text-[10px] normal-case tracking-normal text-pulse transition hover:bg-pulse/10"
        >
          <Plus size={12} /> 添加
        </button>
      </legend>
      <div className="space-y-3">
        {props.value.map((item, index) => (
          <div key={index} className="relative border-t border-line/70 pt-3">
            <button
              type="button"
              onClick={() => props.onChange(props.value.filter((_, itemIndex) => itemIndex !== index))}
              className="absolute right-0 top-2 flex h-7 w-7 items-center justify-center rounded-md text-ghost/50 transition hover:bg-bad/10 hover:text-bad"
              title="删除"
            >
              <Trash2 size={12} />
            </button>
            <div className="pr-9">
              <SchemaField
                name={`${index + 1}`}
                schema={itemSchema}
                root={props.root}
                value={item}
                onChange={(next) =>
                  props.onChange(
                    props.value.map((current, itemIndex) =>
                      itemIndex === index ? next : current,
                    ),
                  )
                }
              />
            </div>
          </div>
        ))}
        {props.value.length === 0 && (
          <p className="py-2 text-[10px] text-ghost/50">暂无条目</p>
        )}
      </div>
    </fieldset>
  );
}

function FieldShell(props: {
  label: string;
  required?: boolean;
  description?: string;
  children: React.ReactNode;
}) {
  return (
    <label className="flex flex-col gap-1.5">
      <span className="font-mono text-[9px] uppercase tracking-[0.2em] text-ghost">
        {props.label} {props.required && <span className="text-warn">*</span>}
      </span>
      {props.children}
      {props.description && (
        <span className="text-[10px] leading-4 text-ghost/55">{props.description}</span>
      )}
    </label>
  );
}

function JsonEditor(props: { value: unknown; onChange: (value: unknown) => void }) {
  const serialized = JSON.stringify(props.value ?? {}, null, 2);
  const [draft, setDraft] = useState(serialized);
  const [valid, setValid] = useState(true);

  useEffect(() => {
    setDraft(serialized);
    setValid(true);
  }, [serialized]);

  return (
    <div className="relative">
      <Braces size={12} className="absolute right-2 top-2 text-ghost/40" />
      <textarea
        className={cn(
          "min-h-24 w-full resize-y rounded-md border bg-void/70 px-3 py-2 pr-7 font-mono text-[10px] leading-5 text-ice outline-hidden",
          valid ? "border-line focus:border-pulse/60" : "border-bad/60 focus:border-bad",
        )}
        value={draft}
        spellCheck={false}
        onChange={(event) => {
          setDraft(event.target.value);
          try {
            props.onChange(JSON.parse(event.target.value) as unknown);
            setValid(true);
          } catch {
            setValid(false);
          }
        }}
      />
    </div>
  );
}
