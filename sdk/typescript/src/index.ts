/**
 * AgentCanvas TypeScript/JavaScript SDK
 *
 * Official client library for interacting with the AgentCanvas API.
 * @packageDocumentation
 */

export { AgentCanvasClient, AgentCanvasError } from './client';
export { ExecutionStream, createExecutionStream } from './streams';
export type { EventCallback, StreamOptions } from './streams';
export type {
  ClientConfig,
  Workflow,
  CreateWorkflowInput,
  UpdateWorkflowInput,
  WorkflowNode,
  WorkflowEdge,
  Execution,
  ExecutionStatus,
  StartExecutionInput,
  ExecutionEvent,
  Provider,
  CreateProviderInput,
  UpdateProviderInput,
  PaginationParams,
  APIError,
} from './types';
