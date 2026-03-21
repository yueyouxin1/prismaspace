# Workflow资源协议与运行时评估报告（2026-03-20）

- 报告版本：v1.0
- 评估方式：基于当前工作树代码审查、现有文档比对与运行时契约梳理
- 核心结论：**Workflow 运行时本身已经进入 durable runtime 阶段，但对外协议层仍停留在“产品内自定义事件流”阶段。**
- 最终建议：**不要把 Workflow 原生协议直接等同于 AG-UI；应新增一套 Workflow 专用标准协议，并为 Agent 节点提供 AG-UI 兼容层，同时给未来 Chat Flow 预留扩展位。**
- 当前落地：**WRP v1 已固化为对内协议说明，见 `doc/workflow-runtime-protocol.md`。**

---

## 1. 结论先行

当前 PrismaSpace 的 Workflow 资源，不再是一个“只能跑图的轻量执行器”。

从运行时能力看，它已经具备：

- Runtime IR 编译
- durable run ledger
- durable event log
- node execution durable records
- checkpoint / resume
- interrupt / resume
- background async execute
- subworkflow lineage

也就是说，**运行时底座已经足够成熟，值得拥有一套正式协议。**

但从“协议化程度”看，当前 Workflow 仍明显弱于 Agent：

- HTTP / SSE / WebSocket 三条入口没有统一的公共协议模型
- 事件名、控制消息、resume 载荷、attach/replay 语义没有形成正式规范
- 许多能力只能通过阅读 `workflow_service.py`、`ws_handler.py`、节点实现与测试才能知道
- 这对于后续接 UIAPP、做 Chat Flow、做前端可视化调试台，都不是成熟做法

因此，本轮判断是：

- **需要立即把 Workflow 资源从“运行时实现”提升到“协议化资源”。**
- **AG-UI 可以复用，但不适合直接充当 Workflow 的原生总协议。**
- **推荐新建 Workflow Runtime Protocol（暂名 WRP），并为 Agent 节点提供 AG-UI profile 或 bridge；Chat Flow 现阶段仅预留扩展位，不纳入当前主计划。**

---

## 2. 本次评估范围

本次判断主要基于以下代码与文档边界：

### 2.1 文档依据

- `doc/ag-ui-guide.md`
- `doc/Agent资源评估报告.md`
- `doc/workflow_coze_vs_prismaspace_evaluation.md`

### 2.2 Workflow 侧代码依据

- `src/app/api/v1/workflow/workflow_api.py`
- `src/app/api/v1/workflow/ws_handler.py`
- `src/app/services/resource/workflow/workflow_service.py`
- `src/app/services/resource/workflow/runtime_runner.py`
- `src/app/services/resource/workflow/runtime_persistence.py`
- `src/app/services/resource/workflow/event_log_service.py`
- `src/app/engine/workflow/runtime_ir.py`
- `src/app/engine/workflow/orchestrator.py`
- `src/app/models/resource/workflow/event.py`
- `src/app/models/resource/workflow/runtime.py`

### 2.3 Agent / UIAPP 对照依据

- `src/app/api/v1/agent/agent_api.py`
- `src/app/api/v1/agent/ws_handler.py`
- `src/app/schemas/protocol/ag_ui.py`
- `src/app/api/v1/uiapp.py`
- `src/app/services/resource/uiapp/uiapp_service.py`

### 2.4 测试依据

- `tests/api/v1/test_workflow_runtime.py`
- `tests/services/resource/workflow/test_workflow_durable_runtime.py`
- `tests/services/resource/workflow/nodes/test_node_agui_streaming.py`

---

## 3. 当前现状判断

## 3.1 已经成立的优点

先明确，当前 Workflow 不是“没做 runtime”，而是：

- 已有独立 Runtime IR：`src/app/engine/workflow/runtime_ir.py`
- 已有 durable event log：`src/app/models/resource/workflow/event.py`
- 已有 durable checkpoint / node execution：`src/app/models/resource/workflow/runtime.py`
- 已有 resume 恢复：`src/app/services/resource/workflow/runtime_persistence.py`
- 已有 async background run：`src/app/api/v1/workflow/workflow_api.py` 的 `/{uuid}/async`
- 已有 replay / cancel / runs list / run detail
- 已有 interrupt 节点与恢复语义

这说明：

- **Workflow 运行时能力已经明显超过 MVP。**
- **真正的短板，不在引擎本体，而在协议层。**

## 3.2 当前对外协议层的真实形态

当前 Workflow 对外并没有一个正式命名、版本化、独立建模的协议。

它更接近下面这几套“混合契约”：

### HTTP Blocking

- 输入：`WorkflowExecutionRequest`
- 输出：`WorkflowExecutionResponse`
- 语义：直接等待到 finish / interrupt

### HTTP SSE

- 输入：同样还是 `WorkflowExecutionRequest`
- 输出：`SSEvent(event, data)` 的裸事件流
- 事件名：`start / node_start / node_finish / stream_chunk / interrupt / finish ...`

### WebSocket

- 输入：不是 `WorkflowExecutionRequest`，而是通用 `WSPacket`
- 控制动作：`run / stop`
- 运行参数：`packet.data.instance_uuid + packet.data.inputs`
- 输出：也是通用 `WSEvent(event, data, request_id)`

### Async Background

- 输入：同 `WorkflowExecutionRequest`
- 输出：只返回 run summary
- 无 live attach 标准流协议

也就是说：

- **同一个 Workflow 运行时，有 4 套外部交互形态。**
- **但这 4 套形态并没有被统一抽象成一套协议。**

---

## 4. 当前“协议感弱”的具体表现

这部分是本次评估最关键的结论。

## 4.1 事件词汇表是存在的，但没有被“协议化”

当前 Workflow durable 事件类型大致是：

- `start`
- `node_start`
- `node_finish`
- `node_error`
- `node_skipped`
- `stream_start`
- `stream_chunk`
- `stream_end`
- `interrupt`
- `error`
- `finish`
- `system_error`

这些事件已经够形成协议雏形，但现在的问题是：

- 只有代码里的枚举和回调实现，没有正式 spec
- 没有公开定义每个事件的必填字段
- 没有“哪些字段稳定、哪些字段可扩展”的规则
- replay 只是把数据库里的 `event_type + payload` 原样吐出去

因此当前的 durable event log 更像“内部审计记录”，不是“正式对外协议事件”。

## 4.2 控制面完全是 ad hoc 的

Workflow WebSocket 当前只支持：

- `action_run`
- `action_stop`

这不够支撑成熟 runtime 的控制面。

至少下面这些动作后续都会变成一等公民：

- `run.start`
- `run.attach`
- `run.cancel`
- `run.resume`
- `run.replay`
- `ui.event.submit`
- `ui.event.abort`

当前如果前端要做更复杂的 attach / reconnect / UIAPP 交互，只能继续在 `packet.data` 里加私有字段。这会继续放大协议漂移。

## 4.3 不同入口的输入模型不统一

当前：

- HTTP Blocking / HTTP SSE 用 `WorkflowExecutionRequest`
- WS 用 `WSPacket`
- resume 依赖 `resume_from_run_id + meta.resume`
- interrupt 节点又要求前端理解 `resume_output_key`

这使得“如何恢复一个被中断的 workflow”并不是一条显式协议，而是：

1. 看测试
2. 看 interrupt 节点实现
3. 读 `workflow_service._prepare_run_context`
4. 猜 `meta.resume` 应该长什么样

这显然不成熟。

## 4.4 Streaming run 仍然是连接绑定语义

当前 Workflow 的 SSE / WebSocket streaming run 有一个很关键的问题：

- SSE 断开时，会 cancel 运行中的 task
- WebSocket 断开时，也会 cancel 当前 task

这意味着当前 Workflow streaming 语义还是：

- **连接 = 执行生命周期**

而不是 Agent 那样的：

- **Run 生命周期独立于连接**
- **连接只是一种 attach/detach transport**

这对未来的：

- UIAPP 中途交互
- 页面刷新恢复
- chat flow 接回流
- 长任务后台执行 + 前端晚到附着

都不够。

## 4.5 缺少 run_id 级 live attach；但 active-run 不能直接照搬 Agent

Agent 已经有：

- `active-run`
- `/runs/{run_id}/live`
- WebSocket `ps.attach_run`

但 Workflow 不能简单照搬成：

- “先查 active-run，再决定是否 attach”

原因是：

- Agent 天然有会话/线程心智，`thread_id` 本身就是明确作用域
- 通用 Workflow workbench 只是构建与调试环境，并不天然存在唯一会话
- 即使服务端按 actor 过滤，**同一用户也可能同时跑多个 workflow run**
- 如果只按 `workflow uuid` 甚至宽泛 `thread_id` 查活跃 run，语义仍然可能模糊

因此更合理的判断应是：

- **通用 Workflow 场景，默认 reconnect 键应是 `run_id`，不是 `active-run`。**
- **Workflow 最需要先补的是 `/runs/{run_id}/live`，让前端拿着已知 run_id 接回流。**
- **`active-run` 只适用于“有显式交互 scope”的 Workflow profile。**

还需要补一条前端行为原则：

- **通用 workflow workbench 默认进入页面时，不应自动 attach 任意运行中的流。**
- **每次进入 workflow 编辑页，默认都应是干净的构建环境。**
- **只有当前 UI 上下文已经持有显式 `run_id` 时，前端才应进入 live attach。**

这里的“持有显式 `run_id`”包括：

- 用户刚刚在当前页面手动启动了一次 run
- 前端本地恢复了上一次明确保存的 run 上下文
- 用户点击了历史执行记录中某条仍在运行的 run

但不包括：

- 仅仅打开 workflow 编辑页面
- 仅仅知道当前 workflow uuid
- 仅仅知道“可能有人正在跑这个 workflow”

这里的“显式交互 scope”包括但不限于：

- Chat Flow 的 conversation/thread
- UIAPP 双向交互会话
- 未来某种明确的 `execution_scope_id / client_session_id`

也就是说：

- 通用 workflow workbench 不应该靠 `active-run` 猜测要接哪条流
- 应该靠“启动时已经拿到的 `run_id`”来 attach
- 默认进入页面时不应主动 attach
- 如果未来引入 workflow `active-run`，也必须要求显式 scope，不能只按 workflow uuid 查询

当前 Workflow 的问题因此应改写为：

- async 背景执行虽然存在
- durable event log 虽然存在
- 但“前端如何按已知 run_id 重新接回一个正在跑的 workflow”仍没有正式协议面
- 对交互型 profile 而言，也还没有显式 scope 下的 active-run 能力

这恰好会在 UIAPP 联动和未来 Chat Flow 场景里暴露成大问题。

## 4.6 当前事件词汇是混合的

Workflow 外层事件是：

- `start`
- `finish`
- `interrupt`
- `stream_chunk`

但 Workflow 内部 LLM / Agent 节点消费的事件却已经部分使用轻量 AG-UI 风格：

- `TEXT_MESSAGE_CONTENT`
- `REASONING_MESSAGE_CONTENT`
- `RUN_FINISHED`
- `RUN_ERROR`

这说明现在其实存在两套词汇：

- 一套是 workflow runtime 自己的事件词汇
- 一套是 agent/llm 子系统的 AG-UI 风格词汇

但它们没有被统一进一个“多 profile 协议模型”中。

这会直接导致：

- 前端要写很多兼容分支
- replay/debug UI 很难做统一 renderer
- workflow 后续若衍生 chat flow 时，边界会越来越乱

## 4.7 UIAPP 仍然只有资源 CRUD，没有运行时交互协议

当前 UIAPP 资源只有：

- 页面 DSL 获取
- 页面 CRUD
- 依赖同步

但没有：

- workflow -> uiapp 的 mount/render 协议
- uiapp -> workflow 的 interaction submit 协议
- 双向事件流协议
- uiapp session / interaction token / resume token 协议

所以你提出“workflow 后续要给前端发送 UIAPP DSL，完成双向交互”这件事，**目前代码里还没有一个合适的协议容器可承接。**

---

## 5. AG-UI 是否适合直接作为 Workflow 原生协议

结论先说：

- **AG-UI 很适合作为 Workflow 的子协议或兼容 profile。**
- **AG-UI 不适合直接成为 Workflow 的原生总协议。**

## 5.1 AG-UI 的优点

AG-UI 的优点非常明确：

- 已经是 event-driven 协议
- 已经适配 chat / agent / user-facing surface
- 已经具备 interrupt / tool-call / message streaming 语义
- 已经有现成 encoder、客户端、event type 心智
- 对未来 Chat Flow 非常友好

特别是你提到的：

- workflow 包含 agent 节点
- workflow 未来可能衍生 chat flow

这两点都说明：

- **Workflow 不可能无视 AG-UI。**

## 5.2 但 AG-UI 直接做 Workflow 总协议会遇到的问题

AG-UI 的核心抽象是：

- agent 与用户的交互协议

而 Workflow 的核心抽象是：

- graph runtime
- node lifecycle
- execution governance
- durable replay / attach
- interrupt / resume
- subworkflow lineage
- UIAPP mounting / submit

也就是说，Workflow 比 AG-UI 多了很多“执行引擎语义”。

如果强行把 Workflow 原生协议直接做成 AG-UI，会出现两个问题：

### 1. 必然大量依赖 CUSTOM 事件

因为下面这些并不是 AG-UI 的核心标准面：

- node.started / node.completed / node.skipped
- checkpoint.created
- subworkflow.started
- uiapp.mount / uiapp.submit
- graph debug / replay / attach

一旦大部分关键能力都落到 custom event，AG-UI 在 Workflow 场景里就只是一个 transport shell，不再是真正的标准协议。

### 2. 会把 Workflow 的运行时治理语义弱化成“像 agent 一样的消息流”

这会导致：

- durable runtime 的优势表达不出来
- node 级状态查询很难标准化
- 调试台/审计台难以直接消费
- 工作流和 chat flow 的边界被混在一起

所以：

- **Workflow 不能只站在 chat surface 的视角设计协议。**
- **还必须站在 runtime control plane 的视角设计协议。**

---

## 6. 三条路线的评估

| 路线 | 判断 | 结论 |
|---|---|---|
| 继续维持当前自定义事件 + 局部补丁 | 成本最低，但会继续让契约散落在 API/Service/测试里 | 不建议 |
| 直接把 Workflow 原生协议全面改成 AG-UI | 对 chat flow 友好，但会把大量 workflow 语义塞进 custom event | 不建议作为原生总协议 |
| 新建 Workflow 专用协议，并为 chat-flow / agent-node 提供 AG-UI bridge | 同时保留 runtime 正确性和 chat 生态兼容性 | 强烈建议 |

本轮推荐的就是第三条路线。

---

## 7. 推荐方案

## 7.1 总体建议

建议新增：

- **Workflow Runtime Protocol（暂名 WRP）**

并明确分成三层：

### A. WRP Core

用于表达 workflow 本身的运行时控制与事件流：

- run start / attach / cancel / resume
- lifecycle events
- node lifecycle
- stream lifecycle
- checkpoint / replay / live attach
- interrupt / resume contract

### B. WRP-UI 扩展

用于 workflow 与 UIAPP 的双向交互：

- ui.mount
- ui.patch
- ui.unmount
- ui.event.request
- ui.event.response
- interaction token / resume token

### C. AG-UI Compatibility Profile（预留）

用于未来：

- chat flow
- agent 节点输出
- workflow 的 conversational surface

换句话说：

- **Workflow 原生协议 = WRP**
- **Agent-facing surface = AG-UI profile**
- **Chat Flow = 未来可接入的 profile，不是当前主计划**

这比“Workflow = AG-UI”更正确，也更稳定。

---

## 8. 为什么这个方案最适合你提的 4 个理由

## 8.1 Workflow 天然包含 agent 节点

这不意味着 Workflow 应该等于 Agent。

更合理的做法是：

- Workflow 外层仍保持 workflow-native protocol
- 某个节点内部如需暴露 agent/chat surface，则该节点 payload 可携带 AG-UI profile 事件

即：

- **Workflow 是容器协议**
- **AG-UI 是子协议 / profile**

## 8.2 Workflow 后续要和 UIAPP 联动

这点几乎直接决定了：

- 不能只用 AG-UI 原样照搬

因为 UIAPP 联动需要的不是纯消息流，而是：

- mount 某个 UIAPP
- 下发 DSL / props / state
- 接收前端 interaction result
- 继续恢复 workflow 执行

这更像：

- workflow runtime + generative UI runtime

所以必须有专用的 UIAPP 事件类型。

## 8.3 Workflow 后续若要衍生 Chat Flow

这点说明：

- WRP 必须支持 chat profile

但“支持 chat profile”不等于“原生协议直接只剩 AG-UI”。

更合理的是：

- Chat Flow 更适合作为 Workflow 的一种运行 profile
- 该 profile 对外暴露 AG-UI 兼容事件流
- 内部仍保留 WRP 的 node/runtime/control 语义

但这里要明确一个现实判断：

- **Chat Flow 是较大的后续迭代，不建议放入当前收口阶段的主计划。**

当前更合适的策略是：

- 先把 Chat Flow 所需的协议扩展位预留出来
- 先把 run-level attach、UIAPP interaction、canonical event envelope 做正确
- 等后续进入 Chat Flow 专项迭代，再把 AG-UI profile 正式接上

## 8.4 当前 workflow 协议感太弱

这个问题的根因不是“没有事件流”，而是：

- **没有正式 protocol package**
- **没有 versioned event envelope**
- **没有 capability / control plane**
- **没有 public spec**

WRP 刚好就是为了解这个问题。

---

## 9. 目标协议应该长什么样

## 9.1 协议目标

建议 WRP 满足以下原则：

- transport-agnostic
- event-driven
- bidirectional
- versioned
- capability-declared
- durable-friendly
- replay/live-attach friendly
- UI-interaction friendly
- AG-UI bridge friendly

## 9.2 统一事件信封

建议不再只用当前的：

- `event`
- `data`

而是引入统一 envelope，例如：

```json
{
  "spec": "prismaspace.workflow.runtime/v1",
  "type": "node.completed",
  "seq": 18,
  "ts": "2026-03-20T12:00:00Z",
  "runId": "run_xxx",
  "parentRunId": "run_parent",
  "traceId": "trace_xxx",
  "scope": {
    "kind": "chat-thread",
    "id": "thread_xxx"
  },
  "node": {
    "id": "node_1",
    "registryId": "LLM",
    "name": "Generate Draft"
  },
  "payload": {
    "output": {
      "text": "..."
    }
  }
}
```

关键点：

- `spec` 明确协议版本
- `type` 明确事件类型
- `seq` 支撑基于 `run_id` 的 attach/replay 补流
- `runId/traceId` 成为所有事件的一等字段
- `scope` 只在交互型 profile 下出现，不应强制所有 workflow run 都有 `threadId`
- `node` 信息不再散落在 payload 里

## 9.3 核心事件类型建议

建议至少定义以下标准事件：

### 生命周期

- `run.started`
- `run.finished`
- `run.failed`
- `run.cancelled`
- `run.interrupted`

### 节点

- `node.started`
- `node.completed`
- `node.failed`
- `node.skipped`

### 流式

- `stream.started`
- `stream.delta`
- `stream.finished`

### 恢复与治理

- `checkpoint.created`
- `run.attached`
- `run.detached`
- `run.replay.completed`

### UIAPP 扩展

- `ui.mount`
- `ui.patch`
- `ui.unmount`
- `ui.submit.request`
- `ui.submit.received`

### Bridge / Profile

- `agent.event`
- `chat.event`

其中：

- `agent.event` 的 `payload` 可以直接承载 AG-UI event
- `chat.event` 可以在未来 Chat Flow profile 下映射为 AG-UI outward stream

## 9.4 统一控制消息建议

建议 WebSocket 与未来双向流统一成以下控制动作：

- `run.start`
- `run.attach`
- `run.cancel`
- `run.resume`
- `ui.event.submit`
- `ui.event.abort`

补充说明：

- `run.attach` 默认应按 `run_id` 进行
- `active-run.resolve` 只有在显式 scope profile 下才有意义

不建议继续长期保留仅有的：

- `run`
- `stop`

因为这无法承接 live attach / UIAPP 互动。

## 9.5 能力声明

建议在 run start 或握手阶段返回 capabilities，例如：

```json
{
  "spec": "prismaspace.workflow.runtime/v1",
  "type": "session.ready",
  "payload": {
    "capabilities": [
      "interrupt",
      "resume",
      "replay",
      "live_attach",
      "ui_mount",
      "ag_ui_bridge"
    ]
  }
}
```

这样前端不需要再通过“试错”判断某个 workflow surface 支持什么。

---

## 10. 对当前代码的具体调整建议

## 10.1 协议建模层

建议新增：

- `src/app/schemas/protocol/workflow_runtime.py`
- `src/app/schemas/protocol/workflow_ui.py`

职责：

- 定义 run input / control message / event envelope / UIAPP interaction schema
- 把当前散落在 `workflow_api.py`、`ws_handler.py`、`workflow_service.py` 的契约收敛出来

## 10.2 协议适配层

建议新增：

- `src/app/services/resource/workflow/protocol_adapter/`

至少包括：

- `http_sse.py`
- `websocket.py`
- `ag_ui_bridge.py`

职责：

- 不让 `workflow_service.py` 继续直接承担协议拼装职责
- Service 只产出 canonical workflow events
- adapter 再映射到 SSE / WS / AG-UI profile

## 10.3 API 面补齐

建议优先给 Workflow 补齐：

- `GET /api/v1/workflow/runs/{run_id}/live`

对于 `active-run`，建议改成条件性能力：

- 仅在交互型 profile 下提供，例如：
  - `GET /api/v1/workflow/{uuid}/active-run?scope_id=...`
  - 或 `GET /api/v1/workflow/{uuid}/active-run?thread_id=...`

并要求：

- scope 必须由客户端显式提供
- 服务端必须校验 actor + scope 的归属
- 绝不能仅按 workflow uuid 推断“当前该接哪条流”

这样才能把 workflow 真正从“连接绑定 streaming”升级到“run 独立于连接”，同时避免把 Agent 会话心智错误套到通用 Workflow 场景。

对于前端默认行为，建议同时明确：

- workflow 编辑页加载时，只拉资源定义、graph、runs/history 等静态/查询面
- 不自动拉 live stream
- 只有用户显式选择某个运行中的 `run_id`，或当前页面刚刚启动过该 run，才 attach live

## 10.4 WebSocket 面重构

当前 `WorkflowSessionHandler` 需要从：

- 通用 `WSPacket`
- `action_run / action_stop`

升级成：

- versioned control messages
- attach / resume / cancel / ui submit
- capability-aware session

但这里的 session 也要限定为：

- 交互型 profile 的显式 session/scope

而不是给所有 workflow workbench 强行引入 Agent 式会话心智。

## 10.5 Event Log 模型调整

当前 event log 已经有 durable 基础，但建议语义升级为：

- `event_type` 存 canonical `type`
- `payload` 存 envelope payload
- 补 `protocol` / `protocol_version` 或在 payload 里固化 `spec`

否则未来协议升级时，历史 replay 很容易出现前后不兼容。

## 10.6 UIAPP Bridge

建议新增 workflow -> uiapp interaction bridge，至少明确：

- 哪个节点触发 UIAPP
- 下发哪个 UIAPP instance/page
- DSL 是否全量下发还是引用下发
- interaction result 如何回传
- 回传后如何 resume workflow

这块如果不先协议化，后面很容易把 UIAPP 交互写死在某个节点实现里。

---

## 11. 建议的阶段性落地路径

## Phase 1：先把当前 v0 行为固化成正式 spec

目标：

- 不改运行时大逻辑
- 先把现有事件和控制面抽成正式文档与 schema

交付物：

- WRP v1 草案
- canonical event envelope
- workflow v0 -> v1 adapter

## Phase 2：补 run_id 级 live attach / reconnect

目标：

- 让 workflow run 生命周期从连接中独立出来
- 让通用 workflow 前端可以按 `run_id` 重新接回流
- 仅为未来交互型 profile 预留 scoped active-run 能力

交付物：

- `/runs/{run_id}/live`
- live attach SSE
- WebSocket attach
- seq-based replay 补流
- scoped active-run 预留设计（仅交互型 profile）

## Phase 3：补 UIAPP interaction extension

目标：

- workflow 能驱动前端渲染 UIAPP DSL
- 前端能将 interaction result 回传并 resume

交付物：

- `ui.mount / ui.submit.request / ui.submit.received / ui.unmount`
- interaction token / resume token

## Phase 4：占位：Chat Flow / AG-UI Profile（大版本迭代）

当前判断：

- 暂不纳入本轮执行范围
- 当前只需要预留协议扩展位

预期目标：

- 引入 conversation/thread 作用域
- 对外暴露 AG-UI 兼容事件流
- 支持 conversational interrupt / resume / replay / reconnect
- 支持 UIAPP 在对话流中的挂载与提交

未来交付物：

- chat-flow outward AG-UI adapter
- agent.event / chat.event bridge
- 前端复用现有 AG-UI client

---

## 12. 是否需要现在就“完全公开标准”

我的建议是：

- **现在先做平台内标准，不必急着对外宣称开放标准。**

原因：

- 当前最需要解决的是内部一致性与前后端协作问题
- 先稳定一版 WRP，再考虑是否开放
- 如果现在就强行对外公开，而协议还在快速变化，反而会拖慢演进

更现实的节奏是：

1. 先在 PrismaSpace 内部把 WRP 跑通
2. 先让 Workflow / UIAPP 两条线使用它
3. Chat Flow 在后续大版本专项迭代中接入
4. 再评估是否抽象成对外标准

---

## 13. 风险与兼容策略

## 13.1 主要风险

- 现有前端如果直接依赖 `start/node_start/finish` 裸事件，需要迁移
- 当前 WS 前端如果直接依赖 `action=run/stop`，需要兼容期
- interrupt/resume 载荷会从 ad hoc 结构升级成正式 schema

## 13.2 兼容策略

建议保留一个兼容窗口：

- 现有 `/execute`、`/sse`、`/ws` 保留为 v0 adapter
- 新增 v1 protocol schema 与 live attach 能力
- replay 优先输出 v1 envelope
- 必要时保留旧事件名映射

这样可以避免一次性打断所有前端。

---

## 14. 最终裁决

### 14.1 当前 Workflow 是否需要协议化收口

**需要，而且已经到了必须做的时候。**

因为当前 runtime 已经不是“太早谈协议”，而是：

- runtime 已经够成熟
- 协议层反而明显落后

### 14.2 是否应直接采用 AG-UI 作为 Workflow 原生协议

**不建议。**

AG-UI 应该被复用，但更适合：

- Chat Flow profile
- Agent 节点 bridge
- conversational surface

而不是直接统治整个 workflow runtime。

### 14.3 最推荐的方向

**新增 Workflow 专用标准协议（暂名 WRP），并提供 AG-UI 兼容层。**

这是当前最符合 PrismaSpace 长期演进的路线，因为它同时满足：

- workflow 作为执行引擎的正确性
- UIAPP 双向交互的扩展性
- chat flow 的 AG-UI 兼容性
- durable runtime 的治理表达能力

---

## 15. 一句话结论

**Workflow 当前缺的不是运行时能力，而是正式协议层。**

**正确方向不是“让 Workflow 直接变成 AG-UI”，而是“建立 Workflow 原生协议，并把 AG-UI 作为 Chat/Agent profile 接进去”。**
