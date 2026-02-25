# src/app/schemas/resource/tenantdb/tenantdb_schemas.py

from pydantic import BaseModel, Field, ConfigDict, field_validator, model_validator
from typing import Optional, List, Any, Dict, Union, Literal
from app.models.resource.tenantdb import TenantDataType
from app.schemas.common import ExecutionRequest, ExecutionResponse

# --- Column Schemas ---

class TenantColumnBase(BaseModel):
    name: str = Field(..., max_length=63, pattern=r'^[a-zA-Z_][a-zA-Z0-9_]*$', 
                      description="列名，必须符合PostgreSQL标识符规范 (字母或下划线开头，后跟字母、数字或下划线)")
    label: str = Field(..., min_length=1, max_length=255, description="友好名称")
    description: Optional[str] = None
    data_type: TenantDataType
    is_nullable: bool = Field(True)
    is_unique: bool = Field(False)
    is_indexed: bool = Field(False)
    is_vector_enabled: bool = Field(False)
    default_value: Optional[Any] = Field(None, description="类型安全的默认值")

class TenantColumnCreate(TenantColumnBase):
    
    # [关键决策] 移除用户自定义主键的能力，由系统统一管理。
    # is_primary_key: bool = Field(False) 

    # [改进] 添加模型验证器，确保 default_value 与 data_type 逻辑一致
    @model_validator(mode='before')
    @classmethod
    def check_default_value_type(cls, data: Any) -> Any:
        if not isinstance(data, dict):
            return data
            
        default_value = data.get('default_value')
        if default_value is None:
            return data

        data_type = data.get('data_type')
        if not data_type:
            # 如果没有data_type，无法验证，让其他验证器处理
            return data

        type_map = {
            TenantDataType.TEXT: str,
            TenantDataType.INTEGER: int,
            TenantDataType.NUMBER: (int, float),
            TenantDataType.BOOLEAN: bool,
            # TIMESTAMP and JSON are more complex, often received as strings.
            # We can add more robust parsing here if needed.
        }

        expected_type = type_map.get(TenantDataType(data_type))
        if expected_type and not isinstance(default_value, expected_type):
            raise ValueError(f"Default value for type '{data_type}' must be of type '{expected_type.__name__}'")
        
        return data

class TenantColumnUpdate(TenantColumnCreate):
    uuid: str # 关键：更新时必须提供uuid来识别列

class TenantColumnRead(TenantColumnBase):
    uuid: str
    is_primary_key: bool # [新增] 在Read模型中返回主键信息
    model_config = ConfigDict(from_attributes=True)

# --- Table Schemas ---

class TenantTableBase(BaseModel):
    name: str = Field(..., max_length=63, pattern=r'^[a-zA-Z_][a-zA-Z0-9_]*$', 
                      description="表名，必须符合PostgreSQL标识符规范")
    label: str = Field(..., min_length=1, max_length=255, description="友好名称")
    description: Optional[str] = None

class TenantTableCreate(TenantTableBase):
    columns: List[TenantColumnCreate] = Field(..., min_length=1, description="表的列定义列表")

    @field_validator('columns')
    @classmethod
    def unique_column_names(cls, v: List[TenantColumnCreate]):
        seen_names = set()
        # [REFACTOR 2] 将系统列也加入检查，防止用户定义冲突
        system_reserved_names = {'id', 'created_at'}
        
        for column in v:
            if column.name.lower() in seen_names:
                raise ValueError(f"Duplicate column name '{column.name}' found.")
            if column.name.lower() in system_reserved_names:
                raise ValueError(f"Column name '{column.name}' is reserved by the system.")
            seen_names.add(column.name.lower())
        return v

class TenantTableUpdate(TenantTableBase):
    # 更新时，name/label/description都是可选的
    name: Optional[str] = Field(None, max_length=63, pattern=r'^[a-zA-Z_][a-zA-Z0-9_]*$')
    label: Optional[str] = Field(None, min_length=1, max_length=255)
    
    # 允许传入一个完整的列列表，服务层将进行同步
    columns: Optional[List[Union[TenantColumnUpdate, TenantColumnCreate]]] = None
    
class TenantTableRead(TenantTableBase):
    uuid: str
    columns: List[TenantColumnRead]
    model_config = ConfigDict(from_attributes=True)

# --- TenantDB Schemas (Top-Level) ---
class TenantDBUpdate(BaseModel):
    pass

class TenantDBRead(BaseModel):
    uuid: str
    name: str
    version_tag: str
    status: str
    schema_name: str
    tables: List[TenantTableRead]

    model_config = ConfigDict(from_attributes=True)

# --- Schemas for Data Execution ---

class TenantDbExecutionParams(BaseModel):
    """
    统一的TenantDB执行请求体。
    这个模型将作为 POST /execute/... 的JSON body。
    """
    action: Literal["query", "get_one", "insert", "update", "delete", "raw_sql"] = Field(..., description="要执行的操作类型")
    table_name: str = Field(..., description="操作的目标表名")
    
    # 用于 query, get_one, update, delete
    filters: Optional[Union[Dict[str, Any], List[List[Any]]]] = Field(None, description="查询过滤器，支持 {key:val} 或 [[key, op, val]] 格式")

    columns: Optional[List[str]] = Field(None, description="字段选择")

    # 用于 insert, update
    payload: Optional[Union[Dict[str, Any], List[Dict[str, Any]]]] = Field(None, description="用于插入或更新的数据")
    
    # 用于 query
    page: int = Field(1, ge=1, description="页码")
    limit: int = Field(10, ge=1, le=1000, description="每页数量")
    order_by: Optional[str] = Field(None, description="排序字段，例如 'created_at DESC'")
    
    # 用于 raw_sql
    raw_sql: Optional[str] = Field(None, description="要执行的原始SELECT SQL查询")

    @model_validator(mode='before')
    @classmethod
    def check_action_requirements(cls, data: Any) -> Any:
        if not isinstance(data, dict): return data
        
        action = data.get('action')
        if action in ["update", "delete"] and not data.get('filters'):
            raise ValueError(f"Action '{action}' requires 'filters' to be specified.")
        if action in ["insert", "update"] and not data.get('payload'):
            raise ValueError(f"Action '{action}' requires 'payload' to be provided.")
        if action == "raw_sql" and not data.get('raw_sql'):
            raise ValueError("Action 'raw_sql' requires 'raw_sql' to be provided.")
            
        return data

class TenantDbExecutionRequest(ExecutionRequest):
    inputs: TenantDbExecutionParams = Field(..., description="运行时参数")
    
class TenantDbExecutionResponse(ExecutionResponse):
    """
    统一的TenantDB执行响应体。
    """
    success: bool = True
    # 'data' 可以是列表（查询结果）、字典（单条记录）或整数（受影响的行数）
    data: Union[List[Dict[str, Any]], Dict[str, Any], int]
    count: Optional[int] = Field(None, description="对于'query'操作，返回符合条件的总行数")
    model_config = ConfigDict(from_attributes=True) 