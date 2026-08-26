export const DEFAULT_ITERATION_SUBGRAPH = {
  nodes: [
    { id: "item_start", type: "start", position: { x: 0, y: 0 }, config: {} },
    {
      id: "item_agent",
      type: "agent",
      position: { x: 260, y: 0 },
      config: {
        model_config_id: "default",
        system_prompt: "处理当前数组项并返回结果。",
        user_prompt: "{{input.item}}",
      },
    },
    {
      id: "item_end",
      type: "end",
      position: { x: 520, y: 0 },
      config: { output_template: { result: "{{nodes.item_agent.output}}" } },
    },
  ],
  edges: [
    { id: "item_edge_1", source: "item_start", target: "item_agent" },
    { id: "item_edge_2", source: "item_agent", target: "item_end" },
  ],
};

export function defaultIterationConfig(): Record<string, unknown> {
  return {
    items: "{{input.items}}",
    item_variable: "item",
    index_variable: "index",
    batch_size: 10,
    concurrency_limit: 4,
    failure_strategy: "abort",
    recursion_limit: 50,
    subgraph: structuredClone(DEFAULT_ITERATION_SUBGRAPH),
  };
}
