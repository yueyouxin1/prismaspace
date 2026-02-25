# Workflow 运行时指南（前端 Mock 专用）

## 1. 目的
本文档用于前端开发者在不请求后端的情况下，基于后端真实运行时契约构建 Workflow Mock 数据，并在 PlayGround 工作区独立完成联调与交互开发。

## 2. 适用范围
- 后端实现来源：`src/app/services/resource/workflow` + `src/app/engine/workflow`
- 覆盖内容：
  - 支持的节点数据结构（8 类）
  - 工作流图 DSL 契约
  - 参数契约（`ParameterSchema`）
  - 运行时事件/执行结果契约
- 不覆盖内容：
  - 表单契约（`forms`）：留空，由前端按自身架构定义

## 3. 支持节点清单（当前注册）
- 引擎内置节点：`Start` `Output` `End` `Branch` `Loop`
- 业务资源节点：`LLMNode` `AgentNode` `ToolNode`

## 4. 通用数据契约

### 4.1 Workflow 图（DSL）
```ts
type WorkflowGraphDef = {
  nodes: WorkflowNode[]
  edges: WorkflowEdge[]
  viewport?: Record<string, unknown>
}

type WorkflowNode = {
  id: string
  data: NodeData
  position?: { x: number; y: number }
}

type WorkflowEdge = {
  id?: string
  sourceNodeID: string
  targetNodeID: string
  sourcePortID: string
  targetPortID: string
}

type NodeData = {
  registryId: string
  name: string
  description?: string
  config: BaseNodeConfig & Record<string, unknown>
  inputs: ParameterSchema[]
  outputs: ParameterSchema[]
  // 仅 Loop 使用
  blocks?: WorkflowNode[]
  edges?: WorkflowEdge[]
}
```

### 4.2 BaseNodeConfig
```ts
type ExecutionPolicy = {
  switch?: boolean
  timeoutMs?: number
  retryTimes?: number
  // 1=中断, 2=降级固定返回, 3=走 error 端口
  processType?: 1 | 2 | 3
  dataOnErr?: string | null
}

type BaseNodeConfig = {
  executionPolicy?: ExecutionPolicy
  stream?: boolean
  returnType?: string | null
  content?: string | null
  // 允许扩展字段
  [k: string]: unknown
}
```

### 4.3 参数契约（ParameterSchema）
```ts
type SchemaType = "string" | "number" | "integer" | "boolean" | "object" | "array"

type ValueRefContent = {
  blockID: string
  path: string
  source?: string
}

type ParameterValue =
  | { type: "literal"; content: unknown }
  | { type: "expr"; content: string }
  | { type: "ref"; content: ValueRefContent }

type SchemaBlueprint = {
  type: SchemaType
  uid?: number
  description?: string
  enum?: unknown[]
  default?: unknown
  properties?: ParameterSchema[]
  items?: SchemaBlueprint
}

type ParameterSchema = SchemaBlueprint & {
  name: string
  required?: boolean
  open?: boolean
  role?: string
  label?: string
  value?: ParameterValue
  meta?: Record<string, unknown>
}
```

## 5. 各节点运行时数据结构

### 5.1 Start
```ts
type StartNodeData = NodeData & {
  registryId: "Start"
  inputs: []          // 通常为空
  outputs: ParameterSchema[] // Workflow 输入契约来源
  config: {}
}
```
运行语义：`Start.outputs` 会按 `payload` 填充，作为 `start` 节点输出。

### 5.2 Output
```ts
type OutputNodeData = NodeData & {
  registryId: "Output"
  config: {
    stream?: boolean
    returnType?: "Object" | "Text"
    content?: string
  }
}
```
运行语义：当前实现主要作为中间节点，默认返回空对象输出。

### 5.3 End
```ts
type EndNodeData = NodeData & {
  registryId: "End"
  inputs: ParameterSchema[] // Workflow 输出契约来源
  config: {
    stream?: boolean
    returnType?: "Object" | "Text"
    content?: string
  }
}
```
运行语义：
- 非流式：`output = schemas2obj(inputs)`
- 文本模式：`returnType="Text"` 且 `content` 存在时，`content` 作为文本模板
- 流式：`stream=true && returnType="Text" && content 非空` 时产生 `stream_*` 事件

### 5.4 Branch
```ts
type BranchCondition = {
  operator: number // 1..10
  left: ParameterSchema
  right: ParameterSchema
}

type BranchGroup = {
  id?: string
  logic?: "&" | "|"
  conditions: BranchCondition[]
}

type BranchNodeData = NodeData & {
  registryId: "Branch"
  config: {
    branchs: BranchGroup[]
  }
}
```
运行语义：命中第 N 个分支后，`activated_port = String(N)`；未命中为 `"-1"`。

### 5.5 Loop
```ts
type LoopNodeData = NodeData & {
  registryId: "Loop"
  config: {
    loopType: "count" | "list"
    loopCount?: ParameterSchema // loopType=count 时必需，type 必须 integer/number
    loopList?: ParameterSchema  // loopType=list 时必需，type 必须 array
  }
  blocks: WorkflowNode[] // Loop 内部子节点
  edges: WorkflowEdge[]  // Loop 内部子边
}
```
关键约束：
- `loop-block-output` 只允许出现在 `Loop.outputs` 的 `value.ref.content.source`
- Loop 内部专用端口：
  - 入口：`sourcePortID = "loop-function-inline-output"`
  - 出口：`targetPortID = "loop-function-inline-input"`

### 5.6 LLMNode
```ts
type LLMNodeData = NodeData & {
  registryId: "LLMNode"
  config: BaseNodeConfig & {
    llm_module_version_uuid?: string | null
    system_prompt?: string
    history?: Array<{ role: string; content: string }>
    agent_config: {
      model_params?: {
        temperature?: number
        top_p?: number
        presence_penalty?: number
        frequency_penalty?: number
      }
      io_config?: {
        max_response_tokens?: number
        enable_deep_thinking?: boolean
        max_thinking_tokens?: number | null
        response_format?: { type?: string; [k: string]: unknown }
      }
      [k: string]: unknown
    }
  }
}
```
注意：运行时强依赖 `config.agent_config` 存在。

### 5.7 AgentNode
```ts
type AgentNodeData = NodeData & {
  registryId: "AgentNode"
  config: BaseNodeConfig & {
    resource_instance_uuid: string
    input_query: string
    enable_session?: boolean
    session_uuid?: string | null
    history?: Array<{ role: string; content: string }> | null
  }
}
```
注意：`enable_session=true` 时必须提供 `session_uuid`。

### 5.8 ToolNode
```ts
type ToolNodeData = NodeData & {
  registryId: "ToolNode"
  config: BaseNodeConfig & {
    resource_instance_uuid: string
  }
}
```

## 6. 图结构与引用校验规则（Mock 必须满足）
- 必须且只能有一个 `Start`，一个 `End`
- 必须是 DAG（不能有环）
- 所有节点必须从 `Start` 可达（无孤立节点）
- `ref.content.blockID`、`ref.content.path` 不能为空
- `ref.blockID` 必须存在于图中，且必须是当前节点上游
- `ref.path` 必须存在于被引用节点 `outputs` 结构中
- `source="loop-block-output"` 只允许在 `Loop.outputs`

## 7. Workflow 实例读模型（用于页面加载 Mock）
```ts
type WorkflowReadMock = {
  uuid: string
  version_tag: string
  status: string
  created_at?: string
  graph: WorkflowGraphDef
  inputs_schema: ParameterSchema[]  // 建议与 Start.outputs 一致
  outputs_schema: ParameterSchema[] // 建议与 End.inputs 一致
  is_stream: boolean                // 建议与 End.config.stream 一致
}
```

## 8. 运行时事件契约（SSE / WS）

### 8.1 事件包
```ts
type WorkflowEvent = {
  event: string
  data: Record<string, unknown>
}
```

### 8.2 NodeState（`node_start/node_finish/node_error/node_skipped` 的 data）
```ts
type NodeStatus =
  | "PENDING" | "RUNNING" | "COMPLETED" | "SKIPPED"
  | "STREAMTASK" | "STREAMSTART" | "STREAMING" | "STREAMEND" | "FAILED"

type NodeResultData = {
  output?: Record<string, unknown>
  content?: string | null
  error_msg?: string | null
}

type NodeState = {
  node_id: string
  status: NodeStatus
  input: Record<string, unknown>
  result: NodeResultData
  activated_port: string
  executed_time: number
}
```
补充：当节点启用 `executionPolicy.switch=true` 且 `processType` 为 `2` 或 `3` 时，节点输出中会注入：
```json
{
  "runtimeStatus": {
    "isSuccess": false,
    "errorBody": {
      "message": "错误信息",
      "type": "异常类型",
      "data": "dataOnErr 或空"
    }
  }
}
```

### 8.3 StreamEvent（`stream_start/stream_chunk/stream_end` 的 data）
```ts
type StreamEvent = {
  node_id: string
  status: "STREAMSTART" | "STREAMING" | "STREAMEND"
  content?: string | null
}
```

### 8.4 典型事件序列
1. `start` -> `{ trace_id }`
2. `node_start` -> `NodeState`
3. `node_finish` / `node_error` / `node_skipped` -> `NodeState`
4. （可选）`stream_start` -> `StreamEvent`
5. （可选）`stream_chunk` -> `StreamEvent`
6. （可选）`stream_end` -> `StreamEvent`
7. `finish` -> `NodeResultData`
8. （异常）`error` -> `{ error: string }`
9. （系统级异常，少见）`system_error` -> `string | object`

## 9. 阻塞执行接口返回契约（`POST /workflow/{uuid}/execute`）
```ts
type WorkflowExecutionResponse = {
  success: boolean
  data: {
    output?: Record<string, unknown>
    content?: string | null
    error_msg?: string | null
    trace_id?: string | null
  }
  error_message?: string | null
}
```

## 10. 前端 Mock 建议（可直接落地）

### 10.1 节点定义（画布侧边栏）
后端真实接口是 `node_uid + node + forms`，你可以最小化为：
```json
[
  {
    "id": 1,
    "node_uid": "Start",
    "category": "common",
    "label": "开始",
    "icon": "play",
    "node": {
      "registryId": "Start",
      "name": "开始",
      "description": "工作流的起始节点。",
      "inputs": [],
      "outputs": [],
      "config": {}
    },
    "forms": [],
    "is_active": true
  }
]
```

### 10.2 最小可运行图
```json
{
  "nodes": [
    {
      "id": "start",
      "data": {
        "registryId": "Start",
        "name": "Start",
        "inputs": [],
        "outputs": [{ "name": "hello", "type": "string" }],
        "config": {}
      },
      "position": { "x": 100, "y": 200 }
    },
    {
      "id": "end",
      "data": {
        "registryId": "End",
        "name": "End",
        "inputs": [
          {
            "name": "out",
            "type": "string",
            "value": { "type": "ref", "content": { "blockID": "start", "path": "hello" } }
          }
        ],
        "outputs": [],
        "config": { "returnType": "Object", "stream": false }
      },
      "position": { "x": 420, "y": 200 }
    }
  ],
  "edges": [
    { "sourceNodeID": "start", "targetNodeID": "end", "sourcePortID": "0", "targetPortID": "0" }
  ],
  "viewport": { "x": 0, "y": 0, "zoom": 1 }
}
```

### 10.3 最小运行结果 Mock
```json
{
  "success": true,
  "data": {
    "output": { "out": "world" },
    "trace_id": "mock-trace-id-001"
  }
}
```

### 10.4 最小 SSE 事件 Mock
```json
[
  { "event": "start", "data": { "trace_id": "mock-trace-id-001" } },
  { "event": "node_start", "data": { "node_id": "start", "status": "RUNNING", "input": {}, "result": { "output": {} }, "activated_port": "0", "executed_time": 0 } },
  { "event": "node_finish", "data": { "node_id": "start", "status": "COMPLETED", "input": { "hello": "world" }, "result": { "output": { "hello": "world" } }, "activated_port": "0", "executed_time": 0.001 } },
  { "event": "finish", "data": { "output": { "out": "world" } } }
]
```

## 11. 前端落地检查清单
- `registryId` 必须使用本文档列出的 8 个值之一
- `Start.outputs` 与 `inputs_schema` 一致
- `End.inputs` 与 `outputs_schema` 一致
- 引用路径严格对齐输出结构
- Loop 聚合输出必须使用 `source: "loop-block-output"`
- 流式回放时，至少包含 `start` 与 `finish` 事件
