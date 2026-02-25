# src/app/models/resource/uiapp.py

from sqlalchemy import Column, Integer, String, JSON, ForeignKey, Boolean, UniqueConstraint
from sqlalchemy.orm import relationship
from app.db.base import Base
from app.models.resource.base import ResourceInstance

class UiApp(ResourceInstance):
    """
    [App Skeleton] UiApp 资源实例（应用骨架）。
    只存储全局配置、导航结构和页面元数据列表，不存储具体的 Page DSL。
    """
    __tablename__ = 'ai_uiapps'

    version_id = Column(Integer, ForeignKey('ai_resource_instances.id', ondelete='CASCADE'), primary_key=True)

    # 1. 全局配置 (Theme, GlobalState, Scripts)
    global_config = Column(JSON, nullable=False, default={}, comment="全局配置")

    # 2. 导航结构 (Sidebar, Navbar)
    navigation = Column(JSON, nullable=True, comment="导航菜单定义")
    
    # 3. 入口页面指针 (Page UUID)
    home_page_uuid = Column(String(64), nullable=True, comment="默认首页的Page UUID")

    # 4. 关系: 一个 App 版本包含多个页面
    # cascade="all, delete-orphan" 确保删除 App 版本时，关联的页面也被物理删除
    pages = relationship("UiPage", back_populates="app_version", cascade="all, delete-orphan", order_by="UiPage.display_order")

    __mapper_args__ = { 'polymorphic_identity': 'uiapp' }

class UiPage(Base):
    __tablename__ = 'ai_uiapp_pages'

    id = Column(Integer, primary_key=True)
    
    # 归属关系: 指向特定的 UiApp 版本 (Workpace版 或 Published版)
    app_version_id = Column(Integer, ForeignKey('ai_uiapps.version_id', ondelete='CASCADE'), nullable=False, index=True)
    
    # 页面标识 (前端生成的 UUID, e.g. "page_login_v1")
    page_uuid = Column(String(64), nullable=False)
    
    # 页面基础元数据 (用于 App 骨架加载)
    path = Column(String(255), nullable=False, comment="路由路径, e.g. /dashboard")
    label = Column(String(255), nullable=False, comment="页面标题")
    icon = Column(String(100), nullable=True)
    display_order = Column(Integer, default=0)
    
    # [核心] 页面 DSL (Heavy Payload)
    # 对应 contracts/Page.d.ts 中的 data 字段 (节点树)
    data = Column(JSON, nullable=False, default=[], comment="组件树 DSL")
    
    # 配置 (页面级 State/Style)
    config = Column(JSON, nullable=True, default={})

    app_version = relationship("UiApp", back_populates="pages")

    __table_args__ = (
        # 同一个 App 版本下，页面 UUID 必须唯一
        UniqueConstraint('app_version_id', 'page_uuid', name='uq_uiapp_version_page'),
        # 同一个 App 版本下，路由 Path 必须唯一
        UniqueConstraint('app_version_id', 'path', name='uq_uiapp_version_path'),
    )