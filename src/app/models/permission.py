import enum
from sqlalchemy import (
    Column, Integer, String, Text, JSON, Boolean, Enum, ForeignKey,
    DateTime, func, Index, DECIMAL, UniqueConstraint, CheckConstraint
)
from sqlalchemy.orm import relationship
from app.db.base import Base
from app.utils.id_generator import generate_uuid

class RoleType(enum.Enum):
    SYSTEM_PLAN = "system_plan"         # For plan:free, plan:pro etc.
    SYSTEM_TEAM_TEMPLATE = "system_team_template" # For team:owner, team:admin etc.
    CUSTOM_TEAM = "custom_team"         # For team-specific custom roles

class ActionPermissionType(enum.Enum):
    ABILITY = "ability" # 代表一个抽象的业务能力，如“写项目”
    API = "api" # 一个后端API操作
    PAGE = "page" # 一个前端页面/路由
    COMPONENT = "component" # 一个前端组件
    ACTION = "action" # 一个前端UI上的具体交互动作

class ActionPermission(Base):
    """权限原子定义表 - 系统所有权限的权威字典"""
    __tablename__ = 'action_permissions'
    # 必要
    id = Column(Integer, primary_key=True, comment="权限唯一主键ID")
    # [可选] 父权限ID，用于在数据库层面显式地建立树状关系
    # 虽然层级关系已隐含在name中，但这个外键能极大优化查询
    parent_id = Column(Integer, ForeignKey('action_permissions.id'), nullable=True, index=True)
    # 必要
    name = Column(String(100), unique=True, nullable=False, comment="权限的唯一标识符 (e.g., 'page.dashboard.view', 'project.create', 'member.invite')")
    # 增强/QoL
    description = Column(String(255), comment="权限的详细描述")
    # [增强/QoL] 用于UI展示和分类
    label = Column(String(255), nullable=False, comment="对人类友好的名称 (e.g., '更新项目名称')")
    # [关键新增] 权限的类型/作用域
    type = Column(Enum(ActionPermissionType), nullable=False, index=True, comment="权限的作用域 (前端UI, 后端API)")
    # --- 用于“路由”和“UI”的元数据 ---
    # [新增]
    route_path = Column(String(255), nullable=True, comment="[前端页面] 对应的路由路径, e.g., '/dashboard'")
    # [新增]
    icon = Column(String(100), nullable=True, comment="[前端] 用于菜单或按钮的图标")
    # [增强/未来]
    context_schema = Column(JSON, nullable=True, comment="关联的context字段的JSON Schema约束")
    is_assignable = Column(Boolean, nullable=False, default=True, comment="此权限是否对团队管理员可见并可分配给自定义角色。False表示为系统内部权限。")
    parent = relationship("ActionPermission", remote_side=[id], back_populates="children")
    children = relationship("ActionPermission", back_populates="parent", cascade="all, delete-orphan")
    roles = relationship("Role", secondary="role_permissions", back_populates="permissions")

class Role(Base):
    """角色定义表 - 权限的集合"""
    __tablename__ = 'roles'
    # 必要
    id = Column(Integer, primary_key=True, comment="角色唯一主键ID")
    uuid = Column(String(36), default=generate_uuid, unique=True, index=True, nullable=False)
    parent_id = Column(Integer, ForeignKey('roles.id'), nullable=True, index=True)
    # 必要
    name = Column(String(100), nullable=False, comment="角色名称 (e.g., Owner, Admin, Editor, Viewer)")
    # 增强/QoL
    label = Column(String(255), nullable=False, comment="角色别名")
    # 增强/QoL
    description = Column(String(255), comment="角色的详细描述")
    role_type = Column(Enum(RoleType), nullable=False, default=RoleType.CUSTOM_TEAM)
    
    # [必要] 角色的作用域
    team_id = Column(Integer, ForeignKey('teams.id', ondelete='CASCADE'), nullable=True, comment="所属团队ID, 为NULL表示是系统级预设角色")
    workspace_id = Column(Integer, ForeignKey('ai_workspaces.id', ondelete='CASCADE'), nullable=True, comment="所属工作空间ID，用于更细粒度的权限")

    is_active = Column(Boolean, nullable=False, default=True)
    
    team = relationship("Team")
    team_members = relationship("TeamMember", back_populates="role")
    workspace = relationship("Workspace", back_populates="roles")
    parent = relationship("Role", remote_side=[id], back_populates="children")
    children = relationship("Role", back_populates="parent", cascade="all, delete-orphan")
    permissions = relationship("ActionPermission", secondary="role_permissions", back_populates="roles")

class RolePermission(Base):
    """角色权限关联表 - 多对多关系"""
    __tablename__ = 'role_permissions'
    # 必要
    role_id = Column(Integer, ForeignKey('roles.id', ondelete='CASCADE'), primary_key=True, comment="角色ID")
    # 必要
    permission_id = Column(Integer, ForeignKey('action_permissions.id', ondelete='CASCADE'), primary_key=True, comment="权限ID")