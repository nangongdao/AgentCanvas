# AgentCanvas TypeScript/JavaScript SDK

Official TypeScript/JavaScript client library for the AgentCanvas API.

## Installation

```bash
npm install @agentcanvas/sdk
# or
yarn add @agentcanvas/sdk
# or
pnpm add @agentcanvas/sdk
```

## Quick Start

```typescript
import { AgentCanvasClient } from '@agentcanvas/sdk';

// Initialize the client
const client = new AgentCanvasClient({
  apiKey: 'your-api-key',
  baseURL: 'http://localhost:8000', // Optional, defaults to localhost:8000
});

// Create a workflow
const workflow = await client.createWorkflow({
  name: 'My First Workflow',
  description: 'A simple workflow example',
  nodes: [
    {
      id: '1',
      type: 'llm',
      position: { x: 100, y: 100 },
      data: { prompt: 'Hello, world!' },
    },
  ],
  edges: [],
});

// Start execution
const execution = await client.startExecution({
  workflow_id: workflow.id,
});

console.log('Execution started:', execution.id);
```

## API Reference

### Client Initialization

```typescript
const client = new AgentCanvasClient({
  apiKey: string;        // Required: Your API key
  baseURL?: string;      // Optional: API base URL (default: http://localhost:8000)
  timeout?: number;      // Optional: Request timeout in ms (default: 30000)
});
```

### Workflows

#### List Workflows

```typescript
const workflows = await client.listWorkflows({
  skip: 0,     // Optional: Number of records to skip
  limit: 10,   // Optional: Maximum number of records to return
});
```

#### Get Workflow

```typescript
const workflow = await client.getWorkflow('workflow-id');
```

#### Create Workflow

```typescript
const workflow = await client.createWorkflow({
  name: 'My Workflow',
  description: 'Workflow description',  // Optional
  nodes: [/* WorkflowNode[] */],        // Optional
  edges: [/* WorkflowEdge[] */],        // Optional
});
```

#### Update Workflow

```typescript
const updated = await client.updateWorkflow('workflow-id', {
  name: 'Updated Name',           // Optional
  description: 'New description', // Optional
  nodes: [/* ... */],             // Optional
  edges: [/* ... */],             // Optional
});
```

#### Delete Workflow

```typescript
await client.deleteWorkflow('workflow-id');
```

### Executions

#### List Executions

```typescript
const executions = await client.listExecutions({
  skip: 0,
  limit: 20,
});
```

#### Get Execution

```typescript
const execution = await client.getExecution('execution-id');
```

#### Start Execution

```typescript
const execution = await client.startExecution({
  workflow_id: 'workflow-id',
  input: { key: 'value' },  // Optional: Input data for the workflow
});
```

#### Cancel Execution

```typescript
const cancelled = await client.cancelExecution('execution-id');
```

#### Stream Execution Events

Real-time monitoring of execution progress using Server-Sent Events:

```typescript
const stream = client.streamExecution('execution-id', {
  onEvent: (event) => {
    console.log('Event type:', event.type);
    console.log('Event data:', event.data);
    
    if (event.type === 'status') {
      console.log('Status changed:', event.data.status);
    } else if (event.type === 'log') {
      console.log('Log message:', event.data.message);
    } else if (event.type === 'result') {
      console.log('Execution result:', event.data.result);
    } else if (event.type === 'error') {
      console.error('Execution error:', event.data.error);
    }
  },
  onError: (error) => {
    console.error('Stream error:', error);
  },
  onEnd: () => {
    console.log('Stream ended');
  },
});

// Start streaming
stream.start();

// Stop streaming when done
// stream.stop();
```

### Providers

#### List Providers

```typescript
const providers = await client.listProviders();
```

#### Get Provider

```typescript
const provider = await client.getProvider('provider-id');
```

#### Create Provider

```typescript
const provider = await client.createProvider({
  name: 'My OpenAI Provider',
  provider_type: 'openai',
  config: {
    api_key: 'your-openai-key',
    model: 'gpt-4',
  },
});
```

#### Update Provider

```typescript
const updated = await client.updateProvider('provider-id', {
  name: 'Updated Name',     // Optional
  config: { /* ... */ },    // Optional
});
```

#### Delete Provider

```typescript
await client.deleteProvider('provider-id');
```

## Type Definitions

### Workflow

```typescript
interface Workflow {
  id: string;
  name: string;
  description?: string;
  nodes: WorkflowNode[];
  edges: WorkflowEdge[];
  created_at: string;
  updated_at: string;
  user_id: string;
}
```

### Execution

```typescript
interface Execution {
  id: string;
  workflow_id: string;
  status: 'pending' | 'running' | 'completed' | 'failed' | 'cancelled';
  started_at: string;
  completed_at?: string;
  error?: string;
  result?: Record<string, unknown>;
  user_id: string;
}
```

### Provider

```typescript
interface Provider {
  id: string;
  name: string;
  provider_type: string;
  config: Record<string, unknown>;
  created_at: string;
  user_id: string;
}
```

## Error Handling

```typescript
import { AgentCanvasError } from '@agentcanvas/sdk';

try {
  const workflow = await client.getWorkflow('invalid-id');
} catch (error) {
  if (error instanceof AgentCanvasError) {
    console.error('API Error:', error.message);
    console.error('Status Code:', error.status);
  } else {
    console.error('Unexpected error:', error);
  }
}
```

## Examples

### Complete Workflow Execution with Streaming

```typescript
import { AgentCanvasClient } from '@agentcanvas/sdk';

const client = new AgentCanvasClient({
  apiKey: process.env.AGENTCANVAS_API_KEY!,
});

async function runWorkflow() {
  // Create workflow
  const workflow = await client.createWorkflow({
    name: 'Data Processing Pipeline',
    nodes: [
      {
        id: 'start',
        type: 'input',
        position: { x: 0, y: 0 },
        data: {},
      },
      {
        id: 'process',
        type: 'llm',
        position: { x: 200, y: 0 },
        data: {
          prompt: 'Process this data: {{input}}',
          provider_id: 'provider-123',
        },
      },
    ],
    edges: [
      {
        id: 'e1',
        source: 'start',
        target: 'process',
      },
    ],
  });

  // Start execution
  const execution = await client.startExecution({
    workflow_id: workflow.id,
    input: { data: 'Sample input data' },
  });

  // Stream execution events
  const stream = client.streamExecution(execution.id, {
    onEvent: (event) => {
      if (event.type === 'result') {
        console.log('Final result:', event.data.result);
        stream.stop();
      }
    },
    onError: (error) => {
      console.error('Error:', error);
    },
  });

  await stream.start();
}

runWorkflow().catch(console.error);
```

## Requirements

- Node.js 18.0.0 or higher
- TypeScript 5.0+ (for TypeScript projects)

## License

MIT

## Support

- Documentation: https://docs.agentcanvas.dev
- Issues: https://github.com/yourusername/agentcanvas/issues
