# Workflow TODO（2026-03-20）

- 当前状态：**Workflow 后端已经收口到默认 `wrp` 协议、请求边界显式 `protocol`、durable live attach / replay / interrupt / resume，以及 `run_query / run_control / run_preparation / run_execution` 的拆分架构；本轮又补了 resume 路径无效 compile 清理、event sequence 本地缓存和 live attach 尾段读取优化。**
- 当前判断：**本阶段后端主目标已从“定义协议”切换到“补齐前端运行中心、UIAPP host 和前端回归覆盖”，避免协议与消费面再次漂移。**
- 本文目标：**继续跟踪评估报告剩余项，并保证 Workflow 与 Agent 在协议边界和 service 分层上保持一致心智。**

---

## 1. 当前基线

### 1.1 后端已经具备的能力

- [x] `POST /api/v1/workflow/{uuid}/execute`
- [x] `POST /api/v1/workflow/{uuid}/async`
- [x] `POST /api/v1/workflow/{uuid}/nodes/{node_id}/debug`
- [x] `GET /api/v1/workflow/{uuid}/runs`
- [x] `GET /api/v1/workflow/runs/{run_id}`
- [x] `GET /api/v1/workflow/runs/{run_id}/events`
- [x] `GET /api/v1/workflow/runs/{run_id}/replay`
- [x] `POST /api/v1/workflow/runs/{run_id}/cancel`
- [x] `POST /api/v1/workflow/{uuid}/sse`
- [x] `WS /api/v1/workflow/ws`
- [x] runtime IR / durable event log / node executions / checkpoint / interrupt / resume

### 1.2 前端当前确认存在的缺口

- [x] `prismaspace-frontend/packages/prismaspace/sdk/src/clients/workflow-client.ts` 目前只封装了 `execute / executeAsync / debug / listRuns / getRun / listRunEvents / cancelRun`，**没有 workflow SSE / run_id 级 live attach / replay stream / WS client**。
- [x] `prismaspace-frontend/packages/prismaspace/resources/workflow/src/workbench/WorkflowWorkbench.vue` 当前“试运行”默认走 `client.workflow.execute()`，**仍是阻塞同步接口**。
- [x] `WorkflowWorkbench.vue` 当前运行详情与事件主要依赖 query 轮询，**不是流式消费**。
- [x] `WorkflowTestRunPanel.vue` 当前可看历史、可取消运行，但**没有中断恢复、run_id 级 live attach、replay stream、流式输出时间线**。
- [x] 前端还没有 workflow 的 `run_id -> live attach / reconnect` 交互面。
- [ ] 前端也还没有“交互型 profile 下的 scoped active-run”能力，但这不应作为通用 workflow workbench 的默认需求。
- [ ] 前端还没有 workflow -> UIAPP 双向交互承接面。
- [ ] 前端还没有 chat-flow / AG-UI profile 的 workflow 消费面。

### 1.3 当前必须明确的方向

- [x] Workflow 前端默认运行入口应从同步 `execute` 切到流式 `sse`
- [x] Workflow 后续要补成“run 独立于连接”的模型，而不是“连接断开即执行结束”
- [x] Workflow 协议层需要单独收口，不能长期依赖散落在 API / Service / 前端组件里的隐式契约
- [x] 通用 workflow workbench 的 reconnect 默认应基于 `run_id`，而不是先查 `active-run`
- [x] `active-run` 只适用于 Chat Flow / UIAPP 交互这类有显式 scope 的 profile
- [x] 通用 workflow workbench 默认进入页面时应保持干净构建环境，不自动 attach 任意运行中的流
- [x] 只有当前 UI 上下文已经持有显式 `run_id` 时，才允许自动进入 live attach

---

## 2. P0 协议层收口

### 2.1 建立 Workflow 专用协议模型

- [x] 新增 `src/app/schemas/protocol/workflow_runtime.py`
- [x] 定义统一 event envelope：`type / seq / runId / traceId / payload / optional scope`
- [x] 明确 workflow canonical event types：
  - `run.started`
  - `run.finished`
  - `run.failed`
  - `run.cancelled`
  - `run.interrupted`
  - `node.started`
  - `node.completed`
  - `node.failed`
  - `node.skipped`
  - `stream.started`
  - `stream.delta`
  - `stream.finished`
  - `checkpoint.created`
- [x] 明确 control message types：
  - `run.start`
  - `run.attach`
  - `run.cancel`
  - `run.resume`
  - `ui.event.submit`
- [x] 明确 `run.attach` 默认按 `run_id` 运作
- [x] 若未来引入 `active-run.resolve`，限定为显式 scope profile 能力
- [x] 为协议补 `capabilities` 声明，避免前端继续靠源码猜能力

### 2.2 收口 API / Adapter 责任边界

- [x] 新增 `src/app/services/resource/workflow/protocol_adapter/`
- [x] 让 `workflow_service.py` 只产出 canonical workflow events
- [x] HTTP SSE / WebSocket / replay / future AG-UI bridge 改由 adapter 层做映射
- [x] 不再让 `workflow_api.py` 和 `ws_handler.py` 持续手拼 ad hoc 事件
- [x] adapter registry 下沉到 service / protocol bridge 链路，API/WS 不再直接依赖 adapter 细节
- [x] 移除 API/WS 为补 node metadata 额外查 instance graph 的逻辑，事件源头直接补齐 `node`
- [x] `workflow_service.py` 已拆出 `run_query / run_control / run_preparation / run_execution`

### 2.3 统一运行状态和值域

- [x] 收口 workflow run status 的 canonical 值：
  - `pending`
  - `running`
  - `succeeded`
  - `failed`
  - `cancelled`
  - `interrupted`
- [x] 前后端统一状态文案映射，修复当前前端仍按大写状态值渲染的问题

---

## 3. P0 后端运行时对齐

### 3.1 补 run_id 级 live attach；active-run 仅限交互型 profile

- [x] 新增 `GET /api/v1/workflow/runs/{run_id}/live`
- [x] 为 workflow run 引入 attach / detach 语义
- [x] 让前端可以在刷新或重进页面后按已知 `run_id` 重新接回运行中 workflow
- [ ] 若未来引入 workflow `active-run`，必须要求显式 `scope_id / thread_id / client_session_id`
- [ ] scoped active-run 必须校验 actor + scope 归属，不能仅按 workflow uuid 猜测运行中的流

### 3.2 解除“连接绑定执行”的限制

- [x] SSE 断开后不再默认 cancel workflow run
- [x] WebSocket 断开后不再默认 cancel workflow run
- [x] 连接只负责观察 run，不负责定义 run 生命周期
- [x] cancel 仅由显式 `cancel` 控制消息触发

### 3.3 中断恢复协议化

- [x] 把当前 `resume_from_run_id + meta.resume` 的隐式约定收敛为正式 schema
- [x] 明确 interrupt payload、resume payload、resume token 的标准结构
- [x] replay / get_run 返回的 interrupt 信息要足以驱动前端恢复 UI

### 3.4 测试补齐

- [x] 新增 workflow `run_id -> live attach / reconnect` API 测试
- [x] 新增 SSE 断连后后台继续运行的回归
- [x] 新增 WS attach/cancel/resume 的回归
- [x] 新增协议 envelope 与 sequence 补流回归
- [ ] scoped active-run 仅在交互型 profile 下补测试，不纳入通用 workbench 基线

---

## 4. P0 前端迁移到流式执行

### 4.1 SDK 层

- [x] 扩展 `prismaspace-frontend/packages/prismaspace/sdk/src/clients/workflow-client.ts`
- [x] 新增 `streamExecute(instanceUuid, payload)`，对接 `POST /api/v1/workflow/{uuid}/sse`
- [x] 新增 `attachLiveRun(runId, afterSeq?)`，对接 future `/runs/{run_id}/live`
- [x] 新增 `replayRunStream(runId, limit?)`，对接 `/runs/{run_id}/replay`
- [x] 新增 `resumeRun(instanceUuid, runId, payload)` 的高层封装
- [x] 视协议演进新增 workflow websocket session client
- [x] 不把 `active-run` 查询封装成通用 workbench 默认 API
- [ ] 若未来支持 scoped active-run，仅在 chat flow / UIAPP interaction client 中暴露

### 4.2 工作台主链路

- [x] 修改 `WorkflowWorkbench.vue`，默认“试运行”改走 `streamExecute`
- [x] 保留阻塞 `execute` 仅作为 fallback 或测试接口，不再作为主入口
- [x] “后台运行”模式改为：
  - 提交 `/async`
  - 获取 `run_id`
  - 自动 attach live run
- [x] 当前依赖 `selectedRunQuery + selectedRunEventsQuery` 轮询的地方，改为“流式优先、轮询兜底”
- [x] workbench 默认首次进入页面时，不自动 attach 任意 live run
- [x] workbench 页面刷新后，如本地仍持有 `run_id`，优先按 `run_id` 重新 attach
- [x] 若本地没有 `run_id`，回落到 runs/history 查询，而不是盲查 `active-run`
- [x] 用户点击历史记录中某条 `running` run 时，直接按该 `run_id` attach live

### 4.3 试运行面板

- [x] 修改 `WorkflowTestRunPanel.vue`
- [x] 新增 live event timeline
- [x] 新增 stream delta 实时展示
- [x] 新增 run attach 状态提示
- [x] 新增 interrupt 态渲染
- [x] 新增 resume 表单入口
- [x] 新增 replay 模式切换
- [x] 节点调试也改走流式输出，而不是只等最终响应
- [ ] 明确区分：
  - 通用 workflow run reconnect
  - scoped interactive run reconnect

### 4.4 前端状态管理

- [x] 新增 workflow run session store 或 composable
- [x] 跟踪：
  - 当前 runId
  - 当前 sequence
  - attach 状态
  - interrupt 状态
  - resume payload
- [x] 对通用 workflow：`threadId/scopeId` 设为可选，不强制要求
- [x] 页面重进后，如本地持有 `run_id`，优先按 `run_id` attach
- [ ] `active-run` 查询仅用于 future chat flow / UIAPP interaction 这类显式 scope 场景
- [x] 不在通用 workflow 编辑页 mount 时触发任何默认 live attach

### 4.5 前端验证

- [ ] 补 SDK 层流式客户端测试
- [ ] 补 workbench 运行链路测试
- [ ] 补 interrupt/resume UI 回归
- [ ] 补 replay / cancel / reconnect 回归

---

## 5. P1 前端完整覆盖后端现有能力

### 5.1 已有后端能力但前端尚未完整承接的项

- [ ] `runs` 列表做成真正的运行中心，而不只是最近记录列表
- [x] `getRun` 详情完整展示 checkpoint / can_resume / node executions
- [ ] `events` 列表支持筛选、定位、时间线视图
- [ ] `replay` 做成可重放视图，而不是只保留原始 JSON
- [ ] `cancel` 支持运行中即时反馈
- [x] `debug node` 支持实时输出和最终结果双视图
- [ ] `subworkflow` 子运行 lineage 在 UI 上显式展示

### 5.2 当前前端需要修正的对齐问题

- [x] run 状态值对齐后端真实值
- [x] 中断态文案、按钮、恢复入口统一
- [x] 运行日志不再只展示最后几条，支持流式累积与完整展开
- [x] 测试运行结果区支持区分：
  - 最终输出
  - 事件流
  - 节点执行
  - 中断信息

---

## 6. P1 UIAPP 联动

### 6.1 协议与后端

- [x] 定义 workflow -> UIAPP 的 `ui.mount / ui.patch / ui.unmount`
- [x] 定义 UIAPP -> workflow 的 `ui.event.submit / ui.event.abort`
- [ ] 明确：
  - 传整份 DSL 还是引用 UIAPP instance/page
  - interaction token 如何生成与校验
  - 提交后如何恢复 workflow 执行

### 6.2 前端承接

- [ ] 在 workflow workbench 或 runtime surface 中新增 UIAPP host
- [ ] 可根据 workflow 事件挂载 UIAPP DSL
- [ ] UIAPP 提交后，把 interaction payload 回传 workflow runtime
- [ ] UIAPP 交互过程可与 run timeline 对齐显示

---

## 7. P2 占位：Chat Flow / AG-UI Profile（暂不进入本轮执行）

### 7.1 当前处理原则

- [x] 当前只预留协议扩展位，不纳入本轮交付
- [x] 当前不把 Chat Flow 相关 active-run / thread 语义引入通用 workflow workbench
- [x] 当前先保证 WRP 对 future Chat Flow 保留可扩展面

### 7.2 预期目标与能力

- [ ] 定义有 conversation/thread scope 的 workflow profile
- [ ] 对外暴露 AG-UI 兼容事件流
- [ ] 支持 conversational interrupt / resume / replay / reconnect
- [ ] 支持 UIAPP 在对话流中的挂载与提交
- [ ] 让 workflow 节点里的 agent/chat 输出复用 AG-UI 前端消费能力

---

## 8. 文档与治理

- [x] 把 `doc/Workflow资源协议与运行时评估报告.md` 的建议固化成对内协议说明
- [x] 新增 workflow 协议说明文档，明确：
  - 输入契约
  - 事件契约
  - 控制消息
  - interrupt/resume
  - attach/replay
  - UIAPP extension
- [x] 更新前端 `packages/prismaspace/resources/workflow/需求说明.md`
- [x] 明确 v0 兼容窗口和 v1 协议切换计划

---

## 9. 验收标准

### 9.1 前端运行入口

- [x] 工作台默认“试运行”已改为流式接口
- [x] 后台运行提交后可自动 attach live run
- [x] 默认进入 workflow 编辑页时不自动 attach 任意 live run
- [x] 页面刷新后如本地持有 `run_id`，可重新接回运行中的 workflow
- [x] 用户点击某条运行中的历史记录时，可按 `run_id` 重新接回该流

### 9.2 中断与恢复

- [x] interrupt 在 UI 上可见且语义清晰
- [x] 用户可直接从面板提交 resume payload
- [x] resume 后事件流和最终输出可继续接上

### 9.3 运行观测

- [ ] 运行中心可以看 summary / detail / node executions / events / replay
- [x] 事件流可实时看，也可事后 replay
- [x] cancel 有明确反馈，不再靠猜测状态

### 9.4 协议化

- [x] 不再需要通过阅读后端源码才能知道 workflow 支持哪些事件和控制能力
- [x] 前后端共享正式 workflow 协议定义
- [x] Workflow 为 UIAPP 和 Chat Flow 预留稳定扩展面

---

## 10. 非阻塞后续项

- [ ] 更成熟的 workflow websocket runtime surface
- [ ] 更完整的 replay renderer 与 trace 视图
- [ ] 更细的 retention / compaction / archive 策略
- [ ] 更系统的 UIAPP 交互脚手架
- [ ] 若后续对外开放 workflow 协议，再整理为外部标准文档
