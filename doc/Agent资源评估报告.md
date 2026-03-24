# Agent资源评估报告（2026-03-10 Agent生产化收口版）

- 报告版本：v6.2
- 评估方式：基于当前工作树代码审查、架构收口、定向回归测试与现有 Agent 文档更新
- 当前结论：**Agent 资源已经达到生产级主链路水准，可按生产基线验收；但仍不单独宣称“极致性能已证明”或“已完全等同 Coze 全平台”。**

---

## 0. 2026-03-24 持久化粒度复核结论

在 2026-03-21 热路径复核之后，本轮又对 Agent 的 durable event 粒度和 Resource Runtime 统一心智做了一轮收口，目标不是改变 AG-UI 行为，而是让 durable 数据面只保留“最小回放周期有价值”的事件。

本轮已落地的点：

- Agent 流热路径仍保持原有模型：
  - `event_sink` / live buffer
  - `generator_manager.put(...)`
  - 运行结束后批量持久化
- durable event 现已改为**显式白名单**
- 以下高频事件继续实时发送，但不再进入 durable event log：
  - `TEXT_MESSAGE_CONTENT`
  - `REASONING_MESSAGE_CONTENT`
  - `TOOL_CALL_ARGS`
  - `ACTIVITY_DELTA`
  - `STATE_DELTA`
- `active-run`、`/live`、断连不 cancel、checkpoint 恢复、session 锁等既有生产语义保持不变

当前判断：

- **Agent 的热路径生产语义没有回退**
- **Agent durable event 数据量较之前更可控**
- **本轮改动属于“减冗余而不降能力”的收敛**

---

## 0. 2026-03-21 热路径复核结论

在 2026-03-10 的生产化收口之后，本轮又对 Agent 的“首事件前置路径”和 live attach 路径做了一轮只动内部实现的优化，且不改 AG-UI 协议与前端契约。

本轮已落地的点：

- Agent 启动时改走 runtime 专用 instance loader，不再为执行路径默认预加载 `linked_feature -> product -> prices -> tiers`
- prepare 阶段不再提前查询 dependencies，改为仅在真正需要构建 pipeline 时按需加载
- resume 路径不再在 execution 阶段重复查 parent run checkpoint，prepare 阶段已恢复并透传 `resume_checkpoint`
- live attach 的 Redis 读取不再每轮全量 `LRANGE 0 -1`，改为按尾部窗口读取
- 高频事件的 cancel 检查增加了短周期本地缓存，避免每个 token/event 都打一趟 Redis

当前仍保留、且判断为必要的逻辑：

- `session` 级写锁
- `active-run` 预检查
- AG-UI protocol adapter
- live buffer detach 后再刷 Redis 的设计

这意味着：

- Agent 主链路仍保持既有生产语义
- 首事件前置路径和 live attach 路径比 3 月 10 日版本更轻
- 但依然不把这份报告表述成“性能已证明达到极致”

---

## 1. 本轮最终结论

截至 2026 年 3 月 10 日，PrismaSpace `Agent` 已经从“可用的产品级 AG-UI Agent 服务”推进到“具备生产级运行治理、恢复补强与断连重接能力的 Agent Runtime”。

当前已经明确成立的判断是：

- 保留 AG-UI 协议，不破坏现有前后端契约
- 具备 durable `run / event timeline / replay / cancel / checkpoint`
- 具备 interrupt / resume 的生产级恢复补强
- 具备断连后后台继续执行，以及显式 `active-run -> live` 重接流能力
- 具备生命周期结束后的 checkpoint / live buffer 清理策略
- 仅保留必要的 `session` 写入锁，不再额外引入更重的 `run` 锁

换句话说：

- **当前已经达到生产主链路可用**
- **当前仍有继续演进空间，但不再属于“缺基础能力”的阶段**

---

## 2. 当前架构判断

### 2.1 已保留的正确约束

- 保留 `AgentService` 兼容门面，但重职责已拆到：
  - `run_preparation.py`
  - `run_execution.py`
  - `run_query.py`
  - `run_persistence.py`
  - `run_control.py`
  - `live_events.py`
- 保留 `session 锁` 作为唯一的会话写入保护，避免污染模型上下文
- 保留 `active-run` 查询面，作为前端恢复与状态判断的统一入口
- 保留 `live event buffer`，只承担短期重接流，不承担长期历史存储

### 2.2 已去除的不必要复杂性

- 去掉了额外的 `run 锁`
- 去掉了只为 `run 锁` 服务的通用 Redis lock 抽象
- `active-run` 仍在服务端做普通检查，但不再强行把 thread 语义锁死成“永远只能有一个 run”

这样做的原因是：

- 当前真正必须被保护的是“会话写入一致性”，不是“永不允许并发 run”
- 前端本来就会先查 `active-run` 再决定 `/live` 或 `/sse`
- 未来若支持“长任务运行中追加输入”，当前结构仍留有演进空间

---

## 3. 当前生产能力集（合并自《Agent资源能力清单》）

### 3.1 协议与接入能力

- 输入协议统一为 `RunAgentInputExt`
- 保留 AG-UI 事件协议，不破坏既有流式格式
- 支持：
  - `POST /api/v1/agent/{uuid}/execute`
  - `POST /api/v1/agent/{uuid}/sse`
  - `WS /api/v1/agent/chat`
- 支持：
  - `forwardedProps.platform.sessionMode`
  - `forwardedProps.platform.protocol`
  - `forwardedProps.platform.agentUuid`

### 3.2 Run 治理能力

- 所有 Agent 执行都挂到平台级 `resource_executions`
- 具备：
  - `run_id`
  - `thread_id`
  - `parent_run_id`
  - `trace_id`
  - `status`
  - `started_at / finished_at`
- 提供：
  - `GET /api/v1/agent/{uuid}/runs`
  - `GET /api/v1/agent/{uuid}/active-run?thread_id=...`
  - `GET /api/v1/agent/runs/{run_id}`
  - `GET /api/v1/agent/runs/{run_id}/events`
  - `GET /api/v1/agent/runs/{run_id}/replay`
  - `GET /api/v1/agent/runs/{run_id}/live`
  - `POST /api/v1/agent/runs/{run_id}/cancel`

### 3.3 Event / Tool History

- `ai_agent_run_events` 持久化 AG-UI 关键事件时间线
  - 当前已明确**不再追求 token/chunk 级 durable 镜像**
  - durable event 以“最小回放周期有价值”的事件为准
- `ai_agent_tool_executions` 持久化 tool/step 执行历史
- 可用于：
  - run detail
  - replay
  - 故障排查
  - 审计与恢复辅助

### 3.4 Cancel / Control

- 支持 Redis cancel signal
- 支持进程内 `AgentRunRegistry`
- WebSocket 支持：
  - `ps.cancel_run`
  - `ps.attach_run`

---

## 4. 中断 / 恢复 / 回传能力

### 4.1 当前 Checkpoint 内容

当前 `AgentRunCheckpoint` 持久化以下运行态信息：

- `run_input_payload`
- `adapted_snapshot`
- `runtime_snapshot`
- `pending_client_tool_calls`

其中真正作为恢复主依据的是：

- `runtime_snapshot.messages`
- `runtime_snapshot.tools`
- `runtime_snapshot.pending_client_tool_calls`

这使恢复不再主要依赖查消息库重组上下文。

### 4.2 当前恢复语义

当前 resume 时：

1. 通过 `interruptId / parent_run_id` 找到 parent run
2. 校验 parent run 必须处于 `INTERRUPTED`
3. 基于 checkpoint 严格校验 `pending_client_tool_calls`
4. 优先使用 checkpoint 中冻结的 `messages/tools` 恢复上下文

当前判断应表述为：

- **已具备生产级恢复补强**
- **已显著优于“纯 message 查库重组”**
- **仍未完全达到 Coze 那种彻底的执行栈原地恢复**

---

## 5. 断连 / 重连 / 接回流能力

### 5.1 当前断连语义

- SSE 断连不会自动 cancel run
- WebSocket 断连不会自动 cancel run
- 后台 run 会继续执行

### 5.2 当前重连语义

当前已经收口为**显式两步走**：

#### HTTP / SSE

1. 前端先请求 `GET /api/v1/agent/{uuid}/active-run?thread_id=...`
2. 如果存在活跃 run，再请求 `GET /api/v1/agent/runs/{run_id}/live`
3. 如果不存在活跃 run，再请求 `POST /api/v1/agent/{uuid}/sse`

也就是说：

- `/sse` 现在只负责新建 run
- `/live` 负责接回未完成 run 的流事件

#### WebSocket

1. 前端先检查当前会话是否存在 active run
2. 如存在，则通过 `CUSTOM ps.attach_run` 显式附着 live stream
3. 如不存在，再发送新的 `RunAgentInputExt`

这比“连接即自动隐式附着”更清晰，也更符合前端页面恢复心智。

### 5.3 Live Event Buffer

当前 live event buffer 基于 Redis，职责是：

- 跨进程共享短期 live 事件
- 支撑断线后补流
- 支撑显式 live attach

它不承担长期历史存储；长期历史仍以 run events 持久化表为准。

---

## 6. 生命周期结束后的清理策略

### 6.1 Checkpoint

当前策略：

- `RUNNING / INTERRUPTED` 保留 checkpoint
- `SUCCEEDED / FAILED / CANCELLED` 终态直接删除 checkpoint

因此：

- 不会让终态 run 持续堆积完整 checkpoint

### 6.2 Live Buffer

当前策略：

- 正常运行阶段使用常规 TTL
- 终态事件后自动缩短 TTL
- 过期后自动回收

因此：

- live buffer 只用于短期恢复接流
- 不会长期膨胀成历史存储层

### 6.3 长期历史依赖

长期调试与审计主要依赖：

- run ledger
- run event timeline
- tool/step execution history
- messages/session
- trace

---

## 7. 当前验证结果

### 7.1 本轮定向回归

执行命令：

```bash
poetry run pytest tests/api/v1/agent/test_agent_ag_ui_sse.py tests/api/v1/agent/test_agent_ws_handler.py tests/services/resource/agent/test_ag_ui_agent_service.py tests/services/resource/execution/test_execution_ledger_service.py tests/api/v1/agent/test_agent_run_api.py tests/api/v1/agent/test_agent_run_persistence_api.py tests/api/v1/agent/test_agent_session_api.py tests/services/resource/agent/test_runtime_production_guards.py -q
```

结果：

- `57 passed`

说明：

- 本轮主要覆盖的是 reconnect 语义收口、active-run/live 查询、checkpoint 恢复主链路、execution ledger active 判定与冗余锁清理后的行为稳定性。

---

## 8. 当前验收判断

### 8.1 已可按生产基线验收的项

- Agent 主执行链路
- execution ledger / turn lineage / terminal event 时序一致性
- session 写入一致性
- checkpoint 恢复补强
- active-run / live / replay / cancel
- 断连后后台继续执行
- reconnect 显式接回流
- tool history / event timeline
- Deep memory / RAG / Resource-as-Tool 主链路
- AG-UI 协议兼容性

### 8.2 当前不单独作结论的项

- 吞吐、延迟、背压方面的独立性能基准
- “极致性能”营销口径
- “已完全超越 Coze 全平台”的口径

### 8.3 综合结论

- 结论：**当前 Agent 资源可按生产级主链路通过验收。**
- 补充：**当前已经合理符合 Coze Agent 的核心生产主链路水准，但仍未完全等同 Coze 的完整平台能力。**

---

## 9. 尚未完全做满的点

当前仍值得继续演进，但已不构成“生产主链路阻塞”的点主要是：

- 更彻底的 engine-level canonical checkpoint
- 更完整的 Agent / Workflow 统一执行底座
- 更成熟的 Trace / 调试主视图
- 更系统化的 retention / archival / compaction 策略
- 更完整的平台化 product surface（connector / publish / openapi）

---

## 10. 收尾项

- 清理代码库剩余的 `datetime.utcnow()` 弃用告警
- 如未来要求更强的异步投递保证，可继续升级 outbox + retry/repair
- 如未来要求更强的运行中交互，可在当前显式 reconnect 模型上继续设计“运行中追加输入”
- 如未来要对外宣称“极致性能”，补充吞吐/延迟/背压压测与基准报告
