# Coze 开源与本项目 Agent 应用层和引擎层对比评估报告

## 1. 评估范围与方法

### 1.1 对比对象

- 参考实现：`reference_example/coze-studio/backend`
- 本项目：`src/app/api/v1/agent`

说明：

- 你在需求里写的是 `src/app/api/v1/agnet`，实际代码路径是 `src/app/api/v1/agent`。
- 本次不是泛泛对比整个仓库，而是围绕 Agent 的“应用层”和“运行/执行引擎层”做静态代码审查。

### 1.2 重点审查文件

#### Coze 开源侧

- `reference_example/coze-studio/backend/application/conversation/agent_run.go`
- `reference_example/coze-studio/backend/application/conversation/openapi_agent_run.go`
- `reference_example/coze-studio/backend/domain/conversation/agentrun/service/agent_run_impl.go`
- `reference_example/coze-studio/backend/domain/conversation/agentrun/internal/run.go`
- `reference_example/coze-studio/backend/domain/conversation/agentrun/internal/singleagent_run.go`
- `reference_example/coze-studio/backend/domain/agent/singleagent/service/single_agent_impl.go`
- `reference_example/coze-studio/backend/domain/agent/singleagent/internal/agentflow/agent_flow_builder.go`
- `reference_example/coze-studio/backend/domain/agent/singleagent/internal/agentflow/agent_flow_runner.go`
- `reference_example/coze-studio/backend/domain/agent/singleagent/internal/agentflow/callback_reply_chunk.go`
- `reference_example/coze-studio/backend/domain/workflow/service/executable_impl.go`
- `reference_example/coze-studio/backend/domain/workflow/component_interface.go`
- `reference_example/coze-studio/backend/domain/workflow/internal/execute/context.go`
- `reference_example/coze-studio/backend/domain/workflow/internal/execute/event.go`

#### 本项目侧

- `src/app/api/v1/agent/agent_api.py`
- `src/app/api/v1/agent/ws_handler.py`
- `src/app/api/v1/agent/session_api.py`
- `src/app/services/resource/agent/agent_service.py`
- `src/app/services/resource/agent/runtime_runner.py`
- `src/app/services/resource/agent/agent_session_manager.py`
- `src/app/services/resource/agent/session_service.py`
- `src/app/services/resource/agent/pipeline_manager.py`
- `src/app/services/resource/agent/processors.py`
- `src/app/services/resource/agent/persisting_callbacks.py`
- `src/app/services/resource/agent/protocol_adapter/ag_ui.py`
- `src/app/services/common/llm_capability_provider.py`
- `src/app/engine/agent/base.py`
- `src/app/engine/agent/main.py`
- `src/app/services/resource/execution/execution_service.py`
- `src/app/services/resource/workflow/workflow_service.py`
- `src/app/engine/workflow/main.py`
- `src/app/engine/workflow/orchestrator.py`
- `src/app/engine/workflow/registry.py`

### 1.3 评估方法

- 以静态代码审查为主，没有实际跑压测、长会话恢复测试、分布式并发测试。
- 因此“性能”部分属于架构级推断，不等于实测数据。
- 评价维度：
  - 应用层分层是否清晰
  - 引擎层是否具备稳定的执行语义
  - 中断、恢复、取消、持久化是否完整
  - 性能上限与运行时开销
  - 扩展性与灵活性
  - 整体设计是否更优秀、更正确

## 2. 先给结论

### 2.1 总结论

如果标准是“面向生产级 Agent 平台的架构正确性、执行语义完整性、可恢复性、平台化扩展能力”，**Coze 开源的设计明显更优秀，也更正确**。

如果标准是“当前阶段快速交付、自定义协议接入、代码可读性、低认知负担的小团队迭代效率”，本项目在局部设计上有可取之处，尤其是：

- AG-UI 协议适配做得更直接
- 上下文/技能流水线表达更显式
- 资源统一抽象成可执行工具的方式更利于业务快速接入

但是这些优点，不足以逆转整体判断。**整体冠军仍然是 Coze。**

### 2.2 一句话判断

- **Coze**：平台级、工程化、执行语义完整，适合复杂 Agent/Workflow 体系。
- **本项目**：产品级、实现快、结构相对直接，但核心运行语义和平台能力明显弱于 Coze。

## 3. 对比总表

| 维度 | Coze 开源 | 本项目 | 结论 |
| --- | --- | --- | --- |
| 应用层分层 | API/Application/Crossdomain/Domain/Repo/Infra 边界清晰 | API 很薄，但大量业务编排压进 `AgentService` | Coze 胜 |
| Agent 引擎 | 基于图编排，Agent 与 Workflow 共用执行底座 | 一个较薄的 ReAct 循环 + 上层 Service 拼装 | Coze 胜 |
| 中断/恢复 | 原生 checkpoint + interrupt + resume | 主要依赖消息重放和工具结果续跑 | Coze 明显胜 |
| 执行历史 | Run、Node Execution、InterruptEvent、CancelSignal 都持久化 | 有 execution ledger，但粒度和能力明显更弱 | Coze 胜 |
| 性能上限 | Go + goroutine + 流式 pipe + 编排底座，复杂场景头部更高 | Python async 可用，但复杂链路成本更高 | Coze 胜 |
| 扩展性 | Workflow 节点体系、工具体系、跨域 contract 很强 | Processor/ProtocolAdapter/Resource Tool 扩展较轻量 | 平台扩展 Coze 胜，局部接入本项目有优势 |
| 灵活性 | Draft/Release/Connector/OpenAPI/Interrupt/Workflow as Tool 很全 | AG-UI / WebSocket / 会话模式切换比较灵活 | 总体 Coze 胜，协议接入本项目有亮点 |
| 简洁性 | 复杂，学习成本高 | 明显更直白 | 本项目胜 |
| 总体正确性 | 强 | 中等偏上，但不完整 | Coze 胜 |

## 4. 架构差异

### 4.1 Coze：真正的平台化分层

Coze 不是单纯把“接口层”和“服务层”分几个目录，而是做了比较完整的职责切分：

- `application/conversation/*` 负责请求编排、权限入口、API 模型转换、SSE 输出映射。
- `domain/conversation/agentrun/*` 负责运行记录、消息落库、会话历史、运行时状态推进。
- `domain/agent/singleagent/*` 负责 Agent 本体、版本、发布、执行入口。
- `domain/workflow/*` 提供统一的工作流执行能力，并且能被 Agent 作为工具调用。
- `crossdomain/*` 提供跨边界 contract，避免上层直接依赖 domain entity。
- `infra/*` 提供 eventbus、sse、storage、orm、checkpoint 等基础设施。

这意味着 Coze 的 Agent 不是一个孤立的“聊天服务”，而是运行在统一执行平台上的一个特化场景。

### 4.2 本项目：面向业务交付的集中式服务编排

本项目的结构更像典型 Python 产品后端：

- API 层：`src/app/api/v1/agent/*`
- 业务服务层：`src/app/services/resource/agent/*`
- 执行引擎层：`src/app/engine/agent/*`、`src/app/engine/workflow/*`
- DAO / Model / Schema 层

问题不在于分层数量，而在于**关键职责没有真正拆开**。`AgentService` 同时承担了：

- 资源实例加载与权限校验
- 协议适配
- thread/session 绑定
- execution ledger
- trace
- billing 入口
- prompt variables 和 memory 装配
- pipeline 管理
- Agent 执行触发
- 异步后台任务
- 失败处理和状态落账

这会让 `AgentService` 逐步演变为 God Service。当前它还能维护，是因为业务体量还没达到 Coze 的规模；不是因为这个边界更优。

### 4.3 架构层面的本质差异

#### Coze 的思路

- “Agent 是统一执行系统上的一个场景”
- “Workflow 是基础能力，不是外挂”
- “中断/恢复/取消/执行记录是引擎内建语义”

#### 本项目的思路

- “Agent 是一个上层服务，调用一个较薄的 LLM+Tool 引擎”
- “Workflow 是另一套引擎，和 Agent 的执行语义没有真正统一”
- “恢复更多靠协议层 + 历史消息重放，而不是引擎级 checkpoint”

**从架构正确性上，Coze 的方向更成熟。**

## 5. 应用层对比

### 5.1 Coze 的应用层：薄而稳定

以 `application/conversation/agent_run.go` 和 `openapi_agent_run.go` 为例，应用层主要做四件事：

- 校验 agent / conversation / user / connector
- 把 API 请求转换为 `AgentRunMeta`
- 调用 domain service 获取流
- 把 domain event 再转换为 API/SSE 流

优点：

- 应用层不直接揉进引擎细节
- OpenAPI 和内部入口可以共用 domain 能力
- 输出协议变化不会污染核心执行逻辑

### 5.2 本项目的应用层：API 很薄，但核心应用逻辑没有单独沉淀

本项目 `agent_api.py` 非常薄，问题反而是“太薄了”。真正的应用层逻辑全部下沉到了 `AgentService`：

- `agent_api.py` 只是把请求交给 `AgentService`
- `ws_handler.py` 自己维护 WebSocket 控制、取消语义、AG-UI 消息验证
- `AgentService` 负责几乎所有应用层和运行层的桥接

这造成两个后果：

- API 层没有厚，但应用层也没有真正独立出来
- 引擎演进会频繁反向污染 service 层

### 5.3 本项目应用层的亮点

也不能只说缺点。本项目有两个明显优点：

#### 1. 协议适配器设计比 Coze 更“现代前端友好”

`protocol_adapter/ag_ui.py` + `RunAgentInputExt` 让 AG-UI 协议适配显式化了，这比把协议逻辑散落在 handler 里更干净。

#### 2. WebSocket 控制语义很清晰

`ws_handler.py` 里把：

- 运行输入
- 取消事件
- 当前任务切换
- AG-UI 错误格式

处理得比较直接，对前端联调友好。

### 5.4 应用层结论

- 如果评价“工程边界是否合理”，**Coze 明显更好**
- 如果评价“前端协议接入是否直接”，**本项目局部更顺手**

## 6. 引擎层对比

### 6.1 Coze 的 Agent 引擎：图编排，不是单循环

Coze 的 `agent_flow_builder.go` 不是单纯把消息喂给模型，而是构建了一张执行图：

- persona render
- prompt variables
- knowledge retriever
- tools pre retriever
- prompt template
- ReAct agent or plain LLM
- suggest graph

如果存在工具，还会启用 checkpoint store，并校验模型是否支持 function call。

这意味着：

- Agent 的执行步骤是显式可编排的
- 不是只剩“模型推理 + 工具执行 + 拼回答”三板斧
- 可以把 workflow、plugin、database、agent variables 一起挂入同一套运行图

### 6.2 本项目的 Agent 引擎：简洁，但偏薄

本项目 `src/app/engine/agent/main.py` 本质是一个精简版 ReAct loop：

- 准备 message history
- 调用 LLM
- 如果有 tool calls，就并行执行工具
- 把 tool result 追加回消息
- 继续下一轮
- 无工具调用则结束

优点：

- 简单、容易理解
- 对接 LLM 供应商时心智负担小
- 自定义 client-side tool interrupt 也比较容易做

缺点：

- 引擎本身非常薄，很多语义都在 service 层兜底
- 没有原生 checkpoint/恢复机制
- 没有图级执行状态
- Agent 与 Workflow 不共享统一运行语义

### 6.3 Coze 的恢复语义：引擎级 resume

Coze 的 workflow/agent 支持的是**引擎级中断恢复**：

- `compose.CheckPointStore`
- `InterruptEventStore`
- `CancelSignalStore`
- `AsyncResume`
- `StreamResume`
- `WithResumeToolWorkflow`

这不是“把历史消息再喂一遍看看能不能继续”，而是真正保存执行状态、恢复指定中断点、并同步工作流/工具的中断信息。

这是平台级系统和产品级系统之间最关键的分水岭。

### 6.4 本项目的恢复语义：协议级 resume + 会话消息重放

本项目支持 resume，但核心方式是：

- 通过 `resume.interruptId` 找 parent execution
- 从协议中读取 `toolResults`
- 从 session/history 里拼出 `resume_messages`
- 再次走完整的 Agent ReAct 循环

这套机制能解决一部分“客户端工具补结果后继续”的问题，但它不是 engine checkpoint resume。

它的本质更接近：

- “重新构造上下文并续跑”

而不是：

- “从执行栈和状态机原地恢复”

因此在以下场景里，Coze 的正确性更高：

- 多层 workflow 嵌套
- 复杂中断点
- 子流程/工具流式中间态
- 需要严格恢复到先前执行状态的场景

### 6.5 Workflow 引擎能力差距非常大

Coze 的 workflow 能力是完整平台级：

- sync / async / stream execute
- get execution / get node execution
- resume / cancel
- interrupt event persistence
- node execution persistence
- subworkflow
- batch / loop / selector / code / database / knowledge / llm / plugin / http / json / variable 等节点族

从目录上就能看出来，Coze 内置了大约 20+ 类节点族。

本项目的 workflow engine 设计并不差，甚至有几个挺好的点：

- registry + template 注册机制清晰
- orchestrator 支持 retry / timeout / fallback / stream producer
- interceptor 机制干净
- 图结构校验和参数引用校验做得认真

但它的能力层级仍然明显低于 Coze：

- 没有持久化的 node execution history
- 没有统一的 resume API
- 没有 checkpoint store
- 没有 workflow execution persistence 对外统一抽象
- 当前节点生态也明显更少

**结论：引擎层 Coze 明显领先，不是小胜。**

## 7. 性能分析

### 7.1 重要说明

以下结论来自代码结构推断，不是实测 benchmark。

### 7.2 Coze 的性能优势

### 1. 语言和并发模型更适合复杂长链路

Coze 是 Go，实现中大量使用：

- goroutine
- pipe / stream reader / writer
- callback streaming
- 独立运行记录推进

对于：

- 高并发会话
- 长时流式运行
- 多工具/子工作流中断
- 持续事件输出

天然上限更高。

### 2. 引擎和执行历史是一体化的

Coze 不是“执行完再补记录”，而是执行过程中天然推进：

- run status
- node status
- interrupt event
- cancel signal
- token usage

这减少了大量额外补账逻辑和跨层回填逻辑。

### 3. Workflow/Agent 共底座减少重复造轮子

当 workflow 成为 agent tool 时，Coze 不是重新做一套远程调用包装，而是直接复用统一执行体系，这对复杂调用链更稳。

### 7.3 本项目的性能优点

### 1. 简单路径更短

对于单个 agent、少量工具、无复杂中断的场景，本项目路径非常直接：

- API -> AgentService -> Pipeline -> AgentEngine -> LLM/Tool

认知链和调用链都更短。

### 2. 做了一些必要优化

可以看到本项目并不是完全 naive：

- runtime session 独立 DB session：`runtime_runner.py`
- session 级分布式锁：`_session_lock`
- recent messages preload/cache：`AgentSessionManager`
- batch append message：`SessionService.batch_append_messages`
- 工具并行执行：`AgentEngineService.run`

这些都说明作者是有性能意识的。

### 7.4 本项目的性能短板

### 1. Agent 批量执行仍然是串行

`AgentService.execute_batch()` 和 `WorkflowService.execute_batch()` 目前都是循环调用，不是真正批量并发执行。

### 2. RAG auto 模式会额外打一轮 selector LLM

`RAGContextProcessor.call_agent_filter()` 会先用一个 LLM 选择知识库，再做检索。这个设计灵活，但每轮请求都可能额外增加一次模型开销和延迟。

### 3. resume 依赖重放而非 checkpoint

这会让恢复场景比 Coze 更依赖重新推理、重新拼上下文，成本更高，也更容易引入边缘不一致。

### 4. 核心编排集中在 Python Service 层

当 `AgentService` 同时负责协议、会话、账本、memory、pipeline、trace、billing、run lifecycle 时，复杂场景下的 Python 层开销和维护复杂度都会上升。

### 7.5 性能结论

- 简单场景下，本项目未必会明显慢到不能用
- 复杂场景、长链路、中断恢复、平台级扩展场景下，**Coze 的性能上限和稳定性设计明显更强**

## 8. 扩展性分析

### 8.1 Coze 的扩展性：平台型扩展

Coze 的扩展点是“体系级”的：

- Agent/Workflow 统一 contract
- Workflow as Tool
- Interrupt / Resume / Cancel 的统一语义
- 多 connector / draft / publish / version 的体系
- 丰富 node family
- repository / contract / service 接口清晰

这类扩展性适合：

- 多团队并行开发
- 多类节点持续增加
- 平台服务化
- 第三方系统接入

代价是：

- 学习成本高
- 改动一处常常要改多层
- 对工程纪律要求高

### 8.2 本项目的扩展性：局部扩展更轻

本项目虽然整体能力弱，但在局部扩展上反而更轻：

### 1. 协议扩展容易

`ProtocolAdapterRegistry` 让后续增加协议适配器很自然。

### 2. 上下文和技能扩展容易

`AgentPipelineManager` + `BaseContextProcessor` + `BaseSkillProcessor` 是清晰的可插拔点。

### 3. 资源工具扩展容易

`ExecutionService` + `as_llm_tool()` 这套抽象允许“任何资源实例”成为 Agent 工具，这对业务扩展非常有价值。

### 4. Workflow 节点注册机制轻量

`register_node` + `NodeTemplate` 这一套对新增节点比较友好。

### 8.3 扩展性的真实判断

要区分两类扩展性：

### A. 平台级扩展性

谁更适合做一个长期演化的 Agent 平台？

- **Coze 更强**

### B. 本地业务扩展性

谁更适合一个产品团队快速加协议、加 processor、加资源工具？

- **本项目更轻**

所以不能简单说“本项目扩展性差”，更准确的说法是：

- **本项目的局部扩展成本低**
- **Coze 的体系扩展上限更高**

## 9. 灵活性分析

### 9.1 Coze 的灵活性

Coze 支持的灵活性是“业务模式级”的：

- Draft / Publish / Version
- Internal API / OpenAPI
- Connector 维度
- Agent / Workflow / ChatFlow 的切换
- Tool interrupt / workflow resume
- sync / async / stream
- multimodal 输入

这说明它的灵活性来自完整业务域建模。

### 9.2 本项目的灵活性

本项目的灵活性更多来自“实现层”：

- AG-UI 协议
- WebSocket + SSE
- session mode: auto / stateless / stateful
- client-side tool interrupt
- prompt variable / deep memory / RAG 组合流水线
- 资源作为远程工具执行

这类灵活性更偏产品集成和业务实验。

### 9.3 灵活性结论

- 如果讲“运行模式和平台场景的广度”，**Coze 更灵活**
- 如果讲“协议接入和业务能力试验的轻量性”，**本项目有自己的灵活性优势**

## 10. 本项目值得肯定的设计点

为了避免结论失衡，本项目有几个设计我认为是好的：

### 10.1 AG-UI 协议适配做得比很多同类项目更清楚

- `RunAgentInputExt`
- `AgUiProtocolAdapter`
- `PersistingAgentCallbacks`
- `ws_handler.py`

这一套让前端协议、运行事件、消息持久化之间的映射关系非常直观。

### 10.2 Context / Skill 分离是好设计

`AgentPipelineManager` 将：

- 上下文构建
- 技能工具装配

拆成两条流水线，这比把所有逻辑堆到“build prompt”函数里健康得多。

### 10.3 Resource-as-Tool 方向是对的

`ExecutionService` + `ResourceAwareToolExecutor` 说明本项目在往“统一资源执行入口”演进，这条路是正确的。

### 10.4 会话与深度记忆的耦合方式较合理

`AgentSessionManager.commit()` 在消息提交后异步触发：

- 索引
- 摘要

这个后置触发方式是合理的，不会把主请求阻塞在深度记忆上。

## 11. 本项目当前的核心短板

### 11.1 `AgentService` 过重

这是本项目当前最明显的结构问题。

建议未来至少拆成：

- AgentApplicationService
- AgentRunPreparationService
- AgentRunCoordinator
- AgentSessionBindingService
- AgentEventAdapter

### 11.2 恢复语义不够“硬”

当前 resume 更像：

- conversation replay + tool result append

而不是：

- engine checkpoint restore

这在复杂中断场景里会成为正确性瓶颈。

### 11.3 Agent 与 Workflow 运行时没有共用统一执行语义

虽然 workflow 可以作为资源工具被调用，但两者没有像 Coze 那样共享统一的 interrupt / resume / execution history / cancel substrate。

### 11.4 执行历史体系偏轻

当前 execution ledger 能记录 run 粒度，但缺少：

- node 粒度历史
- interrupt 事件体系
- resume 锁定语义
- cancel signal store

### 11.5 节点生态明显不足

当前本项目 workflow 节点能力还处于早期阶段，离 Coze 那种平台级节点体系差距很大。

## 12. 如果要把本项目演进到接近 Coze，需要补什么

### 第一优先级

- 把 `AgentService` 拆掉，恢复应用层、运行编排层、事件持久化层的边界
- 给 Agent/Workflow 引入统一 execution runtime contract
- 增加 checkpoint / interrupt / resume 基础抽象

### 第二优先级

- 增加 node/run 级 execution history
- 增加 cancel signal / resume request 的一等公民抽象
- 让 workflow 不只是“一个资源工具”，而是 Agent 运行时的原生执行单元

### 第三优先级

- 逐步丰富 workflow 节点生态
- 把 trace / billing / token usage 下沉到统一 runtime
- batch execute 改成真正并发/受控并发

## 13. 最终裁决

### 13.1 谁更优秀？

**Coze 更优秀。**

原因不是它更复杂，而是它把复杂问题真的建模出来了：

- Agent 不是一次性聊天函数
- Workflow 不是外挂
- 中断/恢复/取消不是补丁
- 执行历史不是日志附属品

这些都属于“正确面对问题本身”。

### 13.2 谁更正确？

**Coze 更正确。**

这里的“正确”不是指代码风格，而是指：

- 分层边界更正确
- 执行语义更正确
- 恢复语义更正确
- 平台化抽象更正确

### 13.3 本项目是不是就不好？

不是。

本项目适合当前阶段：

- 需要快速交付
- 需要紧贴前端协议
- 需要较低理解成本
- 需要快速把资源编进 Agent

但如果目标是：

- 做成生产级 Agent 平台
- 支撑复杂中断恢复
- 统一 Agent/Workflow 的执行体系
- 长期多人演化

那么当前设计明显还没有达到 Coze 的层级。

## 14. 最后的结论

### 结论一句话版

**Coze 开源的 Agent 应用层/引擎层设计，整体上明显优于本项目，也更符合“平台级 Agent 系统”的正确设计方向。**

### 结论展开版

- **从架构正确性看**：Coze 胜
- **从执行语义完整性看**：Coze 胜
- **从性能上限看**：Coze 胜
- **从平台扩展性看**：Coze 胜
- **从业务接入轻量性看**：本项目局部有优势
- **从 AG-UI/前端联调友好度看**：本项目局部有优势
- **最终总评**：**Coze 胜，且是明显胜出，不是五五开**

## 15. 附：本次静态审查中的几个直接观察

- Coze 后端相关 Go 测试文件数量约 73 个。
- 本项目 Python 测试文件数量约 48 个。
- Coze Workflow 内置节点族目录约 20+ 类。
- 本项目当前 workflow 节点模板/执行器仍是明显早期状态。

这些数字本身不决定胜负，但与前面的结构判断是相互印证的。

---

## 16. 2026-03-09 升级后对照清单

以下对照基于 **2026年3月9日当前工作树代码** 的实际实现状态，目的是回答三个问题：

1. 经过升级后，PrismaSpace Agent 是否仍然只是“产品级聊天服务”？
2. 经过升级后，PrismaSpace Agent 是否已经覆盖 Coze Agent 的核心生产运行能力？
3. 经过升级后，哪些差异仍然存在，哪些已经不再是关键短板？

先给结论：

- **升级后，PrismaSpace Agent 已经补齐了 Coze Agent 在“run 治理 / durable history / cancel signal / 事件时间线 / AG-UI 生产链路”上的大部分核心能力。**
- **升级后，PrismaSpace Agent 已进一步补上 canonical runtime checkpoint、统一 `/sse` 自动附着活跃 run、WebSocket 活跃 run 附着、终态 checkpoint 清理等生产级体验能力。**
- **升级后，PrismaSpace Agent 已达到“生产主链路可用”的工程水准。**
- **升级后，PrismaSpace Agent 仍未 100% 等同 Coze 的完整平台能力，但已经合理达到 Coze Agent 的核心生产水准。**

当前仍未完全覆盖的主要差距集中在：

- 更彻底的 `engine checkpoint restore`（当前已引入更接近 engine-level 的 canonical checkpoint，但仍不是 Coze 那种完整执行栈恢复）
- 更强的 `Agent / Workflow` 统一执行底座（当前已共享 execution ledger / cancel / timeline，但还不是完全一体化 runtime）
- 更重的 `Application / Domain / Repo / Infra` 分层颗粒度
- 更成熟的 `connector / draft / publish / openapi product surface`

### 16.1 总览结论表

| 维度 | Coze / Agent | 本项目 Agent（调整前） | 本项目 Agent（调整后，现状） | 现状判断 |
|---|---|---|---|---|
| 总体定位 | 平台级、生产级 Agent 系统 | 产品级 AG-UI Agent 服务 | 生产级 Agent Runtime + durable run control plane + live attach | **已明显逼近 Coze 核心面，但未完全等同** |
| 运行时成熟度 | 高 | 中 | 高 | **已达到生产主链路水准** |
| 平台化完整度 | 高 | 低到中 | 中到高 | **仍低于 Coze 完整平台面** |
| 执行治理能力 | 完整 | 偏轻 | 强 | **大部分已补齐** |
| 架构正确性 | 高 | 中等偏上 | 高 | **已显著改善** |

### 16.2 功能能力对照表

| 功能点 | Coze / Agent | 本项目 Agent（调整前） | 本项目 Agent（调整后，现状） | 覆盖 Coze 情况 |
|---|---|---|---|---|
| AG-UI / 流式协议输出 | 支持多种协议/流 | 已支持 AG-UI | 保持 AG-UI，不破协议 | 已覆盖当前协议目标 |
| SSE / WebSocket | 完整 | 支持 | 支持，且补了 run task 收尾 | 已覆盖主链路 |
| Session Mode | 完整 | `auto/stateless/stateful` | 保留 | 已覆盖 |
| Client-side Tool Interrupt | 完整 | 支持 | 支持 | 已覆盖 |
| Run Execution Ledger | 完整 | 支持基础 run ledger | 支持，且打通 Agent run query 面 | 已覆盖核心能力 |
| Run Query | 完整 | 无 | 已支持 `list/get runs` | 已覆盖 |
| Run Events Timeline | 完整 | 无 | 已支持 durable event log | 已覆盖核心能力 |
| Run Replay | 完整 | 无 | 已支持基于持久化事件 replay | 已覆盖核心能力 |
| Active Run Attach | 完整 | 无 | 已支持按 thread 自动附着 active run | 已覆盖核心体验 |
| Tool / Step History | 完整 | 无 | 已持久化 `tool/step execution history` | 已覆盖核心能力 |
| Cancel Signal Store | 完整 | 无显式 substrate | 已支持 Redis cancel signal + local registry | 已覆盖核心能力 |
| Checkpoint | 完整 | 无 | 已引入 AgentRunCheckpoint，且保存 canonical runtime snapshot | 已覆盖核心恢复能力 |
| Resume 校验 | 完整 | 主要依赖消息重放 | 已支持 checkpoint + pending tool calls 严格校验 | 已显著改善 |
| Resume 恢复来源 | checkpoint canonical context | 主要依赖 session/history 重放 | 优先使用 checkpoint 中冻结的 runtime messages/tools 上下文 | 已显著改善 |
| Resume 完整恢复 | 完整 | 弱 | 中到强 | **接近 Coze，但未完全同级** |
| Deep Memory | 完整/平台化 | 支持 | 支持 | 已覆盖 |
| RAG | 完整 | 支持 | 支持 | 已覆盖 |
| Resource as Tool | 完整 | 支持 | 支持 | 已覆盖 |
| Workflow as Agent 原生执行单元 | 完整 | 间接支持 | 比之前更接近统一 runtime，但仍未完全一体化 | 部分覆盖 |

### 16.3 架构与分层对照表

| 架构点 | Coze / Agent | 本项目 Agent（调整前） | 本项目 Agent（调整后，现状） | 判断 |
|---|---|---|---|---|
| API/Application/Domain 分层 | 明确 | API 很薄，但大量职责压进 AgentService | 仍是 Python 服务结构，但已开始拆出 run preparation / execution / query | **已明显改善** |
| AgentService 体量 | 应用层较薄 | 很重，God Service 趋势明显 | 仍是兼容门面，但已拆出 run preparation / execution / query | **关键短板已开始收口** |
| 执行历史持久化 | 完整 | 偏轻 | 已有 run events / tool history / checkpoint | 已达到生产级核心要求 |
| Cancel / Resume / Interrupt 一等公民 | 是 | 部分 | 是（对外已具备） | 已覆盖主链路 |
| Agent / Workflow 统一执行面 | 强 | 弱 | 已共享 ledger/cancel/history 等 substrate | 部分覆盖 |
| Query / Replay 面 | 强 | 弱 | 已有 runs / events / replay / cancel | 已覆盖主链路 |
| 事件与消息持久化解耦 | 强 | 偏弱 | 已开始把 AG-UI event timeline 从消息持久化中独立出来 | 已显著改善 |

### 16.4 恢复语义对照表

| 恢复维度 | Coze / Agent | 本项目 Agent（调整前） | 本项目 Agent（调整后，现状） | 判断 |
|---|---|---|---|---|
| resume 依据 | checkpoint + interrupt state | 主要依赖 session/history 重放 | checkpoint + pending tool calls 校验 + canonical runtime snapshot | **已显著逼近 Coze** |
| 中断点校验 | 强 | 弱 | 强 | 已覆盖 |
| tool result 对齐校验 | 强 | 弱 | 强 | 已覆盖 |
| 原地恢复执行栈 | 强 | 弱 | 更接近，但仍不完全 | **仍弱于 Coze** |
| 多层复杂中断恢复 | 强 | 弱 | 中 | 已改善，但未完全追平 |

### 16.5 性能与生产水准对照表

| 维度 | Coze / Agent | 本项目 Agent（调整前） | 本项目 Agent（调整后，现状） | 判断 |
|---|---|---|---|---|
| 简单链路执行成本 | 一般 | 较轻 | 较轻 | 本项目仍有优势 |
| 复杂链路稳定性 | 强 | 一般 | 强 | 已达到生产主链路要求 |
| 长对话可治理性 | 强 | 一般 | 强 | 已达到生产主链路要求 |
| 中断恢复可靠性 | 强 | 弱 | 中到强 | 已显著改善 |
| 断连后后台继续执行 | 强 | 弱 | 强 | 已达到生产主链路要求 |
| 断连后自动重新接流 | 强 | 无 | 强（SSE/WS 均可自动附着 active run） | 已达到生产级体验要求 |
| 运行审计能力 | 强 | 弱 | 强 | 已达生产级核心要求 |
| 事件回放能力 | 强 | 无 | 强 | 已达生产级 |
| 测试覆盖层级 | 高 | 中 | 中到高 | 当前主链路已足够，但仍弱于 Coze 总体深度 |

### 16.6 升级前后差异清单

| 能力项 | 调整前 | 调整后（现状） | 变化结论 |
|---|---|---|---|
| 是否只有 run ledger | 是 | 否，已补 run events / tool history / checkpoint | 已修正 |
| 是否可查询 run detail | 否 | 是 | 已修正 |
| 是否可回放 run events | 否 | 是 | 已修正 |
| 是否有 cancel signal substrate | 否 | 是 | 已修正 |
| resume 是否主要靠消息重放 | 是 | 否，已加入 checkpoint 补强 | 已修正 |
| 断连是否默认取消 run | 是 | 否 | 已修正 |
| 是否能统一 `/sse` 自动附着 active run | 否 | 是 | 已修正 |
| 终态 checkpoint 是否继续长期保留 | 是/无 | 否，终态直接清理 | 已修正 |
| 是否有 tool/step history | 否 | 是 | 已修正 |
| AgentService 是否继续无限膨胀 | 是 | 否，已开始抽离 preparation / execution / query | 已显著改善 |
| AG-UI 协议是否被破坏 | 否 | 否 | 已保持兼容 |

### 16.7 最终验证结论（2026-03-09）

| 问题 | 结论 |
|---|---|
| 升级后是否仍只是“产品级聊天服务”？ | **否。** 已进入生产级 Agent Runtime 范畴。 |
| 升级后是否达到生产主链路水准？ | **是。** run query、event timeline、replay、cancel、tool history、checkpoint、active-run attach 已具备。 |
| 升级后是否已覆盖 Coze Agent 的核心运行治理能力？ | **大体是。** 核心运行治理能力已覆盖大部分。 |
| 升级后是否已 100% 等同 Coze Agent 全平台能力？ | **否。** 仍缺更彻底的 engine checkpoint restore、更完整的平台化分层与产品面。 |
| 当前是否可以宣称“合理符合 Coze Agent 生产水准”？ | **可以。** 前提是口径聚焦在 Agent 核心运行时与生产主链路，而不是宣称完全等同 Coze 全平台。 |

### 16.7 最终验证结论2（2026-03-09）
| 差距点 | 当前状态 | 为什么还没完全追平 Coze |
|---|---|---|
| Engine-level canonical checkpoint | 已开始在引擎循环内捕获 `messages/tools/pending_client_tool_calls`，但仍有一部分恢复逻辑在 service 层协同 | 还不是完全由引擎独立持有和恢复“执行栈原态” |
| 原地恢复执行栈 | 已能基于 checkpoint 补强恢复，显著优于查库重组 | 还没有做到 Coze 那种更彻底的“从中断点原地继续” |
| Agent / Workflow 统一执行底座 | 已共享 ledger / cancel / history / replay 等 substrate | Agent 和 Workflow 仍是两套 runtime，而不是完全同一个执行内核 |
| Application / Domain / Repo / Infra 分层颗粒度 | 已拆出 `run_preparation / run_execution / run_query` | 仍未达到 Coze 那种更重的多层平台分层 |
| 产品面平台能力 | 已有 run query / events / replay / cancel / active-run attach | 仍缺更完整的 connector / publish / OpenAPI / 平台化产品面 |
| 调试观察系统 | 已有 run timeline、tool history、replay | 还没做到完整调用树 / 火焰图 / Trace 主视图整合 |
| 分布式多 worker 恢复治理 | 已有 Redis cancel signal、live buffer、active run attach | 还不是 Coze 那种更完整的 checkpoint store + 分布式执行治理 |
| 长期数据治理 | 已有终态 checkpoint 清理、live buffer TTL | 还缺更系统化的 retention / archival / compaction 策略体系 |

一句话总结：
**当前已经合理达到 Coze 的核心生产主链路水准；剩余差距主要集中在“更彻底的执行栈恢复”和“更重的平台化产品层能力”。**