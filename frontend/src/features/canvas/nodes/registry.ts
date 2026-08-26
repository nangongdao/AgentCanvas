import type { NodeTypes } from "@xyflow/react";

import { BaseNode } from "./BaseNode";

/** Node type registry used by React Flow. All custom nodes share BaseNode for P0. */
export const nodeTypes: NodeTypes = {
  start: BaseNode,
  agent: BaseNode,
  tool: BaseNode,
  condition: BaseNode,
  switch: BaseNode,
  rag: BaseNode,
  human: BaseNode,
  iteration: BaseNode,
  http: BaseNode,
  code: BaseNode,
  subworkflow: BaseNode,
  end: BaseNode,
  plugin: BaseNode,
};
