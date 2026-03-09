# Coze 开源与本项目 Tool 应用层和引擎层对比评估报告

## 1. 评估范围与方法

### 1.1 对比对象

- 参考实现：`reference_example/coze-studio/backend`
- 本项目：`src/app/services/resource/tool_service.py`

本次不是泛泛对比整个仓库，而是围绕 Tool/Plugin 的两层核心能力做静态审查：

- 应用层：资源管理、版本管理、权限、执行编排、与 Agent/Workflow 的集成方式
- 引擎层：参数模型、执行器、认证、请求组装、响应裁剪、扩展点设计

### 1.2 重点审查文件

#### Coze 开源侧

- `reference_example/coze-studio/backend/application/plugin/init.go`
- `reference_example/coze-studio/backend/domain/plugin/service/service.go`
- `reference_example/coze-studio/backend/domain/plugin/service/exec_tool.go`
- `reference_example/coze-studio/backend/domain/plugin/service/tool/invocation.go`
- `reference_example/coze-studio/backend/domain/plugin/service/tool/invocation_args.go`
- `reference_example/coze-studio/backend/domain/plugin/service/tool/invocation_http.go`
- `reference_example/coze-studio/backend/domain/plugin/service/tool/invocation_custom_call.go`
- `reference_example/coze-studio/backend/domain/plugin/service/tool/invocation_mcp.go`
- `reference_example/coze-studio/backend/domain/plugin/service/tool/invocation_saas.go`
- `reference_example/coze-studio/backend/crossdomain/plugin/model/openapi.go`
- `reference_example/coze-studio/backend/crossdomain/plugin/model/plugin_manifest.go`
- `reference_example/coze-studio/backend/domain/plugin/repository/tool_repository.go`
- `reference_example/coze-studio/backend/domain/plugin/repository/tool_impl.go`
- `reference_example/coze-studio/backend/domain/workflow/plugin/plugin.go`

#### 本项目侧

- `src/app/services/resource/tool_service.py`
- `src/app/engine/tool/main.py`
- `src/app/engine/tool/callbacks.py`
- `src/app/engine/utils/parameter_schema_utils.py`
- `src/app/engine/schemas/parameter_schema.py`
- `src/app/schemas/resource/tool_schemas.py`
- `src/app/services/resource/base/base_impl_service.py`
- `src/app/services/resource/base/base_resource_service.py`
- `src/app/services/resource/execution/execution_service.py`

### 1.3 评估方法

- 以静态代码审查为主，没有做真实压测和故障注入。
- “性能”部分属于架构级推断，不等于实测结果。
- 判断标准不是“谁代码更短”，而是：
  - 分层是否清晰
  - 运行时语义是否完整
  - 契约是否严格且一致
  - 扩展成本是否可控
  - 在生产环境中是否更正确

## 2. 先给结论

### 2.1 最终结论

如果评价标准是“Tool 平台架构的完整性、正确性、可扩展性、与 Agent/Workflow 的系统性集成能力”，**Coze 开源明显更优秀，也更正确**。

如果评价标准是“当前阶段只做少量 HTTP Tool、快速上线、代码尽量简单”，本项目的方案更轻、更快上手，也更接近 MVP。

但是你要求的是“谁设计得更优秀、更正确”，不是“谁更省事”。在这个标准下，**最终胜者是 Coze 开源**。

### 2.2 一句话原因

本项目当前更像“带计费和 Trace 的 HTTP Tool 服务”；Coze 则是“以 Manifest/OpenAPI 为契约中心、可被 Agent/Workflow/Marketplace 复用的通用 Plugin/Tool 平台”。

两者不在同一个完成度层级上。

## 3. 总体判断表

| 维度 | Coze 开源 | 本项目 | 胜出方 |
| --- | --- | --- | --- |
| 应用层架构 | 分层清晰，场景完整 | 统一但耦合重 | Coze |
| 引擎层设计 | 契约驱动，策略化执行 | 轻量直接，但能力窄 | Coze |
| 运行时正确性 | 明显更强 | 存在多个契约缺口 | Coze |
| 性能上限 | 更高，批量/缓存/复用更多 | 单次执行路径较短 | Coze |
| 扩展性 | 高 | 中低 | Coze |
| 灵活性 | 高且受控 | 表面灵活，实则能力窄 | Coze |
| 研发启动成本 | 高 | 低 | 本项目 |
| MVP 交付效率 | 中 | 高 | 本项目 |

## 4. 架构层面的核心差异

### 4.1 Coze 的本质

Coze 把 Tool 放在 Plugin 体系里，不把它当成“数据库里一条 URL 配置”。

它的主线是：

1. `PluginManifest + OpenAPI` 定义契约
2. `application/plugin` 负责应用层编排和对外接口
3. `domain/plugin/service` 负责领域执行和场景判定
4. `repository` 负责 draft/online/version/agent-tool 等多种读写模型
5. `tool/invocation_*` 负责具体 transport 执行
6. `domain/workflow/plugin`、Agent 节点等上层模块复用同一套 Tool 契约

这意味着 Coze 不是“某个服务会执行工具”，而是“整个系统把工具视为一等平台能力”。

### 4.2 本项目的本质

本项目把 Tool 放进统一 Resource 抽象里，这个方向本身是合理的。`ToolService` 继承统一资源契约，确实让 Tool/Agent/Workflow 有了统一管理入口。

但当前 `ToolService` 同时承担了太多职责：

- 资源实例创建与发布
- 输入输出 schema 校验
- LLM Tool 转换
- 权限检查
- 计费
- Trace
- 回调日志
- HTTP 执行编排

相关代码集中在 `src/app/services/resource/tool_service.py:64-336`。

这会带来一个直接问题：**它是一个“能跑”的服务，但还不是一个“平台化”的 Tool 子系统。**

## 5. 应用层对比

### 5.1 Coze 应用层更优秀的地方

#### 5.1.1 执行场景是显式建模的

Coze 在 `ExecuteTool` 里不是直接查一条 Tool 然后执行，而是先根据场景构建执行器：

- 在线 Agent
- 草稿 Agent
- Tool Debug
- Workflow

见 `reference_example/coze-studio/backend/domain/plugin/service/exec_tool.go:45-179`。

这很关键。因为“同一个 Tool”在不同场景下读取的版本、权限、默认参数、认证上下文都可能不同。Coze 把这种差异当作核心语义处理，而不是后期 if/else 打补丁。

#### 5.1.2 版本体系和绑定关系更完整

Coze 的 repository 接口不仅有 `draft/online/version`，还有：

- `agent tool draft`
- `agent tool version`
- SaaS plugin tools
- 按 API 唯一键查 tool
- 批量读取、批量绑定、批量复制

见 `reference_example/coze-studio/backend/domain/plugin/repository/tool_repository.go:13-63`。

相对地，本项目 Tool 的版本语义主要复用通用 `ResourceInstance`，这对简单场景足够，但对“Tool 被 Agent 定制绑定、再发布、再回溯”的平台场景是不够的。

#### 5.1.3 Workflow 集成不是“顺带支持”，而是正式接口

Coze 在 `domain/workflow/plugin/plugin.go` 中：

- 可获取 Tool 信息给工作流设计器
- 可把 Tool 转成 invokable tool
- 可把 OAuth 中断转成 Workflow 的 interrupt event

见 `reference_example/coze-studio/backend/domain/workflow/plugin/plugin.go:257-401`。

这说明 Coze 的 Tool 不只是“被某个接口执行”，而是具备系统级复用语义。

### 5.2 本项目应用层的优点

#### 5.2.1 Resource 统一抽象是对的

`ResourceImplementationService` 给 Tool/Agent/Workflow 建立了统一契约，这个方向没有问题，见：

- `src/app/services/resource/base/base_impl_service.py:25-166`
- `src/app/services/resource/base/base_resource_service.py:11-105`

这比“每种资源各搞一套 API/Service/Execution 体系”更容易在后续统一治理。

#### 5.2.2 接入计费和 Trace 更直接

`ToolService.execute` 中把：

- 权限
- TraceManager
- BillingContext
- ToolEngineService

串成了一条直观路径，见 `src/app/services/resource/tool_service.py:247-327`。

对于早期产品，这种直接性是高效的。

### 5.3 本项目应用层的明显问题

#### 5.3.1 抽象契约定义了，但 Tool 实现没有完成

`ToolService` 里至少有两个契约方法仍然是 `pass`：

- `get_searchable_content`：`src/app/services/resource/tool_service.py:203-207`
- `execute_batch`：`src/app/services/resource/tool_service.py:329-336`

而上层 `ExecutionService.execute_batch` 已经把批量执行当成正式入口暴露，见 `src/app/services/resource/execution/execution_service.py:64-99`。

这意味着当前设计在“架构上宣称支持批量”，但实现上并没有真正闭环。

#### 5.3.2 基类默认批量实现本身也有 bug

`ResourceImplementationService.execute_batch` 的默认实现引用了未定义的 `billing_entity`：

- `src/app/services/resource/base/base_impl_service.py:159-166`

这不是小瑕疵，而是说明抽象层本身还没打磨到可安全复用。

#### 5.3.3 ToolService 混合了应用层和引擎编排职责

`ToolService` 既负责资源生命周期，又亲自构造回调、解析 schema、决定 raw response、调引擎、处理计费、设置 trace 输出。

这导致：

- 应用层难以变薄
- 引擎难以独立演进
- 未来支持多 transport/auth/content-type 时，`ToolService` 会继续膨胀

Coze 把这部分切开了，本项目当前没有切开。

#### 5.3.4 还有直接的代码正确性问题

- `create_instance` 使用了未导入的 `ConfigurationError`：`src/app/services/resource/tool_service.py:87-93`
- `ToolSchema.inputs_schema/outputs_schema` 的 `default_factory` 写成了 `dict` 而不是 `list`：`src/app/schemas/resource/tool_schemas.py:15-16`

这类问题说明当前 Tool 应用层还没有进入“结构稳定、边界清晰”的状态。

## 6. 引擎层对比

### 6.1 Coze 引擎层为什么更强

#### 6.1.1 它是契约优先，而不是执行优先

Coze 不是先拼 HTTP 请求，再希望 schema 能兜住；它先约束契约。

`Openapi3T.Validate` 和 `Openapi3Operation.Validate` 对以下内容做了强校验：

- server 数量和 URL 合法性
- operationId / summary 必填
- request body 只能是合法 object 且 media type 受限
- parameter 位置/type 合法
- response 只接受 200 + JSON object

见 `reference_example/coze-studio/backend/crossdomain/plugin/model/openapi.go:39-137`、`276-423`。

另外，Manifest 对 auth payload、auth type、subtype、HTTPS URL、common params location 也做了严格验证，见：

- `reference_example/coze-studio/backend/crossdomain/plugin/model/plugin_manifest.go:94-323`

这套设计牺牲了一部分“随便配都能跑”的自由度，但换来了更稳定的执行语义。**这就是“更正确”的核心。**

#### 6.1.2 参数组装是编译式的，不是散装拼接

Coze 的 `InvocationArgs` 会做这些事情：

- 按 path/query/header/cookie/body 分组
- 注入 manifest common params
- 注入默认值
- 从变量系统拉默认值
- 识别文件字段并把 URI 转成可访问 URL

见 `reference_example/coze-studio/backend/domain/plugin/service/tool/invocation_args.go:90-514`。

这意味着 Tool 运行时不是单纯“拿用户输入替换模板”，而是“根据契约生成可执行请求”。

#### 6.1.3 transport 扩展点明确

Coze 用 `Invocation` 接口加策略分发：

- HTTP：`invocation_http.go`
- Custom：`invocation_custom_call.go`
- SaaS：`invocation_saas.go`
- MCP：预留实现位

分发入口在 `reference_example/coze-studio/backend/domain/plugin/service/exec_tool.go:597-607`。

这比本项目当前把 Tool 固定为 HTTP 请求更有平台性。

#### 6.1.4 响应处理有策略，不是一刀切

Coze 支持三种 invalid response process strategy：

- 返回原值
- 返回默认值
- 直接报错

见 `reference_example/coze-studio/backend/domain/plugin/service/exec_tool.go:680-977`。

这在 Workflow/Agent 场景里非常有价值，因为有时你要强约束，有时你要容错，有时你要保留原始响应。

### 6.2 本项目引擎层的优点

#### 6.2.1 引擎与 ORM 解耦，这点是对的

`ToolEngineService` 只收原生参数，不依赖 SQLAlchemy model，见：

- `src/app/engine/tool/main.py:11-18`

这是一个好的方向，说明作者知道“执行引擎不该直接依赖数据库实体”。

#### 6.2.2 单次 HTTP Tool 的实现足够直接

本项目的引擎路径很短：

1. 校验输入
2. 生成 request parts
3. 发送 HTTP 请求
4. 校验输出
5. 用 schema 塑形

见 `src/app/engine/tool/main.py:19-175`。

在“只支持 JSON API 工具”的前提下，这种简单性是有价值的。

#### 6.2.3 超时策略比 Coze 片段里更明确

本项目显式设置了 `httpx.AsyncClient(timeout=30.0)`，见 `src/app/engine/tool/main.py:16-18`。

而 Coze 当前审查到的 `defaultHttpCli := resty.New()` 未看到显式 timeout，见：

- `reference_example/coze-studio/backend/domain/plugin/service/tool/invocation_http.go:51`

这一点本项目更稳妥。

### 6.3 本项目引擎层的关键问题

#### 6.3.1 能力面过窄，本质上只支持 JSON over HTTP

当前 `ToolEngineService` 硬编码了：

- 只发 HTTP
- 只组装 path/header/query/body
- body 只走 `json=`
- 响应直接 `response.json()`

见 `src/app/engine/tool/main.py:41-48`、`160-175`。

这意味着它不支持或没有显式建模：

- OAuth / Service Token
- 表单、multipart、文件上传
- 非 JSON 响应
- 自定义 transport
- Workflow/用户变量默认值注入
- Tool 级中断语义

从平台视角看，这不是“轻量版 Coze”，而是“HTTP Tool 的第一个版本”。

#### 6.3.2 URL 占位符校验实际上是失效的

`_format_url` 的注释说“缺少 path 参数会因 KeyError 失败”，但实际实现只是 `str.replace`，不会抛 KeyError：

- `src/app/engine/tool/main.py:146-158`

所以如果 URL 里有 `{city}`，而 schema/输入没正确提供，最终请求很可能带着未替换占位符直接发出。

这是典型的“代码注释描述的语义”和“真实语义”不一致。

#### 6.3.3 输出塑形会伪造空数组

`schemas2obj` 在数组源数据为空时，会主动构造一个默认元素：

- `src/app/engine/utils/parameter_schema_utils.py:142-156`

这意味着：

- API 真返回 `[]`
- 本项目可能输出 `[default_item]`

这已经不是“容错”，而是**改变真实业务语义**。从 correctness 角度看，这个问题很严重。

Coze 的响应裁剪虽然也会做策略处理，但它是显式策略选择；本项目这里是隐式伪造数据。

#### 6.3.4 raw response 的类型契约不一致

`ToolExecutionResponse.data` 被声明为 `Dict[str, Any]`：

- `src/app/schemas/resource/tool_schemas.py:77-80`

但 `ToolEngineService.run(..., return_raw_response=True)` 返回的是 `Dict[str, Any] | Any`：

- `src/app/engine/tool/main.py:19-30`

如果某个工具返回 JSON array、string、number，这个契约就不成立。Coze 在这方面更清楚，它同时返回：

- `Request`
- `RawResp`
- `TrimmedResp`
- `RespSchema`

见 `reference_example/coze-studio/backend/domain/plugin/service/exec_tool.go:85-93`。

#### 6.3.5 日志/回调设计过于原始

本项目 `_ToolExecutionCallbacks` 直接 `print` 输入、metadata、结果：

- `src/app/services/resource/tool_service.py:30-61`

问题包括：

- 无日志级别
- 无脱敏
- 无结构化字段
- 生产环境不可控

Coze 虽然也会打请求日志，但走的是 logger，并且至少在代码形态上是 debug 级输出：

- `reference_example/coze-studio/backend/domain/plugin/service/tool/invocation_http.go:109-117`

两边都需要注意敏感信息泄漏，但本项目当前明显更粗糙。

## 7. 性能评估

## 7.1 Coze 的性能上限更高

原因不是“Go 一定比 Python 快”，而是架构更有利于规模化：

- repository 有大量 batch 接口：`tool_repository.go`
- 工具、版本、Agent 绑定信息可批量加载
- HTTP client 复用：`invocation_http.go:51`
- 静态插件产品可走配置读取：`tool_impl.go:178-194`
- Workflow/Agent 复用同一套 Tool 契约，减少重复转换逻辑

尤其是 `BatchGetSaasPluginToolsInfo`、`MGetOnlineTools`、`MGetVersionTools` 这类接口，说明 Coze 从一开始就在考虑“大量工具、多版本、多主体绑定”的场景。

## 7.2 本项目单次调用路径更短，但平台规模性能较弱

本项目对单个 HTTP Tool 的执行路径非常短，这意味着：

- 少量工具
- 单次执行
- 低并发
- 内网 API

场景下，未必会慢。

但是一旦进入平台化场景，问题会出现：

- 每次执行都重新解析 `inputs_schema/outputs_schema`：`tool_service.py:300-301`
- 当前没有批量执行实现：`tool_service.py:329-336`
- DAO 每次按实例加载完整依赖树
- `ToolEngineService` 的 client 生命周期是隐式的，创建与关闭边界不清晰：`tool_service.py:67-74`、`main.py:16-18`

所以本项目不是“性能差”，而是**性能上限不高，扩容路径也不清晰**。

## 8. 扩展性评估

### 8.1 Coze 扩展性明显更强

Coze 当前已经把扩展点摆出来了：

- 新 transport：实现 `Invocation`
- 新执行场景：扩展 `ExecuteScene`
- 新认证：扩展 manifest/auth 和注入逻辑
- 新 Agent/Workflow 集成：复用 crossdomain plugin contract
- 新 SaaS 来源：通过 repository/service 适配

这套设计的关键是：**扩展点是有边界的，不需要一路改穿所有层。**

### 8.2 本项目扩展性目前偏低

本项目如果要支持以下任意一项，都会改动较大：

- OAuth 工具
- 文件输入输出
- FormData / multipart
- 非 HTTP Tool
- Workflow/变量系统注入默认值
- Tool 执行中断 / rerun
- 多种响应处理策略

原因是当前 Tool 引擎的核心抽象仍然是：

`ParameterSchema + HTTP method + URL + JSON body`

这个抽象可以跑，但很难自然长成一个通用 Tool 平台。

## 9. 灵活性评估

### 9.1 Coze 的灵活性是“受约束的灵活”

Coze 不是最自由的，因为它故意限制：

- request body 必须是 object
- response 只支持 200 + JSON object
- manifest/openapi 必须满足平台规则

但这种限制让它获得了：

- 更稳定的 Agent Tool 调用
- 更可预测的 Workflow 接入
- 更容易做 UI 配置、参数面板、市场化分发

这是一种成熟平台的灵活性。

### 9.2 本项目的灵活性更多来自“约束少”，不是“能力强”

本项目表面上看很自由，因为你可以直接写 URL、写 schema、写 role。

但这种自由本质上是：

- 认证能力没建模
- transport 没建模
- 中断语义没建模
- 响应策略没建模
- 批量执行没建模

所以它更像“先让用户自己配到能跑”，而不是“平台本身提供成熟灵活性”。

## 10. 正确性评估

这里的“更正确”不是指“谁没有 bug”，而是指：

- 架构是否忠实表达了业务语义
- 契约是否闭合
- 运行时结果是否与定义一致

### 10.1 Coze 更正确的原因

1. Tool 的契约模型更强
2. 执行场景语义更完整
3. 认证、中断、默认值、响应裁剪都有正式机制
4. 与 Agent/Workflow 的适配是同一套契约延伸
5. 很多行为是“显式策略”，而不是“隐式副作用”

### 10.2 本项目“不够正确”的具体表现

#### 10.2.1 抽象契约和实现状态不一致

- 抽象层定义了 `execute_batch`
- Tool 实现没有完成
- 基类默认实现还带 bug

这说明架构承诺和实现交付不一致。

#### 10.2.2 输出塑形会改变真实数据

- 空数组被伪造为默认元素数组

这已经触碰 correctness 底线。

#### 10.2.3 类型契约不闭合

- raw response 可能不是 dict
- API schema 却固定 `data: Dict[str, Any]`

#### 10.2.4 存在直接可见的实现级错误

- `ConfigurationError` 未导入
- `default_factory=dict` 用在 `List[...]`
- URL 占位符缺失检测无效

这些问题单个看都不大，但放在一起就说明：**当前 Tool 子系统还没有达到“设计正确且收敛”的状态。**

## 11. Coze 也不是没有问题

为了避免结论失真，也要指出 Coze 侧的不完美之处。

### 11.1 MCP 路径是未完成状态

Coze 已经预留了 MCP plugin type，并在执行分发时支持 `PluginTypeOfMCP`：

- `reference_example/coze-studio/backend/domain/plugin/service/exec_tool.go:597-603`

但真正的 `mcpCallImpl` 仍然直接返回：

- `mcp call not implemented`
- `reference_example/coze-studio/backend/domain/plugin/service/tool/invocation_mcp.go:26-32`

这说明 Coze 的平台设计领先于实现完成度。

### 11.2 HTTP client timeout 在当前代码片段里不够显式

`defaultHttpCli := resty.New()` 没看到明确 timeout 配置：

- `reference_example/coze-studio/backend/domain/plugin/service/tool/invocation_http.go:51`

如果上游 context 没有 deadline，这可能成为运行时风险。

### 11.3 SaaS plugin 会引入额外网络依赖

`BatchGetSaasPluginToolsInfo` 直接请求外部 API：

- `reference_example/coze-studio/backend/domain/plugin/repository/tool_impl.go:483-558`

这会引入额外延迟与外部依赖面。

但是这些问题并没有推翻整体结论，因为它们更多是“局部未收口”，而不是架构方向错误。

## 12. 最终裁决

### 12.1 谁设计得更优秀

**Coze 开源更优秀。**

原因：

- 平台视角更完整
- 分层更清楚
- 执行语义更丰富
- Agent/Workflow 集成更自然
- 扩展点设计更成熟

### 12.2 谁设计得更正确

**Coze 开源更正确。**

原因：

- 契约优先
- 运行时策略显式
- 版本/场景/认证/中断语义更一致
- 本项目当前存在多处契约不闭合和实现缺口

### 12.3 对本项目的客观定位

本项目当前 Tool 设计不是“错误方向”，而是：

- 方向基本正确
- 抽象有一定前瞻性
- 但完成度明显不足
- 更适合当作 MVP / 过渡架构

它暂时还不能和 Coze 这种成熟 Plugin/Tool 平台设计打平。

## 13. 对本项目的改进建议

如果目标是把本项目 Tool 子系统升级到接近 Coze 的层级，我建议至少做以下改造：

### 13.1 把 ToolService 拆成三层

- ToolApplicationService：权限、计费、Trace、API 编排
- ToolDomainService：版本/实例/执行语义
- ToolInvocationEngine：transport/auth/request/response

### 13.2 引入“编译后的 Tool 契约”

不要直接拿 `inputs_schema/outputs_schema` 在运行期到处解释。应先编译成类似：

- request model
- response model
- auth config
- transport config
- capability flags

### 13.3 把 transport 做成策略接口

至少预留：

- HTTP JSON
- HTTP form/multipart
- Custom
- MCP

### 13.4 修复当前 correctness 问题

- 修正 `ConfigurationError` 导入
- 修正 `default_factory=dict`
- 修正 `_format_url` 占位符校验
- 修正 `schemas2obj` 空数组伪造
- 修正 `return_raw_response` 的类型契约
- 完成 `execute_batch` 和 `get_searchable_content`

### 13.5 增加引擎级测试

当前本项目 Tool 测试主要是 API 侧，且依赖外部 `wttr.in`：

- `tests/api/v1/test_tool.py:41-166`

这不足以保护引擎层语义。至少应补：

- request parts 组装测试
- URL 模板缺参测试
- 输出塑形测试
- raw response 类型测试
- auth/header 注入测试
- batch execute 测试

## 14. 结论归档

最终结论保持不变：

**Coze 开源在 Tool 的应用层/引擎层设计上，整体明显优于本项目，也更正确。**

本项目当前设计可作为早期版本继续迭代，但如果目标是做中大型 Agent/Workflow/Marketplace 共用的 Tool 平台，应优先向 Coze 的“契约驱动 + 场景建模 + 策略执行器 + 系统级集成”方向演进，而不是继续在现有 `ToolService + ToolEngineService` 上横向堆功能。
