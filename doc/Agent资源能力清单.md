# Agent资源能力清单（2026-03-09）

本文档基于当前工作树代码，整理 PrismaSpace `Agent` 资源的**现状能力集**、**运行治理能力**、**协议能力**、**恢复能力**与**当前边界**，用于产品、研发和运维统一对齐。

---

## 1. 总体定位

当前 `Agent` 资源已经不是单纯的“聊天接口封装”，而是一个具备以下能力的生产级 Agent Runtime：

- AG-UI 协议驱动
- 支持 `HTTP Execute / SSE / WebSocket`
- 支持 `run / event timeline / replay / cancel / checkpoint`
- 支持 `Session / Stateless / Stateful` 三种会话语义
- 支持 `DeepMemory / RAG / Resource-as-Tool`
- 支持请求断开后后台继续执行与自动附着活动运行

---

## 2. 协议与接入能力

### 2.1 输入协议

当前统一使用 `RunAgentInputExt` 作为 AG-UI 协议输入，核心字段包括：

- `threadId`
- `runId`
- `messages`
- `tools`
- `context`
- `forwardedProps.platform`
- `resume`

### 2.2 输出协议

当前保留 AG-UI 事件流，不改变既有协议格式，支持：

- `RUN_STARTED`
- `TEXT_MESSAGE_*`
- `REASONING_*`
- `TOOL_CALL_*`
- `STEP_*`
- `RUN_FINISHED`
- `RUN_ERROR`
- `CUSTOM`

### 2.3 支持的传输方式

- `POST /api/v1/agent/{uuid}/execute`
  - 阻塞执行，返回 AG-UI 事件列表

- `POST /api/v1/agent/{uuid}/sse`
  - 流式执行 / 自动附着活动运行

- `WS /api/v1/agent/chat`
  - WebSocket 双向交互 / 自动附着活动运行

---

## 3. 运行治理能力

### 3.1 Run Ledger

当前所有 Agent 执行都挂在平台级 `resource_executions` 上，具备：

- `run_id`
- `thread_id`
- `parent_run_id`
- `status`
- `trace_id`
- `started_at / finished_at`

### 3.2 Run Query 面

支持：

- `GET /api/v1/agent/{uuid}/runs`
- `GET /api/v1/agent/runs/{run_id}`
- `GET /api/v1/agent/runs/{run_id}/events`
- `GET /api/v1/agent/runs/{run_id}/replay`
- `POST /api/v1/agent/runs/{run_id}/cancel`
- `GET /api/v1/agent/{uuid}/active-run?thread_id=...`
- `GET /api/v1/agent/runs/{run_id}/live`

### 3.3 Event Timeline

每次 run 的 AG-UI 关键事件都会持久化到 `ai_agent_run_events`，可用于：

- run detail 时间线
- 历史 replay
- 问题排查
- 断连后重新附着

### 3.4 Tool / Step Execution History

每次 tool call / step 都会持久化到 `ai_agent_tool_executions`，记录：

- `tool_call_id`
- `tool_name`
- `status`
- `step_index`
- `thought`
- `arguments`
- `result`
- `error_message`

### 3.5 Cancel Signal

支持 Redis cancel signal：

- 本地进程内 `AgentRunRegistry`
- Redis key 作为跨进程 cancel substrate

---

## 4. 会话与上下文能力

### 4.1 Session 模式

支持：

- `auto`
- `stateless`
- `stateful`

### 4.2 Session 持久化

持久化：

- `AgentSession`
- `AgentMessage`
- `run_id / turn_id / trace_id`
- `tool_calls / tool_call_id`
- `reasoning_content`
- `encrypted_value`

### 4.3 Context Pipeline

当前上下文构建能力包括：

- 用户输入
- 自定义 history
- resume messages
- AG-UI context
- prompt variables
- short context
- RAG context
- deep memory recall

### 4.4 Deep Memory

支持：

- 向量召回
- 摘要召回
- 后台索引
- 后台摘要

---

## 5. 恢复能力（Checkpoint / Resume）

### 5.1 当前 Checkpoint 内容

当前 `AgentRunCheckpoint` 持久化：

- `run_input_payload`
- `adapted_snapshot`
- `runtime_snapshot`
- `pending_client_tool_calls`

其中 `runtime_snapshot` 已包含更接近模型真实看到的上下文：

- `messages`
- `tools`
- `pending_client_tool_calls`

### 5.2 Resume 语义

当前 resume 时：

1. 先根据 `interruptId / parent_run_id` 找到 parent run
2. 校验 parent run 必须处于 `INTERRUPTED`
3. 基于 checkpoint 校验 `pending_client_tool_calls` 是否被完整回传
4. 优先使用 checkpoint 中冻结的 runtime snapshot 恢复上下文

### 5.3 当前能力边界

当前已显著优于“纯 message 查库重组”，但还不是最理想的完整执行栈恢复。

当前更准确的定位是：

- **已具备生产级恢复补强**
- **仍可继续演进到更彻底的 engine-level canonical checkpoint**

---

## 6. 断连与重连能力

### 6.1 断连后是否继续执行

当前：

- SSE 断连不会自动 cancel run
- WebSocket 断连不会自动 cancel run

也就是说：

- 请求方断开后，后台 run 仍会继续执行

### 6.2 重连后是否可继续接流

当前：

- `/sse` 是统一入口
- 如果同一 `threadId` 下存在 active run，会自动附着到该 run
- 会先从 live buffer 补到最新，再继续推后续事件

WebSocket 也已支持自动附着 active run。

### 6.3 Live Event Buffer

当前 live event buffer 基于 Redis，特点：

- 跨进程共享
- 有界缓存
- 支持重连后补流

当前策略：

- `MAX_BUFFERED_EVENTS`
- 正常 TTL
- 终态后缩短 TTL

---

## 7. 生命周期结束后的清理策略

### 7.1 Live Buffer

终态 run：

- live buffer 不会永久保留
- TTL 缩短后自动过期

### 7.2 Checkpoint

当前策略：

- `RUNNING / INTERRUPTED` 保留 checkpoint
- `SUCCEEDED / FAILED / CANCELLED` 终态直接删除 checkpoint

因此：

- 不会因终态 run 长期堆积完整 checkpoint 而膨胀

### 7.3 长期历史数据

长期调试与审计主要依赖：

- run ledger
- run event timeline
- tool/step execution history
- messages/session
- trace

---

## 8. 当前架构拆分

当前 Agent 运行相关职责已拆成：

- `agent_service.py`
  - 兼容门面

- `run_preparation.py`
  - 协议适配 / session 绑定 / execution 创建 / run 初始化

- `run_execution.py`
  - 后台执行协调 / checkpoint / terminal cleanup

- `run_query.py`
  - run 查询 / replay / active run / cancel

- `run_persistence.py`
  - run events / tool history / checkpoint 持久化

- `run_control.py`
  - cancel signal / local registry

- `live_events.py`
  - 运行中 live event buffer 与附着

---

## 9. 当前已达到的生产水准

### 9.1 已满足

- 生产级 run 治理
- event timeline / replay
- tool/step history
- cancel signal
- checkpoint 恢复补强
- 断连后后台继续执行
- 重连后自动附着 active run
- AG-UI 协议稳定兼容

### 9.2 尚未完全终局

- 更彻底的 engine-level canonical checkpoint
- 更完整的 Agent / Workflow 统一执行底座
- 更重的平台化 product surface（connector / publish / openapi）

---

## 10. 结论

当前 `Agent` 资源已经具备：

- 可执行
- 可治理
- 可中断
- 可恢复
- 可回放
- 可重连附着
- 可清理生命周期缓存

从“生产主链路可用”的标准看，当前已经达标。

从“完全等同或超越 Coze 全平台能力”的标准看，当前仍有继续演进空间，但已经不再属于“缺基础能力”的阶段。
