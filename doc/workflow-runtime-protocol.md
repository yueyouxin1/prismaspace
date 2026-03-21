# Workflow Runtime Protocol（WRP）v1

- 协议名：`prismaspace.workflow.runtime/v1`
- 定位：PrismaSpace Workflow 的原生运行时协议
- 适用面：HTTP SSE、WebSocket、replay、live attach、future UIAPP bridge、future Chat Flow profile
- 非目标：不直接等同 AG-UI；AG-UI 仅作为 future conversational/profile bridge

## 1. 设计目标

WRP v1 的目标是让 Workflow 的外部契约不再散落在 `workflow_api.py`、`ws_handler.py`、前端 workbench 和测试中，而是统一收口为：

1. versioned
2. event-driven
3. transport-agnostic
4. run-lifecycle independent from connection
5. replay / live-attach friendly
6. interrupt / resume friendly
7. extensible for UIAPP and Chat Flow

## 2. 运行状态

唯一合法 run status：

- `pending`
- `running`
- `succeeded`
- `failed`
- `cancelled`
- `interrupted`

## 3. 能力声明

`session.ready.payload.capabilities` 当前返回：

- `cancel`
- `interrupt`
- `resume`
- `replay`
- `live_attach`
- `history`

## 4. 统一事件信封

所有 WRP 事件都使用同一 envelope：

```json
{
  "type": "run.started",
  "seq": 1,
  "ts": "2026-03-21T09:00:00Z",
  "runId": "run_xxx",
  "threadId": "workflow-thread_xxx",
  "parentRunId": null,
  "traceId": "trace_xxx",
  "scope": null,
  "node": null,
  "payload": {}
}
```

字段约束：

- `type`：canonical WRP event type
- `seq`：同一 `runId` 下单调递增，用于 attach / replay 补流
- `runId`：所有 workflow runtime 事件的主锚点
- `threadId`：通用 workflow 中可为空；interactive profile 可用于 scope 关联
- `scope`：仅显式 scoped profile 才出现
- `node`：节点级事件附带 `id / registryId / name`
- `payload`：事件业务载荷

## 5. Canonical Event Types

### 5.1 Session / Governance

- `session.ready`
- `checkpoint.created`
- `run.attached`
- `run.replay.completed`
- `system.error`

### 5.2 Run Lifecycle

- `run.started`
- `run.finished`
- `run.failed`
- `run.cancelled`
- `run.interrupted`

### 5.3 Node Lifecycle

- `node.started`
- `node.completed`
- `node.failed`
- `node.skipped`

### 5.4 Stream Lifecycle

- `stream.started`
- `stream.delta`
- `stream.finished`

### 5.5 Reserved Extensions

- `ui.mount`
- `ui.patch`
- `ui.unmount`
- `agent.event`
- `chat.event`

## 6. 事件 Payload 约定

### 6.1 `run.started`

```json
{
  "trace_id": "trace_xxx",
  "run_id": "run_xxx",
  "thread_id": "workflow-thread_xxx"
}
```

### 6.2 `run.finished`

```json
{
  "output": {},
  "content": null,
  "error_msg": null,
  "run_id": "run_xxx",
  "thread_id": "workflow-thread_xxx"
}
```

### 6.3 `run.cancelled`

```json
{
  "output": {},
  "outcome": "cancelled",
  "run_id": "run_xxx",
  "thread_id": "workflow-thread_xxx"
}
```

### 6.4 `run.interrupted`

```json
{
  "interrupt": {
    "id": null,
    "node_id": "interrupt_node",
    "reason": "approval_required",
    "message": "Please confirm the workflow run.",
    "resumeToken": {
      "runId": "run_xxx",
      "threadId": "workflow-thread_xxx",
      "nodeId": "interrupt_node"
    },
    "payload": {
      "runId": "run_xxx",
      "nodeId": "interrupt_node",
      "resumeOutputKey": "resume",
      "resumeToken": {
        "runId": "run_xxx",
        "threadId": "workflow-thread_xxx",
        "nodeId": "interrupt_node"
      }
    }
  },
  "outcome": "interrupt",
  "run_id": "run_xxx",
  "thread_id": "workflow-thread_xxx"
}
```

### 6.5 `checkpoint.created`

```json
{
  "checkpointId": 12,
  "reason": "node_completed",
  "nodeId": "llm_1",
  "stepIndex": 4,
  "run_id": "run_xxx",
  "thread_id": "workflow-thread_xxx"
}
```

## 7. 控制消息

### 7.1 已启用

- `run.start`
- `run.attach`
- `run.cancel`
- `run.resume`
- `ui.event.submit`

### 7.2 预留

- `ui.event.abort`
- `active-run.resolve`

`active-run.resolve` 仅保留给显式 scoped interactive profile。通用 workflow workbench 不使用它来猜测运行中的流。

## 8. Control Message Schema

### 8.1 `run.start`

```json
{
  "type": "run.start",
  "requestId": "req_1",
  "instanceUuid": "workflow_uuid",
  "input": {
    "inputs": {}
  }
}
```

### 8.2 `run.attach`

```json
{
  "type": "run.attach",
  "requestId": "req_2",
  "runId": "run_xxx",
  "afterSeq": 18
}
```

### 8.3 `run.cancel`

```json
{
  "type": "run.cancel",
  "requestId": "req_3",
  "runId": "run_xxx"
}
```

### 8.4 `run.resume`

```json
{
  "type": "run.resume",
  "requestId": "req_4",
  "instanceUuid": "workflow_uuid",
  "runId": "run_xxx",
  "resume": {
    "token": {
      "runId": "run_xxx",
      "threadId": "workflow-thread_xxx",
      "nodeId": "interrupt_node"
    },
    "output": {
      "approved": true
    },
    "meta": {}
  }
}
```

### 8.5 `ui.event.submit`

```json
{
  "type": "ui.event.submit",
  "requestId": "req_5",
  "runId": "run_xxx",
  "interactionId": "uiapp_interaction_xxx",
  "payload": {}
}
```

## 9. Attach / Replay / Disconnect 语义

- `POST /api/v1/workflow/{uuid}/sse`
  - 新建 run
  - 断开连接只 detach，不 cancel run

- `GET /api/v1/workflow/runs/{run_id}/live`
  - 以 `run_id` + `after_seq` 重新接回 live stream
  - 先读 live buffer，再用 durable event log 补齐尾段终态事件

- `GET /api/v1/workflow/runs/{run_id}/replay`
  - 返回持久化事件重放流
  - 首先发送 `session.ready`
  - 结束时发送 `run.replay.completed`

- `WS /api/v1/workflow/ws`
  - 是 WRP 的双向 transport
  - run 与连接生命周期解耦
  - 切换 attach 只 detach 当前观察，不隐式 cancel run

## 10. Interrupt / Resume 规则

- interrupt 必须提供 `resumeToken`
- resume 可以继续兼容老的 `resume_from_run_id + meta.resume`
- WRP v1 的推荐用法是 `resume_from_run_id + resume.token + resume.output`
- 服务端校验：
  - `resume.token.runId` 必须匹配 parent run
  - `resume.token.threadId` 必须匹配 parent thread
  - 若 interrupt 中存在 token，则 `nodeId` 必须匹配

## 11. UIAPP / Chat Flow 扩展位

当前仅定义扩展位，不把它们纳入本轮通用 workflow 交付基线。

### 11.1 UIAPP

- outward:
  - `ui.mount`
  - `ui.patch`
  - `ui.unmount`
- inward:
  - `ui.event.submit`
  - `ui.event.abort`

### 11.2 Chat Flow / AG-UI Profile

- `chat.event`
- `agent.event`
- `active-run.resolve` with explicit `scope`

## 12. 兼容窗口

- v0：
  - 旧式 ad hoc workflow events
  - 旧式 `resume_from_run_id + meta.resume`

- v1：
  - WRP canonical events
  - structured resume payload
  - `session.ready / run.attached / run.replay.completed / checkpoint.created`

兼容策略：

1. SSE / WS / replay adapter 继续兼容 legacy event names
2. 前端默认按 WRP v1 消费
3. 老式 resume 载荷继续可用，但不再作为推荐方式

## 13. 协议选择

当前 Workflow runtime transport 默认直接走 `wrp`，并在请求边界显式允许传入 `protocol`。

- HTTP blocking / async / debug / sse：
  - `WorkflowExecutionRequest.protocol`
  - 默认值 `wrp`
- HTTP replay / live：
  - query `protocol`
  - 默认值 `wrp`
- WebSocket control messages：
  - `protocol`
  - 默认值 `wrp`

因此 WRP v1 **不再要求每条消息显式携带 `spec` 字段**。协议归属由边界层的 `protocol` + transport + adapter selector 决定，而不是由消息内魔法字符串判断。

未来若新增：

- `chatflow-ag-ui`
- `uiapp-interactive`

则通过 protocol adapter 层选择，不回退到 per-message `spec` 判定。
