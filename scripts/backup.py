# scripts/seed_initial_data.py
import asyncio
from typing import Dict, List, Any
from sqlalchemy import select, func
from sqlalchemy.orm import joinedload
from sqlalchemy.ext.asyncio import create_async_engine, AsyncSession
from sqlalchemy.orm import sessionmaker

# Adjust these imports to match your project structure
from app.core.config import settings
from app.models import (
    Product, ProductType, ActionPermission, ActionPermissionType, Role, RolePermission,
    ServiceModuleType, ServiceModule, ServiceModuleVersion, ServiceModuleStatus
)
from app.models.resource import ResourceType
from app.db.base import Base # To create tables if they don't exist

# Data definitions are separated for clarity
# ==============================================================================
# 1. Action Permissions Data
# ==============================================================================
PERMISSIONS_DATA = [
    # 1. User & Authentication
    {'name': 'user:profile:read', 'label': '查看个人资料', 'description': '读取用户自己的昵称、头像等信息。', 'category': 'user', 'type': ActionPermissionType.API, 'is_assignable': True},
    {'name': 'user:profile:write', 'label': '修改个人资料', 'description': '更新用户自己的昵称、头像等信息。', 'category': 'user', 'type': ActionPermissionType.API, 'is_assignable': True},
    {'name': 'user:security:write', 'label': '修改安全设置', 'description': '更改密码、设置/解除双因素认证。', 'category': 'user', 'type': ActionPermissionType.API, 'is_assignable': True},
    {'name': 'user:account:delete', 'label': '删除个人账户', 'description': '永久删除用户自己的账户及相关数据。', 'category': 'user', 'type': ActionPermissionType.API, 'is_assignable': True},
    {'name': 'user:api_key:read', 'label': '查看个人API密钥', 'description': '列出和查看用户个人账户下的API密钥。', 'category': 'user', 'type': ActionPermissionType.API, 'is_assignable': True},
    {'name': 'user:api_key:create', 'label': '创建个人API密钥', 'description': '为用户个人账户生成新的API密钥。', 'category': 'user', 'type': ActionPermissionType.API, 'is_assignable': True},
    {'name': 'user:api_key:delete', 'label': '吊销个人API密钥', 'description': '删除用户个人账户下的API密钥。', 'category': 'user', 'type': ActionPermissionType.API, 'is_assignable': True},
    {'name': 'page:settings:profile', 'label': '访问个人资料页', 'description': '控制对前端“/settings/profile”页面的访问。', 'category': 'user', 'type': ActionPermissionType.PAGE, 'is_assignable': True},
    {'name': 'page:settings:security', 'label': '访问安全设置页', 'description': '控制对前端“/settings/security”页面的访问。', 'category': 'user', 'type': ActionPermissionType.PAGE, 'is_assignable': True},
    {'name': 'page:settings:api_keys', 'label': '访问个人API密钥页', 'description': '控制对前端“/settings/api-keys”页面的访问。', 'category': 'user', 'type': ActionPermissionType.PAGE, 'is_assignable': True},
    
    # 2. Team & Collaboration
    {'name': 'team:create', 'label': '创建团队', 'description': '允许用户创建一个新的团队。', 'category': 'team', 'type': ActionPermissionType.API, 'is_assignable': True},
    {'name': 'team:read', 'label': '查看团队信息', 'description': '读取团队的基本信息（名称、头像）。', 'category': 'team', 'type': ActionPermissionType.API, 'is_assignable': True},
    {'name': 'team:update', 'label': '更新团队信息', 'description': '修改团队的名称、头像等设置。', 'category': 'team', 'type': ActionPermissionType.API, 'is_assignable': True},
    {'name': 'team:delete', 'label': '删除团队', 'description': '[高危] 永久删除整个团队及其所有资产。', 'category': 'team', 'type': ActionPermissionType.API, 'is_assignable': False},
    {'name': 'team:member:read', 'label': '查看团队成员', 'description': '列出团队中的所有成员及其角色。', 'category': 'team', 'type': ActionPermissionType.API, 'is_assignable': True},
    {'name': 'team:member:invite', 'label': '邀请新成员', 'description': '向团队发送邀请。', 'category': 'team', 'type': ActionPermissionType.API, 'is_assignable': True},
    {'name': 'team:member:remove', 'label': '移除团队成员', 'description': '将成员从团队中移除。', 'category': 'team', 'type': ActionPermissionType.API, 'is_assignable': True},
    {'name': 'team:member:role:update', 'label': '修改成员角色', 'description': '更改团队成员的角色。', 'category': 'team', 'type': ActionPermissionType.API, 'is_assignable': True},
    {'name': 'team:invitation:manage', 'label': '管理邀请', 'description': '重新发送或撤销待处理的团队邀请。', 'category': 'team', 'type': ActionPermissionType.API, 'is_assignable': True},
    {'name': 'team:role:read', 'label': '查看自定义角色', 'description': '查看团队内创建的自定义角色。', 'category': 'team', 'type': ActionPermissionType.API, 'is_assignable': True},
    {'name': 'team:role:write', 'label': '管理自定义角色', 'description': '创建、更新、删除团队的自定义角色。', 'category': 'team', 'type': ActionPermissionType.API, 'is_assignable': False},
    {'name': 'team:api_key:read', 'label': '查看团队API密钥', 'description': '查看团队级别的API密钥。', 'category': 'team', 'type': ActionPermissionType.API, 'is_assignable': True},
    {'name': 'team:api_key:create', 'label': '创建团队API密钥', 'description': '为团队生成新的API密钥。', 'category': 'team', 'type': ActionPermissionType.API, 'is_assignable': True},
    {'name': 'team:api_key:delete', 'label': '吊销团队API密钥', 'description': '删除团队下的API密钥。', 'category': 'team', 'type': ActionPermissionType.API, 'is_assignable': True},
    {'name': 'page:team:settings', 'label': '访问团队设置页', 'description': '控制对前端“/team/settings”页面的访问。', 'category': 'team', 'type': ActionPermissionType.PAGE, 'is_assignable': True},
    {'name': 'page:team:members', 'label': '访问团队成员页', 'description': '控制对前端“/team/members”页面的访问。', 'category': 'team', 'type': ActionPermissionType.PAGE, 'is_assignable': True},

    # 3. Billing & Subscription
    {'name': 'billing:read', 'label': '查看计费信息', 'description': '查看当前订阅计划、用量、余额和历史账单。', 'category': 'billing', 'type': ActionPermissionType.API, 'is_assignable': True},
    {'name': 'billing:manage', 'label': '管理订阅与支付', 'description': '升级/降级订阅、添加/删除支付方式、充值。', 'category': 'billing', 'type': ActionPermissionType.API, 'is_assignable': False},
    {'name': 'page:settings:billing', 'label': '访问计费与订阅页', 'description': '控制对前端“/settings/billing”或“/team/billing”页面的访问。', 'category': 'billing', 'type': ActionPermissionType.PAGE, 'is_assignable': True},

    # 4. Workspace & Project
    {'name': 'workspace:create', 'label': '创建工作空间', 'description': '在个人或团队下创建新的工作空间。', 'category': 'workspace', 'type': ActionPermissionType.API, 'is_assignable': True},
    {'name': 'workspace:read', 'label': '查看工作空间', 'description': '能够看到并进入一个工作空间。', 'category': 'workspace', 'type': ActionPermissionType.ABILITY, 'is_assignable': True},
    {'name': 'workspace:update', 'label': '更新工作空间设置', 'description': '修改工作空间的名称、头像等。', 'category': 'workspace', 'type': ActionPermissionType.API, 'is_assignable': True},
    {'name': 'workspace:delete', 'label': '删除工作空间', 'description': '[高危] 删除工作空间及其所有项目。', 'category': 'workspace', 'type': ActionPermissionType.API, 'is_assignable': True},
    {'name': 'project:create', 'label': '创建项目', 'description': '在工作空间内创建新项目。', 'category': 'project', 'type': ActionPermissionType.API, 'is_assignable': True},
    {'name': 'project:read', 'label': '查看项目', 'description': '能够看到并打开一个项目。', 'category': 'project', 'type': ActionPermissionType.ABILITY, 'is_assignable': True},
    {'name': 'project:delete', 'label': '删除项目', 'description': '[高危] 删除项目及其所有资源。', 'category': 'project', 'type': ActionPermissionType.API, 'is_assignable': True},
    # Parent permission with children
    {
        'name': 'project:update', 'label': '更新项目', 'description': '[宏观] 拥有修改项目所有设置的总体能力。', 'category': 'project', 'type': ActionPermissionType.ABILITY, 'is_assignable': True,
        'children': [
            {'name': 'project:update:metadata', 'label': '更新项目元数据', 'description': '修改项目的名称、描述、图标。', 'category': 'project', 'type': ActionPermissionType.API, 'is_assignable': True},
            {'name': 'project:update:visibility', 'label': '更新项目可见性', 'description': '更改项目的可见范围（私有、工作空间、公开）。', 'category': 'project', 'type': ActionPermissionType.API, 'is_assignable': True},
        ]
    },
    {'name': 'page:dashboard', 'label': '访问仪表盘/主页', 'description': '查看所有可见的工作空间和项目列表。', 'category': 'project', 'type': ActionPermissionType.PAGE, 'is_assignable': True},
    {'name': 'page:project:detail', 'label': '访问项目详情页', 'description': '进入特定项目的内部视图。', 'category': 'project', 'type': ActionPermissionType.PAGE, 'is_assignable': True},
    
    # 5. Resource Creation & Lifecycle
    {'name': 'resource:create', 'label': '创建资源', 'description': '在项目中创建新的资源（如UI App, Tool）。', 'category': 'resource', 'type': ActionPermissionType.API, 'is_assignable': True},
    {'name': 'resource:read', 'label': '查看资源', 'description': '读取资源的定义和内容。', 'category': 'resource', 'type': ActionPermissionType.API, 'is_assignable': True},
    {'name': 'resource:update', 'label': '编辑资源', 'description': '修改资源的工作区版本（即草稿）。', 'category': 'resource', 'type': ActionPermissionType.API, 'is_assignable': True},
    {'name': 'resource:delete', 'label': '删除资源', 'description': '[高危] 从项目中永久删除一个资源及其所有版本。', 'category': 'resource', 'type': ActionPermissionType.API, 'is_assignable': True},
    {'name': 'resource:version:read', 'label': '查看版本历史', 'description': '读取一个资源的所有历史版本。', 'category': 'resource', 'type': ActionPermissionType.API, 'is_assignable': True},
    {'name': 'resource:version:publish', 'label': '发布版本', 'description': '将一个资源版本发布到线上。', 'category': 'resource', 'type': ActionPermissionType.API, 'is_assignable': True},
    {'name': 'resource:version:manage', 'label': '管理已发布版本', 'description': '回滚、归档或下线一个已发布的版本。', 'category': 'resource', 'type': ActionPermissionType.API, 'is_assignable': True},
    {'name': 'page:editor', 'label': '访问编辑器', 'description': '打开用于编辑资源的可视化创作空间。', 'category': 'resource', 'type': ActionPermissionType.PAGE, 'is_assignable': True},
    {'name': 'action:editor:save', 'label': '在编辑器中保存', 'description': '在编辑器UI上点击“保存”按钮的权限。', 'category': 'resource', 'type': 'ACTION', 'is_assignable': True},
    {'name': 'action:editor:publish', 'label': '在编辑器中发布', 'description': '在编辑器UI上点击“发布”按钮的权限。', 'category': 'resource', 'type': 'ACTION', 'is_assignable': True},

    # 6. Collaboration, Sharing & Execution
    {'name': 'project:share', 'label': '分享项目', 'description': '允许用户与特定用户或通过链接分享项目。', 'category': 'collaboration', 'type': ActionPermissionType.API, 'is_assignable': True},
    {'name': 'resource:share', 'label': '分享资源', 'description': '允许用户分享项目中的单个资源。', 'category': 'collaboration', 'type': ActionPermissionType.API, 'is_assignable': True},
    {'name': 'project:comment:read', 'label': '查看项目评论', 'description': '读取项目中的评论。', 'category': 'collaboration', 'type': ActionPermissionType.API, 'is_assignable': True},
    {'name': 'project:comment:write', 'label': '发表项目评论', 'description': '在项目中添加、编辑或删除自己的评论。', 'category': 'collaboration', 'type': ActionPermissionType.API, 'is_assignable': True},
    {'name': 'project:execute', 'label': '运行项目主应用', 'description': '允许用户运行一个项目的主应用。', 'category': 'execution', 'type': ActionPermissionType.API, 'is_assignable': True},
    {'name': 'resource:execute', 'label': '执行/调用资源', 'description': '[关键] 允许用户调用已发布的资源（如与Agent对话）。', 'category': 'execution', 'type': ActionPermissionType.API, 'is_assignable': True},

    # 7. Templates & Marketplace
    {'name': 'project:template:create', 'label': '创建项目模板', 'description': '将一个现有项目保存为模板。', 'category': 'template', 'type': ActionPermissionType.API, 'is_assignable': True},
    {'name': 'project:template:use', 'label': '使用项目模板', 'description': '从一个模板创建一个新的项目。', 'category': 'template', 'type': ActionPermissionType.API, 'is_assignable': True},
    {'name': 'platform:marketplace:submit', 'label': '提交到市场', 'description': '提交一个资源或模板到官方市场以供审核。', 'category': 'template', 'type': ActionPermissionType.API, 'is_assignable': True},

    # 8. Auditing & Analytics
    {'name': 'audit:activity_log:read', 'label': '查看团队行为日志', 'description': '查看团队成员的操作日志。', 'category': 'audit', 'type': ActionPermissionType.API, 'is_assignable': True},
    {'name': 'audit:trace:read', 'label': '查看应用调用日志', 'description': '查看和调试已发布应用的API调用Trace日志。', 'category': 'audit', 'type': ActionPermissionType.API, 'is_assignable': True},
    
    # 9. Platform Super Admin
    {'name': 'platform:user:impersonate', 'label': '[Admin] 模拟用户登录', 'description': '[高危] 以特定用户身份登录，用于调试和客户支持。', 'category': 'platform_admin', 'type': ActionPermissionType.API, 'is_assignable': False},
    {'name': 'platform:billing:manage_all', 'label': '[Admin] 管理所有计费', 'description': '查看和修改任何用户或团队的计费信息。', 'category': 'platform_admin', 'type': ActionPermissionType.API, 'is_assignable': False},
    {'name': 'platform:resource:manage_all', 'label': '[Admin] 管理所有资源', 'description': '查看、暂停或删除平台上的任何资源（用于内容审核）。', 'category': 'platform_admin', 'type': ActionPermissionType.API, 'is_assignable': False},
    {'name': 'platform:servicemodule:manage', 'label': '[Admin] 管理服务模块', 'description': '添加、更新或下线平台提供的AI能力。', 'category': 'platform_admin', 'type': ActionPermissionType.API, 'is_assignable': False},
]

# ==============================================================================
# 2. System Roles Data
# ==============================================================================
ROLES_DATA = [
    # Plan-Based System Roles
    {'name': 'plan:free', 'label': 'Free Plan', 'description': 'Default permissions for users on the Free plan.'},
    {'name': 'plan:pro', 'label': 'Pro Plan', 'description': 'Permissions for users subscribed to the Pro plan.'},
    {'name': 'plan:team', 'label': 'Team Plan', 'description': 'Permissions for users subscribed to the Team plan.'},
    {'name': 'plan:enterprise', 'label': 'Enterprise Plan', 'description': 'Permissions for users on a custom Enterprise plan.'},
    
    # Team Role Templates
    {'name': 'team:owner', 'label': 'Team Owner', 'description': 'Has ultimate authority over the team, its assets, billing, and members. This role cannot be modified.'},
    {'name': 'team:admin', 'label': 'Team Admin', 'description': 'Can manage projects, resources, and team members, but cannot delete the team or manage billing.'},
    {'name': 'team:member', 'label': 'Team Member', 'description': 'Can create and edit projects and resources within the team, but cannot manage team settings or members.'},
    {'name': 'team:billing_manager', 'label': 'Billing Manager', 'description': 'Can view and manage the team\'s subscription and billing information, but cannot access projects or resources.'},
]

# ==============================================================================
# 3. Role-Permission Mappings Data
# ==============================================================================
# Define permissions for each role by name for easy mapping
PLAN_FREE_PERMS = [
    'user:profile:read', 'user:profile:write', 'user:security:write', 'user:account:delete',
    'user:api_key:read', 'user:api_key:create', 'user:api_key:delete',
    'page:settings:profile', 'page:settings:security', 'page:settings:api_keys',
    'workspace:create', 'workspace:read', 'workspace:update', 'workspace:delete',
    'project:create', 'project:read', 'project:update', 'project:delete',
    'page:dashboard', 'page:project:detail',
    'resource:create', 'resource:read', 'resource:update', 'resource:delete',
    'resource:version:read', 'resource:version:publish', 'resource:version:manage',
    'page:editor', 'action:editor:save', 'action:editor:publish',
    'project:execute', 'resource:execute',
    'billing:read',
    'page:settings:billing'
]

PLAN_PRO_PERMS = [
    'project:share', 'resource:share',
    'project:comment:read', 'project:comment:write',
    'project:template:create', 'project:template:use',
    'platform:marketplace:submit'
]

PLAN_TEAM_PERMS = ['team:create']

PLAN_ENTERPRISE_PERMS = [
    'audit:activity_log:read',
    'audit:trace:read'
]

# --- Team Permissions: 仍然使用拼接，因为没有自动化继承逻辑 ---

TEAM_MEMBER_PERMS = [
    'team:read', 'team:member:read',
    'workspace:create', 'workspace:read', 'workspace:update', 'workspace:delete',
    'project:create', 'project:read', 'project:update', 'project:delete',
    'resource:create', 'resource:read', 'resource:update', 'resource:delete',
    'resource:version:read', 'resource:version:publish', 'resource:version:manage',
    'project:share', 'resource:share',
    'project:comment:read', 'project:comment:write',
    'project:execute', 'resource:execute',
    'audit:trace:read',
    'page:dashboard', 'page:project:detail', 'page:editor'
]

TEAM_ADMIN_PERMS = TEAM_MEMBER_PERMS + [
    'team:update',
    'team:member:invite', 'team:member:remove', 'team:member:role:update', 'team:invitation:manage',
    'team:role:read', 'team:role:write',
    'team:api_key:read', 'team:api_key:create', 'team:api_key:delete',
    'audit:activity_log:read',
    'page:team:settings', 'page:team:members'
]

TEAM_OWNER_PERMS = TEAM_ADMIN_PERMS + [
    'team:delete',
    'billing:read', 'billing:manage',
    'page:settings:billing'
]

TEAM_BILLING_MANAGER_PERMS = [
    'team:read', 'team:member:read',
    'billing:read', 'billing:manage',
    'page:settings:billing'
]

# Master mapping dictionary
ROLE_PERMISSION_MAP = {
    'plan:free': PLAN_FREE_PERMS,
    'plan:pro': PLAN_PRO_PERMS,
    'plan:team': PLAN_TEAM_PERMS,
    'plan:enterprise': PLAN_ENTERPRISE_PERMS,
    'team:member': TEAM_MEMBER_PERMS,
    'team:admin': TEAM_ADMIN_PERMS,
    'team:owner': TEAM_OWNER_PERMS,
    'team:billing_manager': TEAM_BILLING_MANAGER_PERMS,
}

PLAN_HIERARCHY = ['plan:free', 'plan:pro', 'plan:team', 'plan:enterprise']

PRODUCTS_DATA = [
    {
        'name': 'plan_free', 'label': 'Free Plan', 'type': ProductType.SUBSCRIPTION,
        'description': 'The default plan for all new users.', 'is_active': True
    },
    {
        'name': 'plan_pro', 'label': 'Pro Plan', 'type': ProductType.SUBSCRIPTION,
        'description': 'For professional creators with advanced needs.', 'is_active': True
    },
]

RESOURCE_TYPES_DATA = [
    {'name': 'agent', 'label': '智能体', 'is_application': True},
    {'name': 'uiapp', 'label': 'UI应用', 'is_application': True},
    {'name': 'tool', 'label': '工具', 'is_application': False},
    {'name': 'tenantdb', 'label': '数据库', 'is_application': False},
    {'name': 'vectordb', 'label': '知识库', 'is_application': False}
]


SERVICE_MODULE_TYPES_DATA = [
    {'name': 'llm', 'label': '大型语言模型'},
    {'name': 'embedding', 'label': '文本嵌入模型'},
    {'name': 'tts', 'label': '语音合成'},
]

SERVICE_MODULES_DATA = [
    {
        'type_name': 'embedding', # 用于关联到上面的类型
        'name': 'text-embedding-v4',
        'label': 'text-embedding-v4',
        'provider': 'aliyun',
        'versions': [
            {
                'product_name': 'plan_free',
                'version_tag': '1.0.0',
                'status': ServiceModuleStatus.AVAILABLE,
                'attributes': {
                    'dimensions': 1536, 
                    'max_tokens': 8192,
                    'provider_model_name': 'text-embedding-v4' # [关键] 实际调用API时使用的模型名
                }
            }
        ]
    }
]

# --- 自动从 Service Modules 数据生成权限 ---
def generate_module_permissions(modules_data):
    permissions = []
    for module in modules_data:
        for version in module['versions']:
            # 使用我们约定的命名格式
            perm_name = f"servicemodule:use:{module['name']}"
            perm_label = f"使用 {module['label']}"
            permissions.append({
                'name': perm_name,
                'label': perm_label,
                'description': f"允许使用 {module['label']} 服务模块。",
                'category': 'service_module',
                'type': ActionPermissionType.ABILITY, # 这是一个抽象能力
                'is_assignable': True
            })
    # 去重
    unique_permissions = list({p['name']: p for p in permissions}.values())
    return unique_permissions

PERMISSIONS_DATA.extend(generate_module_permissions(SERVICE_MODULES_DATA))

async def _seed_products(db: AsyncSession) -> Dict[str, Product]:
    """播种基础产品 (如 Free Plan) 并返回一个按名称索引的字典。"""
    print("Seeding Products...")
    
    products_to_create = [Product(**data) for data in PRODUCTS_DATA]
    db.add_all(products_to_create)
    await db.flush()
    
    # 返回一个查找表，方便后续步骤使用
    return {p.name: p for p in products_to_create}

async def _seed_permissions(db: AsyncSession) -> Dict[str, ActionPermission]:
    """
    [V5.0 REFACTORED] Recursively seeds ActionPermissions from a tree structure,
    correctly setting up parent-child relationships.
    """
    print("Seeding Action Permissions from tree structure...")
    
    # Check if permissions already exist
    if await db.scalar(select(func.count(ActionPermission.id))) > 0:
        print("  - Permissions already exist. Loading them into map.")
        result = await db.execute(select(ActionPermission))
        return {p.name: p for p in result.scalars().all()}

    permissions_map: Dict[str, ActionPermission] = {}

    async def _recursive_seed(permissions_list: List[Dict], parent: Optional[ActionPermission] = None):
        """A helper function to traverse the permission tree."""
        for p_data in permissions_list:
            children_data = p_data.pop("children", None)
            
            # Create the current permission object
            perm = ActionPermission(**p_data, parent_id=parent.id if parent else None)
            db.add(perm)
            
            # Must flush to get the ID for children
            await db.flush()
            
            # Store it in our lookup map
            permissions_map[perm.name] = perm
            
            # If there are children, recurse
            if children_data:
                await _recursive_seed(children_data, parent=perm)

    # Start the recursion from the root of the data structure
    await _recursive_seed(PERMISSIONS_DATA)
    
    print(f"  - Successfully seeded {len(permissions_map)} permissions.")
    return permissions_map

async def _seed_resource_types(db: AsyncSession):
    """播种平台支持的资源类型。"""
    print("Seeding Resource Types...")
    db.add_all([ResourceType(**data) for data in RESOURCE_TYPES_DATA])

async def _seed_roles(db: AsyncSession) -> Dict[str, Role]:
    """播种所有系统角色和团队角色模板，并返回一个按名称索引的字典。"""
    print("Seeding System Roles...")
    roles_to_create = [Role(**r_data) for r_data in ROLES_DATA]
    db.add_all(roles_to_create)
    await db.flush()
    return {r.name: r for r in roles_to_create}

async def _seed_manual_role_permissions(db: AsyncSession, roles_map: Dict[str, Role], perms_map: Dict[str, ActionPermission]):
    """
    播种手动的角色-权限关系，并根据 PLAN_HIERARCHY 自动处理订阅计划的继承。
    """
    print("Seeding manual Role-Permission Mappings with inheritance...")
    links_to_create = set() # 使用 set 来自动处理重复

    for role_name, perm_names in ROLE_PERMISSION_MAP.items():
        role = roles_map.get(role_name)
        if not role: continue
        
        # 确定当前角色在继承链中的位置
        is_plan_role = role_name in PLAN_HIERARCHY
        current_level_index = PLAN_HIERARCHY.index(role_name) if is_plan_role else -1

        for perm_name in perm_names:
            perm = perms_map.get(perm_name)
            if not perm: continue
            
            # 1. 为当前角色直接添加权限
            links_to_create.add((role.id, perm.id))
            
            # 2. 如果是订阅计划角色，则为所有更高级别的计划也添加此权限
            if is_plan_role:
                for i in range(current_level_index + 1, len(PLAN_HIERARCHY)):
                    higher_plan_role_name = PLAN_HIERARCHY[i]
                    higher_role = roles_map.get(higher_plan_role_name)
                    if higher_role:
                        print(f"  └─ Inheriting '{perm_name}' to '{higher_plan_role_name}'")
                        links_to_create.add((higher_role.id, perm.id))

    # 批量创建 RolePermission 对象
    db.add_all([RolePermission(role_id=r_id, permission_id=p_id) for r_id, p_id in links_to_create])

async def _seed_service_modules(db: AsyncSession, products_map: Dict[str, Product]):
    """播种服务模块、类型和版本。"""
    print("Seeding Service Module Types...")
    module_types_map = {data['name']: ServiceModuleType(**data) for data in SERVICE_MODULE_TYPES_DATA}
    db.add_all(module_types_map.values())
    await db.flush()

    print("Seeding Service Modules and Versions...")
    for module_data in SERVICE_MODULES_DATA:
        versions_data = module_data.pop('versions', [])
        type_name = module_data.pop('type_name')
        
        module = ServiceModule(type_id=module_types_map[type_name].id, **module_data)
        db.add(module)
        
        for version_data in versions_data:
            product_name = version_data.pop('product_name', None)
            version = ServiceModuleVersion(
                service_module=module,
                required_product_id=products_map[product_name].id if product_name in products_map else None,
                **version_data
            )
            db.add(version)
    await db.flush()

async def _seed_automated_module_permissions(db: AsyncSession, roles_map: Dict[str, Role], perms_map: Dict[str, ActionPermission]):
    """根据 ServiceModuleVersion.required_product_id 自动分配权限，并处理继承。"""
    print("Automatically assigning ServiceModule permissions to plan roles with inheritance...")
    
    versions_result = await db.execute(
        select(ServiceModuleVersion).options(
            joinedload(ServiceModuleVersion.required_product),
            joinedload(ServiceModuleVersion.service_module)
        )
    )
    
    links_to_create = set() # 同样使用 set

    for version in versions_result.scalars().unique():
        if not (version.required_product and version.service_module):
            continue
            
        base_role_name = version.required_product.name.replace('_', ':')
        perm_name = f"servicemodule:use:{version.service_module.name}"
        
        base_role = roles_map.get(base_role_name)
        permission_to_assign = perms_map.get(perm_name)
        
        if base_role and permission_to_assign and base_role_name in PLAN_HIERARCHY:
            print(f"  -> Assigning '{perm_name}' to base role '{base_role_name}'")
            links_to_create.add((base_role.id, permission_to_assign.id))
            
            # [核心继承逻辑]
            base_level_index = PLAN_HIERARCHY.index(base_role_name)
            for i in range(base_level_index + 1, len(PLAN_HIERARCHY)):
                higher_plan_role_name = PLAN_HIERARCHY[i]
                higher_role = roles_map.get(higher_plan_role_name)
                if higher_role:
                    print(f"    └─ Inheriting '{perm_name}' to '{higher_plan_role_name}'")
                    links_to_create.add((higher_role.id, permission_to_assign.id))
        else:
            print(f"  - WARNING: Could not auto-assign for module '{version.service_module.name}'. "
                  f"Base Role '{base_role_name}' or Perm '{perm_name}' not found or not a plan role.")
            
    db.add_all([RolePermission(role_id=r_id, permission_id=p_id) for r_id, p_id in links_to_create])

# ==============================================================================
# Seeding Logic
# ==============================================================================
async def seed_data(db: AsyncSession):
    """
    主编排函数，按正确的顺序调用所有独立的播种步骤。
    """
    # Idempotency Check
    if await db.scalar(select(func.count(Role.id))) > 0:
        print("Data already seeded. Skipping.")
        return

    print("Starting database seeding process...")
    
    # Step 1: 播种没有依赖或被其他步骤依赖的基础数据
    products_map = await _seed_products(db)
    await _seed_resource_types(db)
    
    # Step 2: 播种权限和角色，它们是权限系统的核心
    # 我们需要它们的查找表来创建关联
    permissions_map = await _seed_permissions(db)
    roles_map = await _seed_roles(db)
    
    # Step 3: 播种依赖于产品目录的服务模块
    # 注意：SERVICE_MODULES_DATA 需要调整，以便包含 product_name
    await _seed_service_modules(db, products_map)
    
    # Step 4: 创建角色-权限的关联关系
    # 首先是手动的
    await _seed_manual_role_permissions(db, roles_map, permissions_map)
    # 然后是自动化的
    await _seed_automated_module_permissions(db, roles_map, permissions_map)

    print("Database seeding process completed successfully.")

# ==============================================================================
# Main execution block (for standalone running)
# ==============================================================================
async def main():
    """
    当这个脚本被直接执行时，它会自己创建 engine 和 session，
    并管理事务。
    """
    print("Starting database seeding process as a standalone script...")
    engine = create_async_engine(settings.DATABASE_URL, echo=True) # echo=True 可以在调试时看到SQL
    AsyncSessionLocal = sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
        
    async with AsyncSessionLocal() as db:
        async with db.begin(): # 使用 begin() 来自动管理 commit/rollback
            await seed_data(db) # 调用核心逻辑

    await engine.dispose()
    print("Database seeding process finished.")

if __name__ == "__main__":
    asyncio.run(main())