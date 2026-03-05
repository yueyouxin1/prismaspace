# Agent资源评估报告（全链路审计版）

- 报告版本：v1.0
- 评估范围：`Agent API/SSE/WebSocket`、协议适配、引擎、会话记忆、计费、Worker、`Workflow AgentNode` 集成
- 评估方法：静态代码审计 + 协议对照 + 自动化测试复核
- 评估基线：
  - `./.venv/bin/pytest -q tests/services/resource/agent/test_ag_ui_agent_service.py tests/services/resource/agent/test_persisting_callbacks_reasoning.py tests/engine/agent/test_agent_ag_ui_interrupt.py tests/api/v1/agent/test_agent_ag_ui_sse.py`
  - 结果：`36 passed`
  - `./.venv/bin/pytest -q tests/api/v1/e2e/test_agent_full_suite.py`
  - 结果：`4 failed, 1 passed`
  - 备注：失败用例主要基于“任意 threadId/无状态直接运行”的测试假设，与平台“会话必须由平台显式创建”的安全策略不一致。

---

## 1. 执行摘要

### 1.1 总体结论

- 总体评级：**基本通过但有风险**
- 是否达到生产门槛：**核心会话链路达到门槛，深度记忆链路未达门槛**
- 是否全面支持 AG-UI：**基本支持（在平台安全会话约束下），但仍有能力闭环与文档化缺口**

### 1.2 结论依据（高层）

- 优势：
  - Agent 主链路已具备清晰分层：协议适配 -> 流水线 -> 引擎 -> 回调事件 -> 持久化。
  - AG-UI 核心事件（`RUN_*`、`TEXT_*`、`TOOL_CALL_*`、`REASONING_*`、`STATE_*`、`ACTIVITY_*`）已在服务层较完整实现。
  - 核心 AG-UI 单测通过，基础协议行为稳定。
  - 会话仅接受平台显式创建并完成归属校验的 `session_uuid`，安全边界定义正确。
- 阻断项：
  - Deep Memory 异步任务 enqueue 名称与 Worker 注册链路不一致，导致能力链路存在实质断裂风险。

### 1.3 维度评级

| 维度 | 评级 | 结论 |
| --- | --- | --- |
| 协议完整性 | 基本通过但有风险 | 核心事件覆盖较高；会话安全约束正确，但支持子集声明不足 |
| 可用性 | 基本通过但有风险 | 合法会话路径可用；测试集与产品契约存在偏差 |
| 可靠性 | 基本通过但有风险 | 取消/中断/恢复路径具备，但 Worker 任务注册链路存在断点 |
| 性能 | 基本通过但有风险 | 并行工具执行存在优势，但流队列背压与内存控制不足 |
| 工程质量 | 基本通过但有风险 | 分层清晰，存在潜在签名调用错误与注册疏漏 |
| 路线一致性 | 基本通过但有风险 | 与 README 的双运行时方向一致，但核心能力闭环未完全达标 |

---

## 2. 评估对象与方法

### 2.1 评估对象

- 协议入口层：
  - `src/app/api/v1/agent/agent_api.py`
  - `src/app/api/v1/agent/ws_handler.py`
- Agent 业务层：
  - `src/app/services/resource/agent/agent_service.py`
  - `src/app/services/resource/agent/protocol_adapter/ag_ui.py`
  - `src/app/services/resource/agent/ag_ui_normalizer.py`
  - `src/app/services/resource/agent/pipeline_manager.py`
  - `src/app/services/resource/agent/processors.py`
  - `src/app/services/resource/agent/agent_session_manager.py`
- 引擎与模型层：
  - `src/app/engine/agent/main.py`
  - `src/app/engine/model/llm/main.py`
  - `src/app/engine/model/llm/clients/openai_client.py`
- 跨资源集成：
  - `src/app/services/resource/workflow/nodes/node.py`
- 持久化与任务链：
  - `src/app/models/interaction/chat.py`
  - `src/app/worker/tasks/agent.py`
  - `src/app/worker/tasks/__init__.py`
  - `src/app/worker/main.py`

### 2.2 协议对照基准

- AG-UI SDK 版本：`ag-ui-protocol==0.1.13`
  - 证据：`pyproject.toml:11`
- AG-UI 事件/类型定义来源：
  - `/.venv/lib/python3.10/site-packages/ag_ui/core/events.py`
  - `/.venv/lib/python3.10/site-packages/ag_ui/core/types.py`

### 2.3 验证方法

- 静态审计：
  - 执行链路、状态机、错误路径、并发路径、跨模块依赖一致性。
- 协议审计：
  - 输入消息角色、事件类型覆盖、Interrupt/Resume、State/Activity、扩展事件兼容性。
- 测试审计：
  - 基础 AG-UI 测试集合复核。
  - E2E 全套 Agent 场景复核。
- 运行时证据：
  - Worker 注册状态通过 Python 运行时检查（任务函数列表、cron 列表）。

---

## 3. 架构与执行流程审计（应用层/引擎层）

### 3.1 架构分层结论

- 与 README “编排层-业务层-纯引擎层”的设计方向一致（`README.md:565-573`）。
- Agent 执行主链路分层清晰：
  - 协议入口：`agent_api.py` / `ws_handler.py`
  - 业务编排：`AgentService.async_execute`
  - 协议适配：`ProtocolAdapterRegistry + AgUiProtocolAdapter`
  - 流水线上下文与能力装配：`AgentPipelineManager`
  - 引擎执行：`AICapabilityProvider -> AgentEngineService -> LLMEngineService`
  - 事件与持久化：`PersistingAgentCallbacks + AgentSessionManager`

### 3.2 关键时序（摘要）

1. 客户端通过 `/agent/{uuid}/execute`、`/agent/{uuid}/sse` 或 `/agent/chat` 发起请求。  
2. `AgentService.async_execute` 加载实例并做权限校验。  
3. 协议适配器将 AG-UI 输入规范化为 `input_content/history/client_tools/resume_tool_call_ids`。  
4. 解析会话（当前强依赖平台 UUID threadId），构建 Pipeline（短期记忆/RAG/深度记忆/工具装配）。  
5. 调用 `execute_agent_with_billing` 进入 Agent 引擎循环。  
6. 回调层把 LLM/Tool/Reasoning 信号映射为 AG-UI 事件并写入会话。  
7. 终态发 `RUN_FINISHED` 或 `RUN_ERROR`，并收口 `STATE_DELTA`。

### 3.3 审计判断

- 正向评价：
  - 执行链路可读性高、职责边界明确。
  - 中断（client-side tool）与恢复（resume tool result）具备结构化设计。
  - 会话仅接受平台创建并与用户绑定的 `session_uuid`，符合 SaaS 平台安全边界。
  - Workflow AgentNode 能以 AG-UI payload 驱动 Agent，具备跨资源复用能力。
- 关键缺口：
  - Deep Memory 的任务命名/注册链路不一致，削弱“记忆能力”实际有效性。

---

## 4. AG-UI协议覆盖矩阵

### 4.1 Message Role 输入兼容

| Role | 支持状态 | 实现行为 | 证据 |
| --- | --- | --- | --- |
| `user` | 支持 | 文本与多模态 `content parts` 解析为用户输入 | `ag_ui_normalizer.py` |
| `assistant` | 支持 | 转为 LLM assistant history，保留 tool_calls/encrypted_value | `ag_ui_normalizer.py` |
| `system` | 支持 | 映射到 system 消息 | `ag_ui_normalizer.py` |
| `developer` | 支持 | 映射到 system 消息（保留内容） | `ag_ui_normalizer.py` |
| `tool` | 支持 | 映射到 tool history，保留 `tool_call_id` | `ag_ui_normalizer.py` |
| `activity` | 支持（前端态） | 显式不入模，作为 frontend-only | `ag_ui_normalizer.py` 注释 |
| `reasoning` | 支持 | 转系统前缀消息，支持 `encrypted_value` 携带 | `ag_ui_normalizer.py` |

### 4.2 事件输出覆盖

| 事件类别 | 覆盖状态 | 说明 | 证据 |
| --- | --- | --- | --- |
| Run 生命周期 | 支持 | `RUN_STARTED / RUN_FINISHED / RUN_ERROR` 完整 | `agent_service.py:358-727` |
| 文本消息 | 支持 | `TEXT_MESSAGE_START / CONTENT / END` | `agent_service.py:529-547`, `320-336` |
| 文本 chunk 便利事件 | 未直接输出 | 未输出 `TEXT_MESSAGE_CHUNK`，使用 triad 形式 | `events.py` 对照 `agent_service.py` |
| Tool 调用 | 支持 | `TOOL_CALL_START / ARGS / END / RESULT` | `agent_service.py:292-318`, `391-528` |
| Tool chunk 便利事件 | 未直接输出 | 接收 `LLMToolCallChunk`，对外仍发 `TOOL_CALL_ARGS` | `agent_service.py:442-482` |
| Reasoning | 支持 | `REASONING_START / MESSAGE_START / CONTENT / MESSAGE_END / END / ENCRYPTED_VALUE` | `agent_service.py:549-573`, `337-349`, `164-189` |
| State | 支持 | `STATE_SNAPSHOT / STATE_DELTA(runStatus)` | `agent_service.py:376-386`, `629-633` 等 |
| Activity | 支持 | `ACTIVITY_SNAPSHOT / ACTIVITY_DELTA` | `agent_service.py:191-281` |
| Thinking 事件族 | 未实现 | SDK 定义了 `THINKING_*`，当前服务未发 | `events.py:24-33` 对照服务代码 |

### 4.3 Interrupt/Resume 兼容

- `interrupt`：
  - 支持在 `RUN_FINISHED` 扩展 `outcome="interrupt"` 与结构化 payload。
  - 证据：`agent_service.py:654-665`
- `resume`：
  - 支持 `resume.payload.toolResults -> tool messages` 注入。
  - 证据：`ag_ui.py`、`ag_ui_normalizer.py:117-125`
- 风险：
  - Resume 仅覆盖工具结果型恢复；更广义恢复语义未定义（可接受但需文档化）。

### 4.4 判定

- 协议兼容等级：**基本通过但有风险**
- 主要差距：事件子集支持声明不足、异步能力链路存在断点，影响“全面支持”结论。

---

## 5. 全场景用法与推演矩阵（后端覆盖验证）

| 场景 | 请求方式 | 典型前端行为 | 后端覆盖 | 判定 |
| --- | --- | --- | --- | --- |
| 1. 一次性执行 | `POST /agent/{uuid}/execute` | 提交输入后等待完整事件列表 | 已实现 | 通过 |
| 2. SSE流式对话 | `POST /agent/{uuid}/sse` | 增量渲染消息与状态 | 已实现 | 通过（需合法会话） |
| 3. WebSocket会话 | `WS /agent/chat` | 双向交互，持续收发事件 | 已实现 | 通过（需合法会话） |
| 4. 取消运行 | WS `CUSTOM ps.cancel_run` | 用户点击停止按钮 | 已实现（扩展事件） | 通过（需文档化） |
| 5. 现有会话续聊 | `threadId=平台session_uuid` | 连续上下文聊天 | 已实现 | 通过 |
| 6. 无状态会话 | `forwardedProps.sessionMode=stateless` | 不落库、短会话调用 | 当前平台策略不支持 | **符合安全策略（非缺陷）** |
| 7. 非UUID threadId | `threadId=任意会话标识` | 第三方AG-UI客户端常见做法 | 被硬性拒绝 | **符合安全策略（通过）** |
| 8. 服务端工具链 | assistant tool_calls -> tool result | 前端展示工具执行轨迹 | 已实现 | 通过 |
| 9. 客户端工具中断 | tool interrupt -> 等待客户端执行 | 前端弹交互组件 | 已实现 | 通过 |
| 10. resume工具结果恢复 | `resume.toolResults` | 前端回填工具结果后二次运行 | 已实现 | 通过 |
| 11. 多模态输入 | user content parts(binary/text) | 上传文件/图片输入 | 已实现 | 通过 |
| 12. reasoning可见性 | reasoning增量与加密值 | 前端展示思考摘要/隐藏敏感链路 | 已实现 | 通过 |
| 13. state同步 | snapshot + delta | 前端状态驱动UI（runStatus） | 已实现 | 通过 |
| 14. Workflow AgentNode 集成 | Workflow 节点触发 Agent | 编排场景下复用 Agent 能力 | 已实现 | 通过（需合法会话或显式历史） |
| 15. 深度记忆后台任务 | commit 后 enqueue worker job | 用户期望后续可召回/摘要 | enqueue 名称与注册链路不一致 | **不通过（P0）** |
| 16. 计费链路闭环 | 预估-冻结-结算 | 用户按量扣费 | Agent 主路径可运行 | 基本通过（存在潜在签名风险） |

---

## 6. 生产水准评估

### 6.1 代码质量

- 正向：
  - 分层清晰，协议适配器模式可扩展（`ProtocolAdapterRegistry`）。
  - 回调模型完整，业务与引擎耦合度可控。
- 风险：
  - 存在潜在“函数签名调用不一致”的隐患（见问题清单 P1-3）。
  - Worker 注册链路存在维护性问题（见 P0-1、P1-2）。

### 6.2 稳定性与可靠性

- 正向：
  - 提供取消、中断、错误终态处理。
  - Session lock 避免同会话并发写冲突。
- 风险：
  - 若客户端未遵循“先创建会话再执行”契约，会触发稳定拒绝（属于契约不一致，不是安全缺陷）。
  - Deep Memory 任务名/注册不一致造成能力“看似开启、实际弱化”。

### 6.3 性能

- 正向：
  - 工具执行并行化（`asyncio.gather`）具备吞吐优势。
  - Context window 管理器有预算与压缩逻辑。
- 风险：
  - `AsyncGeneratorManager` 默认无界队列（`maxsize=0`），慢消费场景下可能累积内存压力。

### 6.4 可观测性

- 正向：
  - 发出 `ps.meta.trace`、`ps.meta.usage`、`STATE_DELTA`、`ACTIVITY_DELTA`，便于前端与调试联动。
- 风险：
  - Worker 任务链路缺少“任务名校验 + 启动自检 + 告警”，问题易静默。

### 6.5 安全与权限

- 正向：
  - HTTP 与 WS 均走鉴权路径；会话读写校验 user ownership。
  - Agent 资源执行路径有权限检查。
- 风险：
  - WS token 采用 query 参数方式，需配合网关日志脱敏与短时令牌策略。

### 6.6 运维与可发布性

- 当前判断：**有条件可发布**。在“未启用或未依赖 Deep Memory”场景可控；若要放量依赖深度记忆能力，需先关闭 P0 风险。

---

## 7. 路线一致性评估（对照 README）

### 7.1 与目标一致项

- 与 P1.2 Agent 路线基本一致：
  - 支持自主工具调用与多轮循环（`README.md:373-375`）。
- 与“双运行时模型”一致：
  - Agent 属于执行型资源，按编排层/业务层/引擎层运行（`README.md:565-573`）。
- 与统一执行入口一致：
  - 保留 `ExecutionService` 作为跨资源执行入口（`README.md:361`）。

### 7.2 偏离项

- 会话安全契约文档化不足：
  - 当前实现要求 `threadId` 对应“平台显式创建且用户归属合法”的会话 UUID，但外部接入说明不充分，易导致集成误判。
- 深度记忆能力链路存在实际断点：
  - 影响“记忆核心”方向的落地可靠性。
- 多协议扩展（MCP/A2A）仅停留在可扩展结构，尚无实际实现。

### 7.3 路线一致性结论

- 结论：**基本一致但未闭环**
- 建议：优先修复 P0 项后，再进入协议能力扩展与生态化阶段。

### 7.4 公开 API / 接口 / 类型层建议（本轮仅建议，不改代码）

1. 会话策略接口建议：
   - 明确 `threadId` 与平台 session 的关系：`threadId` 仅用于映射已存在、已授权的平台会话。
   - 对“会话必须平台显式创建”给出统一错误码与接入指引。
   - 若平台不支持无状态运行，文档显式声明 `sessionMode=stateless` 为不支持字段。
2. AG-UI 事件接口建议：
   - 发布“已支持事件子集 + 版本号 + 替代映射”清单。
   - 对 `TEXT_MESSAGE_CHUNK`、`TOOL_CALL_CHUNK`、`THINKING_*` 明确“实现/不实现/替代事件”策略。
3. Worker 任务接口建议：
   - 统一 enqueue job name 与 worker function 注册名。
   - 固化 Deep Memory 任务注册清单，并在启动阶段做缺失告警或 fail-fast。

---

## 8. 问题清单（P0/P1/P2）

### 已澄清项（非缺陷）

#### C1 会话必须由平台显式创建（安全设计，不纳入缺陷）

- 结论：
  - `threadId` 绑定平台会话 UUID 且必须通过平台会话创建流程，是 SaaS 安全边界，不应按“兼容任意外部 session_id”整改。
- 证据：
  - `src/app/services/resource/agent/agent_service.py:1082-1113`
  - `src/app/services/resource/agent/protocol_adapter/ag_ui.py:29`
- 建议动作：
  - 将该策略写入接入规范与 API 文档（必读约束），并为非法 `threadId` 提供清晰错误码与指引。

### P0-1 Deep Memory 后台任务链路断裂（阻断）

- 问题：
  - enqueue 使用的任务名与 Worker 注册函数名不一致，且 agent 任务未加入 Worker 注册表。
- 证据：
  - enqueue 名称：`agent_session_manager.py:142,154`（`index_long_term_context_task`, `summarize_trace_task`）
  - 实际函数：`worker/tasks/agent.py:10,40`（`index_trace_task`, `summarize_trace_task`）
  - 注册列表无 agent 任务：`worker/tasks/__init__.py:18-25`
  - 运行时校验：
    - `registered_task_names` 仅 6 个，不含 agent 任务
    - `agent_task_functions` 存在 `index_trace_task/summarize_trace_task`
- 影响面：
  - 深度记忆索引/摘要可能不执行或部分执行。
  - 能力体验与路线上“记忆核心”目标不一致。
- 触发条件：
  - 开启深度记忆并发生会话 commit。
- 建议修复：
  - 统一任务命名（enqueue 与函数注册一致）。
  - 在 `tasks/__init__.py` 注册 agent 任务函数。
  - 增加 Worker 启动自检：关键任务名缺失即 fail-fast。

### P1-1 AG-UI 事件子集支持未显式声明（高风险兼容项）

- 问题：
  - SDK定义了 `TEXT_MESSAGE_CHUNK/TOOL_CALL_CHUNK/THINKING_*`，当前实现未输出或未对齐。
- 证据：
  - 事件全集：`/.venv/.../ag_ui/core/events.py:16-52`
  - Agent 发射集合：`agent_service.py:358-738`（未见 `THINKING_*` 与 chunk 便利事件输出）
- 影响面：
  - 不同客户端对“事件便利型”依赖时可能出现兼容差异。
- 触发条件：
  - 使用依赖 `*_CHUNK` 或 `THINKING_*` 事件的标准/第三方 AG-UI 客户端接入时。
- 建议修复：
  - 输出“AG-UI Support Profile（支持子集）”并固定版本策略。
  - 对不支持事件给出明确替代映射（例如统一 triad）。

### P1-2 Worker cron 注册链路存在实现缺陷（高风险运维项）

- 问题：
  - `tasks/__init__.py` 对 `CRON_JOBS` 进行了局部重绑定，未回写 `worker.main` 中的全局列表。
- 证据：
  - `worker/tasks/__init__.py:27-30`
  - 运行时校验：`cron_jobs_after_import=[]`
- 影响面：
  - 周期任务可能未按预期启用。
- 触发条件：
  - Worker 启动并依赖 `CRON_JOBS` 加载周期任务时。
- 建议修复：
  - 改为 `CRON_JOBS.extend([...])`，避免局部重绑定。

### P1-3 `execute_llm_with_billing` 调用签名不一致（潜在运行时错误）

- 问题：
  - `with_billing` 需要 `usage_accumulator`，但 `execute_llm_with_billing` 调用处未传入。
- 证据：
  - 要求参数：`llm_capability_provider.py:130-137`
  - 调用缺参：`llm_capability_provider.py:239-245`
- 影响面：
  - 该路径一旦被调用，可能触发 `TypeError` 并中断能力。
- 触发条件：
  - 调用 `execute_llm_with_billing` 且进入 `with_billing` 分支时。
- 建议修复：
  - 补齐参数传递并增加单测覆盖该路径。

### P1-4 WebSocket 取消协议为自定义扩展，未文档化（高风险协同项）

- 问题：
  - 取消使用 `CUSTOM ps.cancel_run`，返回 `ps.control.cancelled`。
- 证据：
  - `ws_handler.py:49-76`, `140-149`
- 影响面：
  - 跨客户端集成时，若未了解扩展事件，取消行为不可用。
- 触发条件：
  - 第三方前端按标准 AG-UI 事件消费、但未实现 `ps.cancel_run` 扩展时。
- 建议修复：
  - 在接口文档中明确该扩展协议。
  - 增加统一控制事件命名规范。

### P1-5 E2E 测试契约与产品安全策略漂移（高风险质量门禁项）

- 问题：
  - E2E 仍将“随机 threadId 直接可运行”作为正确行为，和平台“会话显式创建”安全契约冲突。
- 证据：
  - `tests/api/v1/e2e/test_agent_full_suite.py` 中多个 case 使用随机 `threadId` 并期望成功。
- 影响面：
  - CI 信号失真：可能把“安全策略正确实现”误报成回归缺陷。
- 触发条件：
  - E2E 用例使用随机 `threadId` 直连执行、未先完成平台会话创建时。
- 建议修复：
  - 统一测试前置：先创建会话，再执行 Agent。
  - 把非法 session 场景改为“预期拒绝”用例。

### P2-1 流队列背压策略不足（可优化）

- 问题：
  - `AsyncGeneratorManager` 默认无界队列，慢消费时可能内存增长。
- 证据：
  - `async_generator.py:4-9`
- 建议修复：
  - 引入有界队列与背压策略，或按事件类型降采样。

### P2-2 工具并发失败处理可观测性可增强（可优化）

- 问题：
  - 工具并发异常统一封装字符串错误，缺少标准化错误码与维度统计。
- 证据：
  - `engine/agent/main.py:189-193`
- 建议修复：
  - 输出结构化错误对象（code/category/retriable）。

### P2-3 `sessionMode=stateless` 透传字段与平台策略不一致（可优化）

- 问题：
  - 上游节点仍写入 `forwardedProps.sessionMode=stateless`，与“必须平台会话”策略不一致，易误导集成方。
- 证据：
  - `src/app/services/resource/workflow/nodes/node.py:553`
- 建议修复：
  - 若平台明确不支持无状态模式，移除该字段生成逻辑并更新文档。

---

## 9. 修复路线图（短中长期）

### 9.1 短期（1-3天，P0清零）

1. 修复 Deep Memory 任务链：
   - 统一任务名，补齐 agent 任务注册。
   - 增加启动自检与日志告警。
2. 修复测试契约漂移：
   - 将 E2E 会话前置统一改为“显式创建会话”。
   - 增加“非法 session_uuid 被拒绝”的负向用例。
3. 回归验证：
   - 目标：`tests/api/v1/e2e/test_agent_full_suite.py` 仅在预期拒绝用例失败时告警。

### 9.2 中期（1-2个迭代，稳态增强）

1. 输出 AG-UI 支持子集文档（版本化）。
2. 修复 `execute_llm_with_billing` 缺参路径并补单测。
3. 加强 WS 控制通道规范化与兼容说明。
4. 增加流背压策略与容量保护。
5. 统一会话策略对外文档：显式声明“仅平台创建会话可执行”。
6. 对 `sessionMode=stateless` 做策略收敛（移除透传或标记为无效字段）。

### 9.3 长期（路线强化）

1. 扩展多协议适配（MCP/A2A）到可用级。
2. 建立协议合规回归套件（输入/事件/交互）。
3. 建立生产观测面板（会话失败率、中断恢复成功率、任务丢失率、计费闭环时延）。

### 9.4 依赖关系

- P0-1 必须先完成，再进行中长期优化。
- 协议支持文档应在 P0 后立即补齐，避免前后端认知分叉。
- E2E 契约修正需与会话策略文档化同步推进，避免再次漂移。

### 9.5 验收标准

1. `Agent E2E` 在“显式会话创建”前置下全量通过。
2. 非法 `session_uuid/threadId` 场景被稳定拒绝，且错误码与文档一致。
3. Deep Memory 任务可在 Worker 侧观测到稳定执行。
4. AG-UI 支持子集文档已发布并版本化。
5. 报告中所有 P0 项转为“已关闭”。

---

## 10. 附录

### 10.1 测试命令与结果

- 命令：
  - `./.venv/bin/pytest -q tests/services/resource/agent/test_ag_ui_agent_service.py tests/services/resource/agent/test_persisting_callbacks_reasoning.py tests/engine/agent/test_agent_ag_ui_interrupt.py tests/api/v1/agent/test_agent_ag_ui_sse.py`
  - 结果：`36 passed in 70.06s`
- 命令：
  - `./.venv/bin/pytest -q tests/api/v1/e2e/test_agent_full_suite.py`
  - 结果：`4 failed, 1 passed in 149.94s`

### 10.2 运行时校验命令（Worker）

- `registered_task_names` 校验：
  - 结果：仅 `process_consumption_task / process_asset_intelligence_task / physical_delete_asset_task / update_chunk_task / process_document_task / garbage_collect_document_task`
- `agent_task_functions` 校验：
  - 结果：`index_trace_task / summarize_trace_task`
- `CRON_JOBS` 校验：
  - 结果：`[]`

### 10.3 关键证据索引

- 会话策略硬闸门：`src/app/services/resource/agent/agent_service.py:1082-1113`
- 协议适配 threadId->session_uuid：`src/app/services/resource/agent/protocol_adapter/ag_ui.py:29`
- Workflow 注入 stateless（策略一致性待收敛）：`src/app/services/resource/workflow/nodes/node.py:553`
- Deep Memory enqueue：`src/app/services/resource/agent/agent_session_manager.py:142-155`
- Worker agent 任务函数：`src/app/worker/tasks/agent.py:10,40`
- Worker 任务注册：`src/app/worker/tasks/__init__.py:18-25`
- AG-UI 事件全集：`.venv/lib/python3.10/site-packages/ag_ui/core/events.py:16-52`
- Agent 事件发射实现：`src/app/services/resource/agent/agent_service.py:358-738`

---

## 结论（面向排期）

- 当前 Agent 资源在“架构方向”和“核心协议事件能力”上达到生产可用的基础水平；会话安全策略定义正确并应继续坚持。
- 若业务依赖 Deep Memory，需先完成 P0-1 清零，再进行放量；并同步补齐协议支持子集与接入文档，降低跨端集成风险。
