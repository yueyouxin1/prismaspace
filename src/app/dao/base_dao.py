from typing import Type, TypeVar, Generic, Any, Optional, List
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import inspect, or_, and_, func, select, insert, update, delete
from sqlalchemy.orm import (
    subqueryload, joinedload, aliased, contains_eager, 
    with_loader_criteria, selectinload, load_only
)
from sqlalchemy.orm.strategy_options import Load
from sqlalchemy.sql.selectable import Select
from sqlalchemy.exc import IntegrityError, SQLAlchemyError
from sqlalchemy import asc, desc
from datetime import datetime
from app.db.base import Base # 确保从你的项目中正确导入 Base

# --- [最佳实践] 使用 TypeVar 和 Generic 实现类型安全的 DAO ---
SelectableType = TypeVar("SelectableType", bound=Any) 
ModelType = TypeVar("ModelType", bound=Base)

class BaseDao(Generic[ModelType]):
    def __init__(self, model_class: Type[ModelType], db_session: AsyncSession, selectable: Optional[SelectableType] = None):
        self.model: Type[ModelType] = model_class
        self.db_session: AsyncSession = db_session
        self.selectable: SelectableType = selectable if selectable is not None else model_class
        primary_keys = inspect(model_class).primary_key
        if not primary_keys:
            raise ValueError(f"Model {model_class.__name__} does not have a primary key.")
        self.pk: str = primary_keys[0].name
    
    # ==============================================================================
    # 1. 实体/对象方法 (Object Methods)
    #    - 输入和输出都应该是 ORM 对象实例
    # ==============================================================================
            
    async def get_list(
        self, 
        where: Optional[dict | list] = None, 
        where_or: Optional[list] = None, 
        joins: Optional[list] = None, 
        withs: Optional[list] = None, 
        fields: Optional[list[str]] = None, 
        options: Optional[List[Any]] = None,
        order: Optional[list] = None, 
        page: int = 0, 
        limit: int = 0, 
        start_time: Optional[datetime | int] = None, 
        end_time: Optional[datetime | int] = None, 
        time_key: str = "created_at", # [建议] 统一使用 created_at
        unique: bool = False
    ) -> list[ModelType]:
        stmt = self._quick_query(
            where=where, where_or=where_or, joins=joins, withs=withs, fields=fields, 
            options=options, order=order, page=page, limit=limit, start_time=start_time, 
            end_time=end_time, time_key=time_key
        )
        executed = await self.db_session.execute(stmt)
        return list(executed.scalars().unique().all() if unique else executed.scalars().all()) # [优化] 明确返回 list
        
    async def get_one(
        self,
        where: Optional[dict | list] = None, 
        where_or: Optional[list] = None, 
        joins: Optional[list] = None, 
        withs: Optional[list] = None, 
        fields: Optional[list[str]] = None,
        options: Optional[List[Any]] = None,
        order: Optional[list] = None
    ) -> Optional[ModelType]:
        stmt = self._quick_query(
            where=where, where_or=where_or, joins=joins, 
            withs=withs, fields=fields, options=options, order=order
        )
        executed = await self.db_session.execute(stmt)
        return executed.scalars().first()

    async def get_by_pk(
        self,
        pk_value: Any,
        joins: Optional[list] = None, 
        withs: Optional[list] = None, 
        fields: Optional[list[str]] = None,
        options: Optional[List[Any]] = None
    ) -> Optional[ModelType]:
        stmt = self._quick_query(
            where={self.pk: pk_value}, joins=joins, 
            withs=withs, fields=fields, options=options
        )
        executed = await self.db_session.execute(stmt)
        return executed.scalars().first()

    async def count(
        self, 
        where: Optional[dict | list] = None, 
        where_or: Optional[list] = None, 
        joins: Optional[list] = None, 
        start_time: Optional[datetime | int] = None, 
        end_time: Optional[datetime | int] = None, 
        time_key: str = "created_at"
    ) -> int:
        subquery_stmt = self._quick_query(
            where=where, 
            where_or=where_or, 
            joins=joins, 
            start_time=start_time, 
            end_time=end_time, 
            time_key=time_key
        ).subquery()

        count_stmt = select(func.count()).select_from(subquery_stmt)
        
        executed = await self.db_session.execute(count_stmt)
        return executed.scalar() or 0

    async def add(self, instance: ModelType, auto_flush: bool = True) -> ModelType:
        try:
            self.db_session.add(instance)
            if auto_flush:
                await self.db_session.flush()
                await self.db_session.refresh(instance)
            return instance
        except Exception:
            raise

    async def add_all(self, instances: list[ModelType], auto_flush: bool = True) -> list[ModelType]:
        try:
            if not instances:
                return []
                
            self.db_session.add_all(instances)
            
            if auto_flush:
                await self.db_session.flush()
                
                # 对列表进行 refresh 比较棘手，重新查询是很好的策略
                ids = [getattr(inst, self.pk) for inst in instances]
                if not ids:
                    return []
                
                stmt = select(self.selectable).where(getattr(self.model, self.pk).in_(ids))
                result = await self.db_session.execute(stmt)
                return list(result.scalars().all())
            
            # 如果不自动 flush，我们无法知道ID，所以返回原始列表
            # 它们的状态是 "pending"
            return instances

        except Exception:
            raise

    # ==============================================================================
    # 2. 数据/批量方法 (Data/Bulk Methods)
    #    - 用于高性能的、非对象驱动的操作
    # ==============================================================================

    async def update_where(self, where: dict | list, values: dict) -> int:
        if not where or not values:
            return 0
        try:
            conditions = self._where_format(where)
            stmt = update(self.model).where(*conditions).values(values)
            executed = await self.db_session.execute(stmt)
            return executed.rowcount
        except Exception:
            raise

    async def update_all(self, items: list[dict]) -> int:
        if not items:
            return 0
        try:
            # [解释] items 的每个 dict 必须包含主键
            executed = await self.db_session.execute(update(self.model), items)
            return executed.rowcount
        except Exception:
            raise

    async def delete_where(self, where: dict | list) -> int:
        if not where:
            return 0
        try:
            conditions = self._where_format(where)
            stmt = delete(self.model).where(*conditions)
            executed = await self.db_session.execute(stmt)
            return executed.rowcount
        except Exception:
            raise
            
    # ==============================================================================
    # 3. 聚合/数据查询方法 (Aggregation/Projection Methods)
    # ==============================================================================

    async def pluck(self, column_name: str, where: Optional[dict | list] = None, order: Optional[list] = None) -> list[Any]:
        stmt = select(getattr(self.model, column_name))
        stmt = self._quick_query(stmt=stmt, where=where, order=order)
        executed = await self.db_session.execute(stmt)
        return executed.scalars().all()
                    
    async def time_list_count(
        self, 
        start_time: datetime | int, 
        end_time: datetime | int, 
        where: Optional[dict] = None, 
        time_key: str = "created_at"
    ) -> list[dict[str, Any]]:
        stmt = select(
            func.date(getattr(self.model, time_key)).label('day'),
            func.count(getattr(self.model, self.pk)).label('count')
        )

        if where:
            stmt = self.where(stmt, where)

        stmt = stmt.filter(
            getattr(self.model, time_key).between(
                self._convert_to_datetime(start_time), 
                self._convert_to_datetime(end_time)
            )
        ).group_by(
            func.date(getattr(self.model, time_key))
        ).order_by(
            asc('day')
        )
        
        executed = await self.db_session.execute(stmt)
        return [dict(row) for row in executed.mappings()]
        
    # ==============================================================================
    # 4. 查询构建辅助方法 (Query Building Helpers)
    #    - [建议] 将这些方法设为 "protected" (以 _ 开头)
    # ==============================================================================
    
    def _to_class(self, relationship_property: Any) -> Type[Base]:
        return relationship_property.property.mapper.class_
    
    def _quick_query(
        self, 
        stmt: Optional[Select] = None,
        where: Optional[dict | list] = None, 
        where_or: Optional[list] = None, 
        joins: Optional[list] = None, 
        withs: Optional[list] = None, 
        fields: Optional[list[str]] = None, 
        options: Optional[List[Any]] = None,
        order: Optional[list] = None, 
        page: int = 0, 
        limit: int = 0, 
        start_time: Optional[datetime | int] = None, 
        end_time: Optional[datetime | int] = None, 
        time_key: str = "created_at"
    ) -> Select:
        """
        一个线性的、清晰的查询构建方法。
        """
        if stmt is None:
            stmt = select(self.selectable)
            
        if where is not None:
            stmt = self._where(stmt, where)
        
        if where_or is not None:
            stmt = self._where_or(stmt=stmt, where=where_or)

        if start_time and end_time:
            stmt = stmt.filter(
                getattr(self.model, time_key).between(
                    self._convert_to_datetime(start_time), 
                    self._convert_to_datetime(end_time)
                )
            )
            
        if joins is not None:
            stmt = self._joins(stmt, joins)
            
        if withs is not None:
            stmt = self._withs(stmt, withs)
            
        if fields is not None:
            stmt = self._fields(stmt, fields)

        if options is not None:
            stmt = stmt.options(*options)

        if order is not None:
            stmt = stmt.order_by(*order)   
                
        if page > 0 and limit > 0:
            stmt = self._paginate(stmt=stmt, page=page, limit=limit)
            
        return stmt
    
    def _where(
        self,         
        stmt: Optional[Select] = None,
        where: Optional[dict | list] = None
    ) -> Select:
        if stmt is None:
            stmt = select(self.selectable)
        if isinstance(where, dict):
            stmt = stmt.filter_by(**where)
        elif isinstance(where, (list, tuple)):
            stmt = stmt.filter(*where)
        return stmt

    def _where_or(
        self,         
        stmt: Optional[Select] = None,
        where: Optional[dict | list] = None
    ) -> Select:
        if stmt is None:
            stmt = select(self.selectable)
        stmt = stmt.filter(or_(*where))
        return stmt

    def _withs(self, stmt: Select, withs: list) -> Select:
        if not withs:
            return stmt
        
        all_loader_options = []
        for config in withs:
            # Build each top-level loader option recursively
            loader_option = self._build_loader_option(config, self.selectable)
            all_loader_options.append(loader_option)
        
        # Apply all collected options in a single call
        return stmt.options(*all_loader_options)

    def _build_loader_option(self, config: str | dict | Load, current_entity: Any) -> Any:
        """
        Recursively builds a single, complete loader option object (e.g., subqueryload).
        """
        if isinstance(config, Load):
            return config

        if isinstance(config, str):
            return selectinload(getattr(current_entity, config))

        if isinstance(config, dict):
            name = config.get("name")
            if not name:
                raise ValueError("Relation 'name' is required in withs configuration.")

            loader_str = config.get("loader", "selectinload") # Allow customizing loader
            loader_func = {"subqueryload": subqueryload, "selectinload": selectinload, "joinedload": joinedload}.get(loader_str)
            if not loader_func:
                raise ValueError(f"Invalid loader specified: {loader_str}")

            relationship_attr = getattr(current_entity, name)
            target_model_class = self._to_class(relationship_attr)

            # Start building the option for the current level
            loader_option = loader_func(relationship_attr)

            # 1. Apply field restrictions (load_only)
            if "fields" in config:
                fields = self._to_field(model=target_model_class, fields=config["fields"])
                loader_option = loader_option.load_only(*fields)

            # 2. Apply filtering (with_loader_criteria)
            nested_options = []
            if "where" in config:
                where_clauses = self._where_format(config["where"], model=target_model_class)
                # Correctly attach with_loader_criteria to the loader option
                nested_options.append(with_loader_criteria(target_model_class, and_(*where_clauses)))

            # 3. Recurse for nested relationships
            if "withs" in config:
                for nested_config in config["withs"]:
                    nested_option = self._build_loader_option(nested_config, target_model_class)
                    nested_options.append(nested_option)
            
            # Chain all nested options (filters, further loads) to the current loader
            if nested_options:
                loader_option = loader_option.options(*nested_options)

            return loader_option

        raise TypeError("Unsupported 'withs' configuration type. Must be str or dict.")
    
    def _where_format(self, conditions: list | dict, model: Optional[Type[Base]] = None) -> list:
        if model is None:
            model = self.model
            
        if not conditions:
            return []

        processed_conditions = []
        if isinstance(conditions, list):
            for condition in conditions:
                if isinstance(condition, (str, list, tuple)):
                    field, operator, value = condition if isinstance(condition, (list, tuple)) else condition.split()
                    field = getattr(model, field)
                    # 特殊处理 'in' 操作符
                    if operator == 'in':
                        expr = field.in_(value)
                    else:
                        expr = eval(f'field {operator} value')  # 其他操作符正常处理
                    processed_conditions.append(expr)
                else:
                    processed_conditions.append(condition)
        elif isinstance(conditions, dict):
            processed_conditions = [getattr(model, field) == value for field, value in conditions.items()]
        if len(processed_conditions) > 1:
            processed_conditions = [and_(*processed_conditions)]
        return processed_conditions
    
    def _to_field(self, model: Optional[Type[Base]] = None, fields: Optional[list] = None) -> list:
        if model is None:
            model = self.model
            
        extended_fields = []
        if fields is None:
            return extended_fields
        for field in fields:
            if field == "*":
                for col in model.__table__.columns:
                    extended_fields.append(getattr(model, col.name))
            else:  # Handle simple fields
                if isinstance(field, str):
                    extended_fields.append(getattr(model, field))
                else:
                    extended_fields.append(field)
        return extended_fields
    
    def _fields(self, stmt: Optional[Select] = None, fields: Optional[list] = None) -> Select:
        if stmt is None:
            stmt = select(self.selectable)
        extended_fields = self._to_field(fields=fields)
        stmt = stmt.options(load_only(*extended_fields))
        return stmt
    
    def _joins(self, stmt: Optional[Select] = None, joins: Optional[list] = None) -> Select:
        if stmt is None:
            stmt = select(self.selectable)

        if joins is not None:
            for join in joins:
                if not isinstance(join, dict):
                    raise ValueError("Join configuration must be a dictionary.")

                join_name = join.get("name")
                if join_name is None:
                    raise ValueError("Join 'name' is required.")

                join_model = join.get('model', getattr(self.model, join_name))
                join_where = join.get("where")

                # 添加 JOIN
                stmt = stmt.join(join_model)

                # 添加过滤条件
                if join_where:
                    where_clauses = self._where_format(join_where, self._to_class(join_model))
                    stmt = stmt.filter(*where_clauses)

        return stmt
    
    def _paginate(self, stmt: Optional[Select] = None, page: int = 0, limit: int = 0) -> Select:
        if stmt is None:
            stmt = select(self.selectable)
        page = int(page)
        limit = int(limit)
        if page > 0 and limit > 0:
            stmt = stmt.limit(limit).offset((page - 1) * limit)  
        return stmt
        
    # 转换结果

    def _convert_to_datetime(self, date_str_or_timestamp: str | int | datetime) -> datetime:
        if isinstance(date_str_or_timestamp, datetime):
            return date_str_or_timestamp
        try:
            return datetime.fromtimestamp(int(date_str_or_timestamp))
        except (ValueError, TypeError):
            return datetime.strptime(str(date_str_or_timestamp), '%Y-%m-%d')