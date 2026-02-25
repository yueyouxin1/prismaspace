# src/app/services/resource/tenantdb_service.py

import uuid
import re
from decimal import Decimal
from pydantic import ValidationError
from typing import Any, Dict, Optional, List, Set, Tuple, Union
from sqlalchemy.ext.asyncio import AsyncSession, AsyncConnection
from sqlalchemy import (
    Table, Column, MetaData, Text, BigInteger, Numeric, Boolean, 
    TIMESTAMP, JSON as PG_JSON, text, select, inspect, insert, update, delete, func,
    bindparam
)
from sqlalchemy.types import TypeEngine
from sqlalchemy.sql import quoted_name
from sqlalchemy.schema import CreateSchema, CreateTable, DropSchema, Index
from sqlalchemy.exc import SQLAlchemyError, ProgrammingError
from app.core.context import AppContext
from app.db.tenant_db_session import tenant_data_engine
from app.models import User, Team, Workspace
from app.models.resource import Resource, VersionStatus
from app.models.resource.tenantdb import TenantDB, TenantTable, TenantColumn, TenantDataType
from app.schemas.resource.tenantdb.tenantdb_schemas import TenantDBUpdate, TenantTableCreate, TenantTableUpdate, TenantDBRead, TenantColumnCreate, TenantColumnUpdate, TenantDbExecutionRequest, TenantDbExecutionResponse
from app.services.billing.context import BillingContext
from .base.base_impl_service import register_service, ResourceImplementationService, ValidationResult, DependencyInfo
from app.services.exceptions import ServiceException, NotFoundError
from app.dao.resource.tenantdb.tenantdb_dao import TenantDBDao, TenantTableDao
from app.engine.model.llm import LLMTool, LLMToolFunction
from app.core.trace_manager import TraceManager
from app.services.auditing.types.attributes import TenantDBAttributes, TenantDBMeta

@register_service
class TenantDbService(ResourceImplementationService):

    name: str = "tenantdb"

    # --- [REFACTOR 1] 添加PostgreSQL保留关键字列表 (这是一个简化列表，生产环境应更全面)
    PG_RESERVED_WORDS: Set[str] = { "all", "analyse", "analyze", "and", "any", "array", "as", "asc", "asymmetric", "authorization", "binary", "both", "case", "cast", "check", "collate", "column", "concurrently", "constraint", "create", "cross", "current_catalog", "current_date", "current_role", "current_time", "current_timestamp", "current_user", "default", "deferrable", "desc", "distinct", "do", "else", "end", "except", "false", "fetch", "for", "foreign", "freeze", "from", "full", "grant", "group", "having", "ilike", "in", "initially", "inner", "intersect", "into", "is", "isnull", "join", "lateral", "leading", "left", "like", "limit", "localtime", "localtimestamp", "natural", "not", "notnull", "null", "offset", "on", "only", "or", "order", "outer", "overlaps", "placing", "primary", "references", "returning", "right", "select", "session_user", "similar", "some", "symmetric", "table", "tablesample", "then", "to", "trailing", "true", "union", "unique", "user", "using", "variadic", "verbose", "when", "where", "window", "with" }

    def __init__(self, context: AppContext):
        super().__init__(context)
        self.dao = TenantDBDao(context.db)
        self.table_dao = TenantTableDao(context.db)
        # 数据平面 engine
        # self.data_plane_engine = tenant_data_engine

    # 核心辅助函数，用于DML操作
    def _get_sqlalchemy_table_object(self, table_meta: TenantTable, schema: str) -> Table:
        """Dynamically builds a complete SQLAlchemy Table object from our ORM metadata."""
        columns = [Column(c.name, self._map_type(c.data_type)) for c in table_meta.columns]
        return Table(table_meta.name, MetaData(), *columns, schema=schema)

    # --- [全新] 数据执行 (DML) 核心逻辑 ---
    
    async def execute(
        self, 
        instance_uuid: str,
        execute_params: TenantDbExecutionRequest,
        actor: User,
        runtime_workspace: Optional[Workspace] = None
    ) -> TenantDbExecutionResponse:

        """
        [全新] TenantDB 的统一执行入口，一个强大的JSON-to-SQL网关。
        """
        instance = await self.get_by_uuid(instance_uuid)
        await self._check_execute_perm(instance)
        workspace = runtime_workspace or instance.resource.workspace
        billing_entity = workspace.billing_owner
        inputs = execute_params.inputs
        action = inputs.action
        table_name = inputs.table_name
        filters = inputs.filters
        payload = inputs.payload
        
        if not table_name:
            raise ServiceException("'table_name' is required.")

        table_meta = next((t for t in instance.tables if t.name == table_name), None)
        if not table_meta:
            raise NotFoundError(f"Table '{table_name}' not found in this TenantDB instance.")
        
        sa_table = self._get_sqlalchemy_table_object(table_meta, instance.schema_name)
        params_dict = inputs.model_dump()

        # 1. 准备 Trace Attributes
        trace_attrs = TenantDBAttributes(
            inputs=inputs, # 直接传入 Pydantic 模型，TraceManager 会自动 dump
            meta=TenantDBMeta(
                table_name=inputs.table_name,
                action=inputs.action,
                sql_preview=inputs.raw_sql if inputs.action == 'raw_sql' else None
            )
        )

        async with TraceManager(
            db=self.db,
            operation_name="tenantdb.execute",
            user_id=actor.id,
            target_instance_id=instance.id,
            attributes=trace_attrs
        ) as span:

            # --- Action Dispatcher ---
            if action == "query":
                rows, count = await self._query_rows(sa_table, params_dict)
                result = TenantDbExecutionResponse(data=rows, count=count)
            elif action == "get_one":
                row = await self._get_one_row(sa_table, params_dict)
                result = TenantDbExecutionResponse(data=row)
            elif action == "insert":
                inserted = await self._insert_rows(sa_table, payload)
                result = TenantDbExecutionResponse(data=inserted)
            elif action == "update":
                updated_count = await self._update_rows(sa_table, payload, filters)
                result = TenantDbExecutionResponse(data=updated_count)
            elif action == "delete":
                deleted_count = await self._delete_rows(sa_table, filters)
                result = TenantDbExecutionResponse(data=deleted_count)
            elif action == "raw_sql":
                rows = await self._execute_raw_sql(instance, inputs.raw_sql or "")
                result = TenantDbExecutionResponse(data=rows)
            else:
                raise ServiceException(f"Unsupported action '{action}' for TenantDB.")

            span.set_output(result)

            return result

    async def execute_batch(
        self,
        instance_uuids: List[str],
        execute_params: TenantDbExecutionRequest,
        actor: User,
        runtime_workspace: Optional[Workspace] = None
    ) -> List[TenantDbExecutionResponse]:
        pass

    def _build_where_clause(
        self,
        sa_table: Table,
        valid_columns: Set[str],
        filters: Optional[Union[Dict, List]]
    ) -> Tuple[List, Dict]:
        """
        [核心] 安全地从JSON构建SQLAlchemy WHERE子句。
        直接借鉴并优化了您提供的旧架构逻辑。
        """
        if not filters:
            return [], {}
        
        clauses = []
        bind_params = {}
        param_counter = 0

        def get_param_name():
            nonlocal param_counter
            name = f"p_{param_counter}"
            param_counter += 1
            return name

        if isinstance(filters, dict):
            for key, value in filters.items():
                if key not in valid_columns: continue
                param_name = get_param_name()
                clauses.append(sa_table.c[key] == bindparam(param_name))
                bind_params[param_name] = value
        
        elif isinstance(filters, list):
            for cond in filters:
                if not isinstance(cond, list) or len(cond) != 3: continue
                key, op, value = cond
                if key not in valid_columns: continue
                
                op = op.strip().lower()
                param_name = get_param_name()
                
                op_map = {
                    "=": sa_table.c[key] == bindparam(param_name),
                    "!=": sa_table.c[key] != bindparam(param_name),
                    ">": sa_table.c[key] > bindparam(param_name),
                    "<": sa_table.c[key] < bindparam(param_name),
                    ">=": sa_table.c[key] >= bindparam(param_name),
                    "<=": sa_table.c[key] <= bindparam(param_name),
                    "like": sa_table.c[key].like(bindparam(param_name)),
                    "in": sa_table.c[key].in_(bindparam(param_name)),
                    "not in": sa_table.c[key].not_in(bindparam(param_name)),
                }
                
                if op not in op_map: continue
                clauses.append(op_map[op])
                bind_params[param_name] = value
        
        return clauses, bind_params

    async def _query_rows(self, sa_table: Table, params: Dict) -> Tuple[List[Dict], int]:
        from app.db.tenant_db_session import tenant_data_engine
        valid_columns = {c.name for c in sa_table.columns}
        
        where_clauses, bind_params = self._build_where_clause(sa_table, valid_columns, params.get("filters"))

        async with tenant_data_engine.connect() as conn:
            # [修正] 使用同一个 sa_table 对象
            count_stmt = select(func.count(text("1"))).select_from(sa_table).where(*where_clauses)
            count_result = await conn.execute(count_stmt, bind_params)
            total_count = count_result.scalar_one()

            select_cols = sa_table.c # Select all columns from the constructed table
            select_fields_raw = params.get("columns")
            if select_fields_raw and isinstance(select_fields_raw, list):
                select_cols = [sa_table.c[col_name] for col_name in select_fields_raw if col_name in valid_columns]

            stmt = select(*select_cols).select_from(sa_table).where(*where_clauses)
            limit = params.get("limit", 10)
            page = params.get("page", 1)
            stmt = stmt.limit(limit).offset((page - 1) * limit)
            order_by_str = params.get("order_by")
            if order_by_str:
                clauses = []
                for part in order_by_str.split(','):
                    part = part.strip()
                    if not part: continue
                    match = re.match(r'^([a-zA-Z0-9_]+)\s*(ASC|DESC)?$', part, re.IGNORECASE)
                    if not match: raise ServiceException(f"Invalid order_by clause: {part}")
                    col_name, direction = match.groups()
                    if col_name not in valid_columns: raise ServiceException(f"Invalid column for ordering: {col_name}")
                    sa_col = sa_table.c[col_name]
                    clauses.append(sa_col.desc() if direction and direction.upper() == 'DESC' else sa_col.asc())
                if clauses: stmt = stmt.order_by(*clauses)
            data_result = await conn.execute(stmt, bind_params)
            rows = [dict(row) for row in data_result.mappings()]
            return rows, total_count

    async def _get_one_row(self, sa_table: Table, params: Dict) -> Optional[Dict]:
        params = {**params, "action": "query", "limit": 1, "page": 1}
        rows, _ = await self._query_rows(sa_table, params)
        return rows[0] if rows else None

    async def _insert_rows(self, sa_table: Table, payload: Union[Dict, List[Dict]]) -> Union[Dict, List[Dict]]:
        if not payload: return []
        data_to_insert = [payload] if isinstance(payload, dict) else payload
        
        stmt = insert(sa_table).values(data_to_insert).returning(*sa_table.c)

        from app.db.tenant_db_session import tenant_data_engine
        async with tenant_data_engine.connect() as conn:
            result = await conn.execute(stmt)
            await conn.commit()
            inserted_rows = [dict(row) for row in result.mappings()]
            return inserted_rows[0] if isinstance(payload, dict) else inserted_rows

    async def _update_rows(self, sa_table: Table, payload: Dict, filters: Union[Dict, List]) -> int:
        valid_columns = {c.name for c in sa_table.columns}
        
        where_clauses, bind_params = self._build_where_clause(sa_table, valid_columns, filters)
        
        stmt = update(sa_table).where(*where_clauses).values(payload)

        from app.db.tenant_db_session import tenant_data_engine
        async with tenant_data_engine.connect() as conn:
            result = await conn.execute(stmt, bind_params)
            await conn.commit()
            return result.rowcount

    async def _delete_rows(self, sa_table: Table, filters: Union[Dict, List]) -> int:
        valid_columns = {c.name for c in sa_table.columns}

        where_clauses, bind_params = self._build_where_clause(sa_table, valid_columns, filters)
        
        stmt = delete(sa_table).where(*where_clauses)

        from app.db.tenant_db_session import tenant_data_engine
        async with tenant_data_engine.connect() as conn:
            result = await conn.execute(stmt, bind_params)
            await conn.commit()
            return result.rowcount

    async def _execute_raw_sql(self, instance: TenantDB, raw_sql: str) -> List[Dict]:
        """
        [安全关键] 只执行SELECT语句，并设置默认超时和行数限制。
        """
        clean_sql = raw_sql.strip()
        if not re.match(r"^select", clean_sql, re.IGNORECASE):
            raise ServiceException("For security reasons, only SELECT statements are allowed in raw_sql.")

        # 在查询中注入 schema search_path, 这是一个比直接拼接schema更安全的模式
        sql_to_execute = f"SET search_path TO {quoted_name(instance.schema_name, quote=True)}; {clean_sql};"
        
        try:
            from app.db.tenant_db_session import tenant_data_engine
            async with tenant_data_engine.connect() as conn:
                # 设置语句超时以防止恶意长查询
                await conn.execute(text("SET statement_timeout = '5s';"))
                result = await conn.execute(text(sql_to_execute))
                rows = result.mappings().all()
                # 限制最大返回行数
                return [dict(row) for row in rows[:500]]
        except ProgrammingError as e:
            # 捕获SQL语法错误等
            raise ServiceException(f"SQL execution error: {e.orig}")
        except SQLAlchemyError as e:
            raise ServiceException(f"Database error: {e}")

    # --- 1. CRUD & Lifecycle (接口实现) 上游负责权限认证 ---

    async def serialize_instance(self, instance: TenantDB) -> Dict[str, Any]:
        """将TenantDB ORM对象序列化为API响应字典。"""
        return TenantDBRead.model_validate(instance).model_dump()

    async def get_by_uuid(self, instance_uuid: str) -> Optional[TenantDB]:
        """获取完整的TenantDB实例，并预加载其所有表和列。"""
        return await self.dao.get_by_uuid(
            instance_uuid,
            withs=[{"name": "tables", "withs": ["columns"]}]
        )

    async def create_instance(self, resource: Resource, actor: User) -> TenantDB:
        """创建TenantDB实例的核心逻辑。"""
        schema_name = f"tenant_{uuid.uuid4().hex}"
        
        # 1. 尝试在数据平面创建Schema，如果失败，整个操作将中止
        await self._execute_create_schema(schema_name)
        
        # 2. Schema创建成功后，创建元数据对象
        instance = TenantDB(
            version_tag="__workspace__",
            status=VersionStatus.WORKSPACE,
            creator_id=actor.id,
            resource_type="tenantdb",
            name=resource.name,
            schema_name=schema_name,
            resource=resource
        )
        return instance

    async def update_instance(self, instance: TenantDB, update_data: Dict[str, Any]) -> TenantDB:
        """
        [标准实现] 更新TenantDB实例自身的元数据。
        注意：此方法不处理表或列的变更，这些由专门的方法处理。
        """
        try:
            # 验证传入的数据，即使当前没有字段
            validated_data = TenantDBUpdate.model_validate(update_data)
        except ValidationError as e:
            raise e # 将 Pydantic 错误向上抛出
        update_dict = validated_data.model_dump(exclude_unset=True)
        
        # 如果未来 TenantDB 模型增加了字段，更新逻辑会放在这里
        # for key, value in update_dict.items():
        #     setattr(instance, key, value)
            
        return instance

    async def delete_instance(self, instance: TenantDB) -> None:
        await self.db.delete(instance)

    async def on_resource_delete(self, resource: Resource) -> None:
        """
        [Clean] 资源级物理删除。
        契约保证：resource.workspace_instance 是 TenantDB 类型且已加载。
        """
        instance: TenantDB = resource.workspace_instance
        
        # 直接访问子类字段 schema_name，无需任何查询或类型检查
        print(f"[TenantDb] Dropping physical schema '{instance.schema_name}' for resource {resource.uuid}")
        
        # 执行物理删除
        await self._execute_drop_schema(instance.schema_name)

    async def publish_instance(self, workspace_instance: TenantDB, version_tag: str, version_notes: Optional[str], actor: User) -> TenantDB:
        """[关键实现] 创建元数据快照，不操作数据平面。"""
        snapshot = TenantDB(
            resource_id=workspace_instance.resource_id,
            status=VersionStatus.PUBLISHED,
            version_tag=version_tag,
            version_notes=version_notes,
            creator_id=actor.id,
            published_at=text('now()'),
            name=workspace_instance.name,
            description=workspace_instance.description,
            # [核心] 快照共享同一个物理 schema
            schema_name=workspace_instance.schema_name 
        )
        
        # 深拷贝表和列的元数据定义
        tables = await self.get_tables(workspace_instance.uuid)
        for source_table in tables:
            new_table = TenantTable(name=source_table.name, label=source_table.label, description=source_table.description)
            for source_col in source_table.columns:
                new_table.columns.append(TenantColumn(
                    name=source_col.name, label=source_col.label, description=source_col.description,
                    data_type=source_col.data_type, is_primary_key=source_col.is_primary_key,
                    is_nullable=source_col.is_nullable, is_unique=source_col.is_unique,
                    default_value=source_col.default_value, is_vector_enabled=source_col.is_vector_enabled
                ))
            snapshot.tables.append(new_table)
        return snapshot

    # --- 2. Validation & Pre-flight Checks (接口实现) ---

    async def validate_instance(self, instance: TenantDB) -> ValidationResult:
        errors = []
        if not instance.schema_name:
            errors.append("Fatal: Missing physical schema.")
        if not instance.tables:
            errors.append("数据库必须至少包含一个表才能发布。")
        return ValidationResult(is_valid=not errors, errors=errors)

    # --- 3. Dependency Resolution (接口实现) ---

    async def get_dependencies(self, instance: TenantDB) -> List[DependencyInfo]:
        return [] # TenantDB 是数据源，没有下游资源依赖

    # --- 4. Discovery & Indexing (接口实现) ---

    async def get_searchable_content(self, instance: TenantDB) -> str:
        content = [instance.name, instance.description or ""]
        for table in instance.tables:
            content.append(table.name)
            content.append(table.description or "")
            for col in table.columns:
                content.append(col.name)
                content.append(col.description or "")
        return " ".join(filter(None, content))

    async def as_llm_tool(self, instance: TenantDB) -> Optional[LLMTool]:
        return None

    # --- 元数据管理 (DDL) 核心逻辑 ---

    async def get_tables(self, instance_uuid: str) -> List[TenantTable]:
        """获取一个TenantDB实例下的所有表的元数据。"""
        # DAO的withs预加载已经处理了列信息，这里直接返回即可
        # 1. 权限和实例加载
        instance = await self.get_by_uuid(instance_uuid)
        await self.context.perm_evaluator.ensure_can(["resource:read"], target=instance.resource.workspace)
        return instance.tables

    async def get_table_by_uuid(self, instance_uuid: str, table_uuid: str) -> TenantTable:
        # 1. 权限和实例加载
        instance = await self.dao.get_by_uuid(instance_uuid)
        await self.context.perm_evaluator.ensure_can(["resource:read"], target=instance.resource.workspace)
        """通过UUID获取表的元数据。"""
        table_meta = await self.table_dao.get_one(
            where={"uuid": table_uuid, "tenantdb_id": instance.version_id},
            withs=["columns"]
        )
        if not table_meta:
            raise NotFoundError(f"Table with UUID '{table_uuid}' not found.")
        return table_meta

    async def update_table(self, instance_uuid: str, table_uuid: str, update_data: TenantTableUpdate) -> TenantTable:
        """
        更新表的元数据（如重命名）和列结构。
        此方法现在是一个编排器，将复杂逻辑委托给辅助方法。
        """
        # 1. 权限和实例加载
        instance = await self.dao.get_by_uuid(instance_uuid)
        await self.context.perm_evaluator.ensure_can(["resource:update"], target=instance.resource.workspace)     
        table_meta = await self.get_table_by_uuid(instance_uuid, table_uuid)
        original_name = table_meta.name

        # 1. 物理重命名 (如果需要)
        if update_data.name and update_data.name != original_name:
            self._validate_identifier(update_data.name)
            await self._execute_rename_table(instance.schema_name, original_name, update_data.name)
            table_meta.name = update_data.name
        
        if update_data.label is not None:
            table_meta.label = update_data.label
        if update_data.description is not None:
            table_meta.description = update_data.description
        
        # 2. 列结构变更 (Add, Update, Delete, Rename, Index)
        if update_data.columns is not None:
            await self._sync_columns(instance.schema_name, table_meta, update_data.columns)

        await self.db.flush()
        # [关键] 必须在 refresh 时指定 relationships，特别是嵌套的
        await self.db.refresh(table_meta, attribute_names=['columns'])

        return table_meta

    async def delete_table(self, instance_uuid: str, table_uuid: str):
        """删除一张表（元数据和物理表）。"""
        # 1. 权限和实例加载
        instance = await self.dao.get_by_uuid(instance_uuid)
        await self.context.perm_evaluator.ensure_can(["resource:update"], target=instance.resource.workspace)   
        table_meta = await self.get_table_by_uuid(instance_uuid, table_uuid)
        
        # 补偿事务：先删物理表
        await self._execute_drop_table(instance.schema_name, table_meta.name)
        
        # 再删元数据
        await self.db.delete(table_meta)
        await self.db.flush()

    # --- [全新] DDL辅助函数 ---

    async def _sync_columns(self, schema_name: str, table_meta: TenantTable, desired_columns_data: List[Union[TenantColumnCreate, TenantColumnUpdate]]):
        current_cols_by_uuid = {str(c.uuid): c for c in table_meta.columns if not c.is_primary_key and c.name != 'created_at'}
        to_update_map = {str(c.uuid): c for c in desired_columns_data if hasattr(c, 'uuid') and c.uuid}
        to_add_list = [c for c in desired_columns_data if not (hasattr(c, 'uuid') and c.uuid)]
        uuids_to_delete = set(current_cols_by_uuid.keys()) - set(to_update_map.keys())

        # --- Phase 1: Column Structure Sync ---
        async with tenant_data_engine.begin() as conn:
            for col_uuid in uuids_to_delete:
                col_to_delete = current_cols_by_uuid[col_uuid]
                await self._execute_alter_table(conn, schema_name, table_meta.name, 'DROP', col_to_delete)
                await self.db.delete(col_to_delete)
            
            for new_col_data in to_add_list:
                self._validate_identifier(new_col_data.name)
                new_col_meta = TenantColumn(**new_col_data.model_dump(), table_id=table_meta.id)
                await self._execute_alter_table(conn, schema_name, table_meta.name, 'ADD', new_col_meta)
                self.db.add(new_col_meta)

            for col_uuid, desired_state in to_update_map.items():
                current_state = current_cols_by_uuid.get(col_uuid)
                if not current_state: continue
                
                if desired_state.name != current_state.name:
                    self._validate_identifier(desired_state.name)
                    await self._execute_rename_column(conn, schema_name, table_meta.name, current_state.name, desired_state.name)
                
                # Update ORM state for Phase 2 and for the final response
                current_state.name = desired_state.name
                current_state.label = desired_state.label
                current_state.description = desired_state.description
                current_state.is_indexed = desired_state.is_indexed

        # --- Phase 2: Index Sync ---
        async with tenant_data_engine.begin() as conn:
            def inspect_sync(sync_conn):
                inspector = inspect(sync_conn)
                return inspector.get_indexes(table_meta.name, schema=schema_name)
            
            existing_indexes = await conn.run_sync(inspect_sync)
            index_map = {idx['column_names'][0]: idx['name'] for idx in existing_indexes if len(idx['column_names']) == 1}

            # The desired_columns_data IS the final state
            for col_state in desired_columns_data:
                # After phase 1, the name in DB matches col_state.name
                col_name = col_state.name
                should_have_index = col_state.is_indexed
                has_index = col_name in index_map

                if should_have_index and not has_index:
                    await self._execute_index_change(conn, schema_name, table_meta.name, col_name, 'CREATE_INDEX')
                elif not should_have_index and has_index:
                    index_name_to_drop = index_map[col_name]
                    await self._execute_index_change(conn, schema_name, table_meta.name, col_name, 'DROP_INDEX', explicit_index_name=index_name_to_drop)

    async def _execute_rename_table(self, schema: str, old_table_name: str, new_table_name: str):
        """安全地在数据平面重命名表。"""
        from app.db.tenant_db_session import tenant_data_engine
        stmt = text(
            f"ALTER TABLE {quoted_name(schema, True)}.{quoted_name(old_table_name, True)} "
            f"RENAME TO {quoted_name(new_table_name, True)}"
        )
        async with tenant_data_engine.begin() as conn:
            await conn.execute(stmt)

    # And update the helpers to accept the connection
    async def _execute_rename_column(self, conn: AsyncConnection, schema: str, table_name: str, old_col_name: str, new_col_name: str):
        stmt = text(
            f"ALTER TABLE {quoted_name(schema, True)}.{quoted_name(table_name, True)} "
            f"RENAME COLUMN {quoted_name(old_col_name, True)} TO {quoted_name(new_col_name, True)}"
        )
        await conn.execute(stmt)

    async def _execute_index_change(self, conn: AsyncConnection, schema: str, table_name: str, col_name: str, action: str, explicit_index_name: str = None):
        """
        Executes an index change.
        If explicit_index_name is provided for DROP, it will be used.
        Otherwise, the name is constructed from table and column names.
        """
        index_name = explicit_index_name or f"{table_name}_{col_name}_idx"
        
        table_name_safe = f"{quoted_name(schema, True)}.{quoted_name(table_name, True)}"
        index_name_safe = quoted_name(index_name, True)
        col_name_safe = quoted_name(col_name, True)

        if action == 'CREATE_INDEX':
            stmt = text(f"CREATE INDEX IF NOT EXISTS {index_name_safe} ON {table_name_safe} ({col_name_safe})")
        elif action == 'DROP_INDEX':
            # [核心修复] 使用 IF EXISTS 来增加健壮性
            stmt = text(f"DROP INDEX IF EXISTS {quoted_name(schema, True)}.{index_name_safe}")
        else:
            raise ValueError("Unsupported index action")
        
        await conn.execute(stmt)

    async def _execute_alter_table(self, conn: AsyncConnection, schema: str, table_name: str, action: str, column: TenantColumn):
        from app.db.tenant_db_session import tenant_data_engine
        col_name_safe = quoted_name(column.name, True)
        table_name_safe = f"{quoted_name(schema, True)}.{quoted_name(table_name, True)}"
        
        if action == 'ADD':
            col_type_str = self._map_type(column.data_type).compile(dialect=tenant_data_engine.dialect)
            stmt = text(f"ALTER TABLE {table_name_safe} ADD COLUMN {col_name_safe} {col_type_str}")
        elif action == 'DROP':
            stmt = text(f"ALTER TABLE {table_name_safe} DROP COLUMN {col_name_safe}")
        else:
            raise ValueError("Unsupported ALTER action")
        
        await conn.execute(stmt)

    def _validate_identifier(self, name: str):
        """[REFACTOR 1] 检查标识符是否为保留关键字。"""
        if name.lower() in self.PG_RESERVED_WORDS:
            raise ServiceException(f"Identifier '{name}' is a reserved keyword and cannot be used.")

    # --- 5. TenantDB 领域特定方法 ---
    
    async def create_table(self, instance_uuid: str, table_data: TenantTableCreate) -> TenantTable:
        # 1. 权限和实例加载
        instance = await self.dao.get_by_uuid(instance_uuid)
        await self.context.perm_evaluator.ensure_can(["resource:update"], target=instance.resource.workspace)
        # [REFACTOR 1] 在最开始就进行所有标识符的验证
        self._validate_identifier(table_data.name)
        for col in table_data.columns:
            self._validate_identifier(col.name)

        # [REFACTOR 4] 使用 .model_dump() 简化ORM对象创建
        table_meta_dict = table_data.model_dump(exclude={"columns"})
        new_table_meta = TenantTable(
            **table_meta_dict,
            tenant_db=instance
        )
        
        # 添加系统列到元数据中
        new_table_meta.columns.append(TenantColumn(name="id", data_type=TenantDataType.INTEGER, is_primary_key=True, is_nullable=False, label="唯一ID"))
        new_table_meta.columns.append(TenantColumn(name="created_at", data_type=TenantDataType.TIMESTAMP, is_nullable=False, label="创建时间"))
        
        # 添加用户定义的列
        for col_data in table_data.columns:
            new_table_meta.columns.append(TenantColumn(**col_data.model_dump()))
        
        self.db.add(new_table_meta)
        
        try:
            # 补偿事务的核心：先尝试物理操作
            await self._execute_create_table(new_table_meta, instance.schema_name)
            # 成功后，让外层事务提交元数据
            await self.db.flush()
            await self.db.refresh(new_table_meta, attribute_names=['columns'])
            return new_table_meta
        except Exception as e:
            # 失败后，外层事务将自动回滚元数据的添加
            print(f"ERROR: Failed to create physical table. Rolling back metadata changes. Error: {e}")
            raise ServiceException(f"Failed to create physical table: {str(e)}")
            
    # --- 类型映射辅助函数 ---
    def _map_type(self, tenant_type: TenantDataType) -> TypeEngine:
        """将我们的内部类型安全地映射到SQLAlchemy类型 *实例*。"""
        return {
            TenantDataType.TEXT: Text(),
            TenantDataType.INTEGER: BigInteger(),
            TenantDataType.NUMBER: Numeric(),
            TenantDataType.BOOLEAN: Boolean(),
            TenantDataType.TIMESTAMP: TIMESTAMP(timezone=True),
            TenantDataType.JSON: PG_JSON()
        }[tenant_type]

    # --- 数据平面 DDL 辅助函数 ---
    async def _execute_create_schema(self, schema_name: str):
        """在数据平面安全地创建Schema。"""
        from app.db.tenant_db_session import tenant_data_engine
        async with tenant_data_engine.begin() as conn:
            # 使用 CreateSchema 和 quoted_name 来防止SQL注入
            await conn.execute(CreateSchema(quoted_name(schema_name, quote=True), if_not_exists=True))

    async def _execute_create_table(self, table_meta: TenantTable, schema_name: str):
        from app.db.tenant_db_session import tenant_data_engine
        
        sqlalchemy_table = self._get_sqlalchemy_table_object(table_meta, schema_name)
        
        # The orchestrator now owns the transaction and connection
        async with tenant_data_engine.begin() as conn:
            # Step 1: Create the table
            await conn.run_sync(sqlalchemy_table.create)

            # Step 2: Create indexes on the same connection
            for col_meta in table_meta.columns:
                if col_meta.is_indexed:
                    await self._execute_index_change(
                        conn, # Pass the active connection down
                        schema_name, 
                        table_meta.name, 
                        col_meta.name, 
                        'CREATE_INDEX'
                    )

    async def _execute_drop_schema(self, schema_name: str):
        """[新增] 安全地在数据平面删除Schema。"""
        from app.db.tenant_db_session import tenant_data_engine
        async with tenant_data_engine.begin() as conn:
            await conn.execute(DropSchema(quoted_name(schema_name, quote=True), if_exists=True, cascade=True))
