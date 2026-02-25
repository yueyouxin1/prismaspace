# Project 技术文档（唯一正确定义与开发约束）

## 1. 唯一正确定义
**Project 是“运行时语义与治理容器”，以 `main_resource`（UiApp/Agent）作为入口资源，用于组织项目级上下文、依赖视图与发布治理。**

Project 不承担资源归属职责，资源归属 Workspace。Project 仅声明、聚合与治理这些资源在特定交付场景下的使用方式。

## 2. 核心职责（必须满足）
1. **入口资源（main_resource）**
   - Project 必须支持一个入口资源指针，用于表示项目的主入口应用。  
   - `main_resource` 仅用于入口表达与交付入口定位，不等价于项目的完整依赖图。

2. **项目级环境上下文（env_config）**
   - Project 提供 `env_config` 作为可选上下文供运行时注入使用。  
   - `env_config` 不得成为资源的硬依赖，资源必须保持无项目上下文也可运行。

3. **全局依赖视图**
   - Project 必须提供依赖视图能力，用于展示“项目显式引用资源 + 资源自身解析的依赖链”的聚合图。  
   - 当前实现通过 `GET /projects/{project_uuid}/dependency-graph` 提供聚合依赖图。

## 3. 明确不承担的职责（禁止混用）
- **资源归属**：资源归属 Workspace，不归属 Project。  
- **运行时依赖强约束**：Project 引用资源不应强制限制运行时依赖选择。  
- **资源运行前提**：资源不得要求 Project 上下文才能执行。

## 4. 关键数据结构
### 4.1 Project 模型
- `main_resource_id`：项目入口资源指针。  
- `env_config`：项目级环境配置（JSON）。

### 4.2 Project 资源引用（ProjectResourceRef）
- Project 对资源的显式引用，作为依赖声明层。  
- 引用范围限定在同一 Workspace 内，禁止跨 Workspace 引用。  
- 引用只用于治理与视图，不限制运行时依赖。  

### 4.3 项目依赖图
依赖图由两部分构成：
1. **显式引用资源**（ProjectResourceRef）  
2. **资源实现层解析的直接依赖**（`get_dependencies`）  
并以 Node/Edge 的标准结构返回。

## 5. 公开 API
### 5.1 Project 环境配置
- `GET /api/v1/projects/{project_uuid}/env-config`  
- `PUT /api/v1/projects/{project_uuid}/env-config`  
- `DELETE /api/v1/projects/{project_uuid}/env-config`  

### 5.2 Project 依赖图
- `GET /api/v1/projects/{project_uuid}/dependency-graph`  
返回聚合依赖图结构（nodes + edges）。

### 5.3 Project 资源引用
- `POST /api/v1/projects/{project_uuid}/resources`  
- `GET /api/v1/projects/{project_uuid}/resources`  
- `DELETE /api/v1/projects/{project_uuid}/resources/{resource_uuid}`  
用于声明式依赖，并保证同 Workspace 约束。

## 6. 环境配置使用约束（必须遵守）
**env_config 只作为外部注入的“可选上下文”，绝不可成为资源运行的硬依赖。**

### 允许场景
- Project 环境变量作为 Agent/Workflow 的可选输入或提示词增强。  
- Project 环境变量作为资源执行时的默认参数（可覆盖）。  

### 禁止场景
- 资源执行必须依赖 Project env_config。  
- Project env_config 缺失导致资源失败。  

## 7. 依赖图语义约束（必须遵守）
1. Project 依赖图 **不是** `main_resource` 的依赖图。  
2. Project 依赖图应聚合所有显式引用资源及其实现层解析依赖。  
3. 依赖图用于治理和可视化，不限制运行时实际依赖来源。  

## 8. 开发与演进注意事项
1. **保持入口与依赖视图的语义分离**  
   - `main_resource` 用于入口定位。  
   - `dependency-graph` 用于全量依赖视图。  

2. **资源必须保持可移植性**  
   - 任何资源必须在无 Project 上下文情况下可运行。  

3. **依赖解析在实现层完成**  
   - 每个资源类型必须实现 `get_dependencies`，以便 Project 聚合依赖图。  

---

> 文档约束：以上定义与约束为唯一正确语义，后续新增功能必须遵循本文件的“禁止/允许”规则。
