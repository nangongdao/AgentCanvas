/**
 * Core type definitions for AgentCanvas API objects.
 */

/**
 * Workflow definition with nodes and edges.
 */
export interface Workflow {
  id: string;
  name: string;
  description?: string;
  nodes: WorkflowNode[];
  edges: WorkflowEdge[];
  created_at: string;
  updated_at: string;
  user_id: string;
}

/**
 * Input parameters for creating a workflow.
 */
export interface CreateWorkflowInput {
  name: string;
  description?: string;
  nodes?: WorkflowNode[];
  edges?: WorkflowEdge[];
}

/**
 * Input parameters for updating a workflow.
 */
export interface UpdateWorkflowInput {
  name?: string;
  description?: string;
  nodes?: WorkflowNode[];
  edges?: WorkflowEdge[];
}

/**
 * Node in a workflow graph.
 */
export interface WorkflowNode {
  id: string;
  type: string;
  position: { x: number; y: number };
  data: Record<string, unknown>;
}

/**
 * Edge connecting two nodes in a workflow.
 */
export interface WorkflowEdge {
  id: string;
  source: string;
  target: string;
  sourceHandle?: string;
  targetHandle?: string;
}

/**
 * Workflow execution instance.
 */
export interface Execution {
  id: string;
  workflow_id: string;
  status: ExecutionStatus;
  started_at: string;
  completed_at?: string;
  error?: string;
  result?: Record<string, unknown>;
  user_id: string;
}

/**
 * Execution status enumeration.
 */
export type ExecutionStatus = 'pending' | 'running' | 'completed' | 'failed' | 'cancelled';

/**
 * Input parameters for starting an execution.
 */
export interface StartExecutionInput {
  workflow_id: string;
  input?: Record<string, unknown>;
}

/**
 * Real-time execution event from SSE stream.
 */
export interface ExecutionEvent {
  type: 'status' | 'log' | 'result' | 'error';
  execution_id: string;
  timestamp: string;
  data: Record<string, unknown>;
}

/**
 * LLM provider configuration.
 */
export interface Provider {
  id: string;
  name: string;
  provider_type: string;
  config: Record<string, unknown>;
  created_at: string;
  user_id: string;
}

/**
 * Input parameters for creating a provider.
 */
export interface CreateProviderInput {
  name: string;
  provider_type: string;
  config: Record<string, unknown>;
}

/**
 * Input parameters for updating a provider.
 */
export interface UpdateProviderInput {
  name?: string;
  config?: Record<string, unknown>;
}

/**
 * Pagination parameters for list operations.
 */
export interface PaginationParams {
  skip?: number;
  limit?: number;
}

/**
 * API error response.
 */
export interface APIError {
  detail: string;
  status?: number;
}

/**
 * Client configuration options.
 */
export interface ClientConfig {
  apiKey: string;
  baseURL?: string;
  timeout?: number;
}
