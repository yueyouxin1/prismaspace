# engine/tool/main.py

import httpx
import jsonschema
from typing import Any, Dict, List, Literal

from ..schemas.parameter_schema import ParameterSchema
from ..utils.parameter_schema_utils import schemas2obj, build_json_schema_node
from .callbacks import ToolEngineCallbacks

class ToolEngineService:
    """
    纯粹的、无状态的 Tool 执行引擎。
    它完全不依赖任何数据库模型，只接收原生数据类型。
    """
    def __init__(self):
        self.http_client = httpx.AsyncClient(timeout=30.0)

    async def run(
        self,
        # --- [核心修正] 引擎的所有输入都是原生数据类型 ---
        method: Literal["GET", "POST", "PUT", "DELETE", "PATCH"],
        url: str,
        inputs_schema: List[ParameterSchema],
        outputs_schema: List[ParameterSchema],
        runtime_arguments: Dict[str, Any],
        callbacks: ToolEngineCallbacks,
        execution_context: Dict[str, Any], # 用于日志和追踪的上下文
        return_raw_response: bool = False
    ) -> Dict[str, Any] | Any:
        """
        [统一方法] 执行一个 Tool 定义。
        """
        await callbacks.on_start(execution_context, runtime_arguments)

        try:

            await self._validate_inputs(inputs_schema, runtime_arguments)
            await callbacks.on_log("Input validation successful.")
            
            request_parts = await self._build_request_parts(inputs_schema, runtime_arguments)
            formatted_url = self._format_url(url, request_parts['url_params'])
            await callbacks.on_log(f"Executing {method} request to {formatted_url}", metadata=request_parts)
            
            raw_response = await self._execute_request(
                method=method, url=formatted_url, headers=request_parts['headers'],
                params=request_parts['query_params'], json_body=request_parts['body']
            )
            await callbacks.on_log("Request successful.", metadata={"raw_response_preview": str(raw_response)[:500]})
            
            if return_raw_response:
                await callbacks.on_success(raw_response, raw_response)
                return raw_response

            await self._validate_outputs(outputs_schema, raw_response)
            await callbacks.on_log("Output validation successful.")

            shaped_output = await schemas2obj(outputs_schema, context={}, real_data=raw_response)
            
            await callbacks.on_success(shaped_output, raw_response)
            return shaped_output

        except Exception as e:
            await callbacks.on_error(e)
            raise

    # ... _validate_inputs, _validate_outputs, _build_request_parts, _format_url, _execute_request 等私有方法保持不变 ...
    # 它们已经只依赖原生数据类型，无需修改。

    async def _validate_inputs(self, inputs_schema: List[ParameterSchema], inputs: Dict[str, Any]):
        """使用 jsonschema 验证输入是否符合规范。"""
        if not inputs_schema:
            return # 没有定义 schema，跳过验证
        properties = {}
        required = []
        # 将我们的 ParameterSchema 转换为标准 JSON Schema
        for param in inputs_schema:
            if param.name:
                properties[param.name] = build_json_schema_node(param)
            if param.required:
                required.append(param.name)

        schema_root = {
            "type": "object",
            "properties": properties,
            "required": required
        }
        
        try:
            jsonschema.validate(instance=inputs, schema=schema_root)
        except jsonschema.ValidationError as e:
            # 抛出一个更具体的、可被上层捕获的异常
            raise ValueError(f"Input validation failed: {e.message}")

    async def _validate_outputs(self, outputs_schema: List[ParameterSchema], response: Dict[str, Any]):
        """验证工具的输出是否符合规范。"""
        # 逻辑与 _validate_inputs 类似，但针对 outputs_schema
        if not outputs_schema:
            return
        
        schema_root = {
            "type": "object",
            "properties": {param.name: build_json_schema_node(param) for param in outputs_schema if param.name},
            # 输出通常不强制要求所有字段都存在
        }
        
        try:
            jsonschema.validate(instance=response, schema=schema_root)
        except jsonschema.ValidationError as e:
            raise ValueError(f"Output validation failed: {e.message}")

    async def _build_request_parts(self, inputs_schema: List[ParameterSchema], runtime_arguments: Dict) -> Dict:
        """
        Builds all parts of the HTTP request by shaping a complete object from the inputs_schema,
        which merges defaults and runtime arguments, and then distributing the values based on role.
        """
        full_request_obj = await schemas2obj(inputs_schema, context={}, real_data=runtime_arguments)
        url_params, headers, query_params = {}, {}, {}
        body_obj = {}

        for param in inputs_schema:
            name = param.name
            role = param.role
            if not name or not role: continue

            value = full_request_obj.get(name)
            if value is None: continue

            if role == 'http.path': url_params[name] = str(value)
            elif role == 'http.header': headers[name] = str(value)
            elif role == 'http.query': query_params[name] = value
            elif role == 'http.body': body_obj[name] = value
        
        # Now handle complex body. If only one body param and it's an object, it might be the whole body
        # This logic can be refined, but it's a solid start.
        final_body = None
        if body_obj:
            # If multiple fields have role 'http.body', wrap them in an object.
            final_body = body_obj

        return {
            "url_params": url_params, "headers": headers,
            "query_params": query_params, "body": final_body
        }

    def _format_url(self, base_url: str, url_params: Dict[str, str]) -> str:
        """
        如果模板需要的参数没有被提供，它会立即因 KeyError 而失败。
        """
        try:
            for name, value in url_params.items():
                base_url = base_url.replace(f"{{{name}}}", value)
            return base_url
        except KeyError as e:
            raise ValueError(
                f"URL substitution failed. The URL template requires path parameter '{e.args[0]}', "
                "which was not found in the provided inputs with role 'http.path'."
            )

    async def _execute_request(self, method: str, url: str, headers: Dict, params: Dict, json_body: Dict) -> Any:
        """核心的 HTTP 请求执行逻辑。"""
        try:
            response = await self.http_client.request(
                method=method.upper(), url=url, headers=headers,
                params=params, json=json_body
            )
            response.raise_for_status()
            # 假设所有工具都返回 JSON
            return response.json()
        except httpx.HTTPStatusError as e:
            # 捕获 HTTP 错误并提供更多上下文
            raise IOError(f"HTTP error {e.response.status_code} for {e.request.url}: {e.response.text}")
        except httpx.RequestError as e:
            # 捕获网络层面的错误
            raise IOError(f"Request failed for {e.request.url}: {e}")