/**
 * AgentCanvas API client for TypeScript/JavaScript applications.
 */

import type {
  ClientConfig,
  Workflow,
  CreateWorkflowInput,
  UpdateWorkflowInput,
  Execution,
  StartExecutionInput,
  Provider,
  CreateProviderInput,
  UpdateProviderInput,
  PaginationParams,
  APIError,
} from './types';
import { createExecutionStream, type StreamOptions } from './streams';

/**
 * Custom error class for API errors.
 */
export class AgentCanvasError extends Error {
  public status?: number;

  constructor(message: string, status?: number) {
    super(message);
    this.name = 'AgentCanvasError';
    this.status = status;
  }
}

/**
 * Main client class for interacting with AgentCanvas API.
 */
export class AgentCanvasClient {
  private apiKey: string;
  private baseURL: string;
  private timeout: number;

  constructor(config: ClientConfig) {
    this.apiKey = config.apiKey;
    this.baseURL = config.baseURL || 'http://localhost:8000';
    this.timeout = config.timeout || 30000;
  }

  /**
   * Make an HTTP request to the API.
   */
  private async request<T>(
    method: string,
    endpoint: string,
    body?: unknown,
    params?: Record<string, string | number>
  ): Promise<T> {
    const url = new URL(`${this.baseURL}${endpoint}`);

    if (params) {
      Object.entries(params).forEach(([key, value]) => {
        url.searchParams.append(key, String(value));
      });
    }

    const headers: Record<string, string> = {
      'Authorization': `Bearer ${this.apiKey}`,
      'Content-Type': 'application/json',
    };

    const controller = new AbortController();
    const timeoutId = setTimeout(() => controller.abort(), this.timeout);

    try {
      const response = await fetch(url.toString(), {
        method,
        headers,
        body: body ? JSON.stringify(body) : undefined,
        signal: controller.signal,
      });

      clearTimeout(timeoutId);

      if (!response.ok) {
        let errorMessage = `HTTP ${response.status}: ${response.statusText}`;
        try {
          const errorData = await response.json() as APIError;
          errorMessage = errorData.detail || errorMessage;
        } catch {
          // Use default error message
        }
        throw new AgentCanvasError(errorMessage, response.status);
      }

      return await response.json() as T;
    } catch (error) {
      clearTimeout(timeoutId);
      if (error instanceof AgentCanvasError) {
        throw error;
      }
      if (error instanceof Error) {
        if (error.name === 'AbortError') {
          throw new AgentCanvasError('Request timeout');
        }
        throw new AgentCanvasError(error.message);
      }
      throw new AgentCanvasError('Unknown error occurred');
    }
  }

  // ==================== Workflow Methods ====================

  /**
   * List all workflows with optional pagination.
   */
  async listWorkflows(params?: PaginationParams): Promise<Workflow[]> {
    const queryParams: Record<string, number> = {};
    if (params?.skip !== undefined) queryParams.skip = params.skip;
    if (params?.limit !== undefined) queryParams.limit = params.limit;

    return this.request<Workflow[]>('GET', '/api/workflows', undefined, queryParams);
  }

  /**
   * Get a specific workflow by ID.
   */
  async getWorkflow(id: string): Promise<Workflow> {
    return this.request<Workflow>('GET', `/api/workflows/${id}`);
  }

  /**
   * Create a new workflow.
   */
  async createWorkflow(input: CreateWorkflowInput): Promise<Workflow> {
    return this.request<Workflow>('POST', '/api/workflows', input);
  }

  /**
   * Update an existing workflow.
   */
  async updateWorkflow(id: string, input: UpdateWorkflowInput): Promise<Workflow> {
    return this.request<Workflow>('PUT', `/api/workflows/${id}`, input);
  }

  /**
   * Delete a workflow.
   */
  async deleteWorkflow(id: string): Promise<void> {
    await this.request<void>('DELETE', `/api/workflows/${id}`);
  }

  // ==================== Execution Methods ====================

  /**
   * List all executions with optional pagination.
   */
  async listExecutions(params?: PaginationParams): Promise<Execution[]> {
    const queryParams: Record<string, number> = {};
    if (params?.skip !== undefined) queryParams.skip = params.skip;
    if (params?.limit !== undefined) queryParams.limit = params.limit;

    return this.request<Execution[]>('GET', '/api/executions', undefined, queryParams);
  }

  /**
   * Get a specific execution by ID.
   */
  async getExecution(id: string): Promise<Execution> {
    return this.request<Execution>('GET', `/api/executions/${id}`);
  }

  /**
   * Start a new workflow execution.
   */
  async startExecution(input: StartExecutionInput): Promise<Execution> {
    return this.request<Execution>('POST', '/api/executions', input);
  }

  /**
   * Cancel a running execution.
   */
  async cancelExecution(id: string): Promise<Execution> {
    return this.request<Execution>('POST', `/api/executions/${id}/cancel`);
  }

  /**
   * Stream real-time execution events using SSE.
   */
  streamExecution(executionId: string, options: StreamOptions) {
    return createExecutionStream(executionId, this.baseURL, this.apiKey, options);
  }

  // ==================== Provider Methods ====================

  /**
   * List all LLM providers.
   */
  async listProviders(): Promise<Provider[]> {
    return this.request<Provider[]>('GET', '/api/providers');
  }

  /**
   * Get a specific provider by ID.
   */
  async getProvider(id: string): Promise<Provider> {
    return this.request<Provider>('GET', `/api/providers/${id}`);
  }

  /**
   * Create a new LLM provider.
   */
  async createProvider(input: CreateProviderInput): Promise<Provider> {
    return this.request<Provider>('POST', '/api/providers', input);
  }

  /**
   * Update an existing provider.
   */
  async updateProvider(id: string, input: UpdateProviderInput): Promise<Provider> {
    return this.request<Provider>('PUT', `/api/providers/${id}`, input);
  }

  /**
   * Delete a provider.
   */
  async deleteProvider(id: string): Promise<void> {
    await this.request<void>('DELETE', `/api/providers/${id}`);
  }
}
