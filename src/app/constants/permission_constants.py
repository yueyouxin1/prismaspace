# app/constants/permission_constants.py

from app.models import ActionPermissionType

# 添加权限时确保(抽象根)被包含，否则追溯到源头发现没有(抽象根)权限将无法通过认证, 禁用抽象根代表整个子域权限失效，对于恶意用户我们可以直接从他的权限集中删除抽象根，使其无法继续使用服务
# 平台根权限 (仅超级管理员可用)
PLATFORM_ROOT_PERM = 'platform'

# 用户个人账户根权限
USER_ROOT_PERM = 'user'

# 团队管理根权限
TEAM_ROOT_PERM = 'team'

# 计费管理根权限
BILLING_ROOT_PERM = 'billing'

# 服务模块根权限
SERVICE_MODULE_ROOT_PERM = 'servicemodule'

# 工作空间根权限
WORKSPACE_ROOT_PERM = 'workspace'

# 项目根权限
PROJECT_ROOT_PERM = 'project'

# 资源根权限
RESOURCE_ROOT_PERM = 'resource'

# 审计日志根权限
AUDIT_ROOT_PERM = 'audit'

# UI界面根权限
UI_ROOT_PERM = 'ui'

# 平台与UI基础权限
UI_BASE_PERMS = [UI_ROOT_PERM, 'page:dashboard']

# 个人账户管理权限 (不可被团队分配)
# 出现系统漏洞或恶意用户时开发者有权禁用其权限
USER_SELF_MANAGEMENT_PERMS = [
    USER_ROOT_PERM, 'user:profile:read', 'user:profile:write', 'user:security:write', 'user:account:delete',
    'user:apikey:read', 'user:apikey:create', 'user:apikey:delete', 
]

# 协作空间内的基础操作权限 (可被团队分配)
# 虽然会自动添加抽象根，但为了清晰还是手动加上
COLLABORATION_ACTION_PERMS = [
    SERVICE_MODULE_ROOT_PERM,
    AUDIT_ROOT_PERM, 'audit:trace:read',
    WORKSPACE_ROOT_PERM, 'workspace:create', 'workspace:read', 'workspace:update', 'workspace:delete',
    PROJECT_ROOT_PERM, 'project:create', 'project:read', 'project:update', 'project:delete', 'project:publish', 'project:publish:marketplace', 'project:template:create', 'project:template:use', 'project:share', 'project:comment:read', 'project:comment:write',
    RESOURCE_ROOT_PERM, 'resource:create', 'resource:read', 'resource:update', 'resource:delete',
    'resource:publish', 'resource:publish:marketplace', 'resource:share', 'resource:execute',
    # 凭证管理权限也属于协作空间内的操作
    'workspace:credential:servicemodule:read',
    'workspace:credential:servicemodule:create',
    'workspace:credential:servicemodule:update',
    'workspace:credential:servicemodule:delete',
]

# --- 2. 组合角色权限 ---

# 免费计划 = 个人管理权 + 协作操作权 + 平台基础权 + 个人计费权
# 免费用户拥有大多数权限，业务层负责用量限制
PLAN_FREE_PERMS = (
    UI_BASE_PERMS + 
    USER_SELF_MANAGEMENT_PERMS + 
    COLLABORATION_ACTION_PERMS + 
    [BILLING_ROOT_PERM, 'billing:read', 'billing:manage']
)

# Pro计划在Free基础上增加
# _seed_automated_module_permissions负责账户订阅权限自动化继承
PLAN_PRO_PERMS = ['project:publish:api', 'resource:publish:api']

# Team计划在Pro基础上增加
# 表示团队归属人
PLAN_TEAM_PERMS = ['team:create']

# 未来扩展
PLAN_ENTERPRISE_PERMS = []

# --- Team Permissions: 仍然使用拼接，因为没有自动化继承逻辑 ---

# 团队成员 = 协作操作权 + 团队基础权
# 默认预设权限用于展示一组安全模板, 实际团队管理员负责分配成员权限
TEAM_MEMBER_PERMS = COLLABORATION_ACTION_PERMS + [TEAM_ROOT_PERM, 'team:read', 'team:member:read']

# 团队管理员在成员基础上增加...
TEAM_ADMIN_PERMS = (
    TEAM_MEMBER_PERMS + [
        'team:update',
        'team:member:invite', 'team:member:remove', 'team:member:role:update',
        'team:role:read', 'team:role:write',
        'team:apikey:read', 'team:apikey:create', 'team:apikey:delete'
        # 注意：团队管理员的凭证管理权限已经包含在 COLLABORATION_ACTION_PERMS 中了
    ]
)

# 团队所有者在管理员基础上增加
TEAM_OWNER_PERMS = (
    TEAM_ADMIN_PERMS + [
        'team:delete',
        BILLING_ROOT_PERM, 'billing:read', 'billing:manage',
    ]
)

# 团队财务角色
TEAM_BILLING_MANAGER_PERMS = [
    BILLING_ROOT_PERM, 'billing:read', 'billing:manage',
]

PERMISSIONS_DATA = [
    # 1. PLATFORM DOMAIN (Super Admin)
    {
        'name': PLATFORM_ROOT_PERM, 'label': '平台管理', 'description': '平台级超级管理员权限根节点', 'type': ActionPermissionType.ABILITY, 'is_assignable': False,
        'children': [
            {'name': 'platform:user:impersonate', 'label': '[Admin] 模拟用户登录', 'description': '以特定用户身份登录，用于调试和客户支持。', 'type': ActionPermissionType.API, 'is_assignable': False},
            {'name': 'platform:billing:manage_all', 'label': '[Admin] 管理所有计费', 'description': '查看和修改任何用户或团队的计费信息。', 'type': ActionPermissionType.API, 'is_assignable': False},
            {'name': 'platform:workspace:manage_all', 'label': '[Admin] 管理所有资源', 'description': '查看、暂停或删除平台上的任何空间。', 'type': ActionPermissionType.API, 'is_assignable': False},
            {'name': 'platform:resourcetype:manage', 'label': '[Admin] 管理资源类型', 'description': '创建、更新或删除平台支持的资源类型。', 'type': ActionPermissionType.API, 'is_assignable': False},
            {'name': 'platform:permission:manage', 'label': '[Admin] 管理权限定义', 'description': '创建、更新或删除平台的所有权限定义。', 'type': ActionPermissionType.API, 'is_assignable': False},
            {'name': 'platform:product:manage', 'label': '[Admin] 管理产品目录', 'description': '创建、更新或下线平台上的所有产品、权益和价格。', 'type': ActionPermissionType.API, 'is_assignable': False},
            {'name': 'platform:servicemodule:manage', 'label': '[Admin] 管理服务模块', 'description': '添加、更新或下线平台提供的AI能力。', 'type': ActionPermissionType.API, 'is_assignable': False},
            {'name': 'platform:marketplace:manage', 'label': '管理官方市场', 'description': '审核及管理官方市场。', 'type': ActionPermissionType.API, 'is_assignable': False},
            {'name': 'platform:audit:activity_log:read', 'label': '查看用户行为日志', 'type': ActionPermissionType.API, 'is_assignable': False},
        ]
    },

    # 2. USER DOMAIN (Personal Account Management)
    {
        'name': USER_ROOT_PERM, 'label': '个人账户域', 'description': '管理用户个人账户的权限根节点', 'type': ActionPermissionType.ABILITY, 'is_assignable': False,
        'children': [
            {
                'name': 'user:profile:read', 'label': '查看个人资料', 'type': ActionPermissionType.API, 'is_assignable': False,
                'children': [
                    {'name': 'user:profile:write', 'label': '修改个人资料', 'type': ActionPermissionType.API, 'is_assignable': False},
                ]
            },
            {'name': 'user:security:write', 'label': '修改安全设置', 'type': ActionPermissionType.API, 'is_assignable': False},
            {'name': 'user:account:delete', 'label': '删除个人账户', 'type': ActionPermissionType.API, 'is_assignable': False},
            {
                'name': 'user:apikey:read', 'label': '查看个人API密钥', 'type': ActionPermissionType.API, 'is_assignable': False,
                'children': [
                    {'name': 'user:apikey:create', 'label': '创建个人API密钥', 'type': ActionPermissionType.API, 'is_assignable': False},
                    {'name': 'user:apikey:delete', 'label': '吊销个人API密钥', 'type': ActionPermissionType.API, 'is_assignable': False},
                ]
            },
        ]
    },

    # 3. TEAM DOMAIN (Team Management)
    {
        'name': TEAM_ROOT_PERM, 'label': '团队域', 'description': '管理团队的权限根节点', 'type': ActionPermissionType.ABILITY, 'is_assignable': True,
        'children': [
            {'name': 'team:create', 'label': '创建团队', 'description': '允许用户创建一个新的团队。', 'type': ActionPermissionType.API, 'is_assignable': True},
            {
                'name': 'team:read', 'label': '查看团队信息', 'description': '读取团队的基本信息。', 'type': ActionPermissionType.API, 'is_assignable': True,
                'children': [
                    {'name': 'team:update', 'label': '更新团队信息', 'type': ActionPermissionType.API, 'is_assignable': True},
                    {'name': 'team:delete', 'label': '删除团队', 'type': ActionPermissionType.API, 'is_assignable': False},
                    {
                        'name': 'team:member:read', 'label': '查看团队成员', 'type': ActionPermissionType.API, 'is_assignable': True,
                        'children': [
                            {'name': 'team:member:invite', 'label': '邀请新成员', 'type': ActionPermissionType.API, 'is_assignable': True},
                            {'name': 'team:member:remove', 'label': '移除团队成员', 'type': ActionPermissionType.API, 'is_assignable': True},
                            {'name': 'team:member:role:update', 'label': '修改成员角色', 'type': ActionPermissionType.API, 'is_assignable': True},
                        ]
                    },
                    {
                        'name': 'team:role:read', 'label': '查看团队角色', 'type': ActionPermissionType.API, 'is_assignable': True,
                        'children': [
                            {'name': 'team:role:write', 'label': '管理团队角色', 'type': ActionPermissionType.API, 'is_assignable': False},
                        ]
                    },
                    {
                        'name': 'team:apikey:read', 'label': '查看团队API密钥', 'type': ActionPermissionType.API, 'is_assignable': True,
                        'children': [
                            # 平台入站ApiKey体系，未来是多对多的授权系统，所有者可以为其关联一个或多个Workspace的访问权限，需要正确传递角色上下文，apikey推断是团队上下文时检查以下权限
                            {'name': 'team:apikey:create', 'label': '创建团队API密钥', 'type': ActionPermissionType.API, 'is_assignable': True},
                            {'name': 'team:apikey:delete', 'label': '吊销团队API密钥', 'type': ActionPermissionType.API, 'is_assignable': True},
                        ]
                    },
                ]
            },
        ]
    },

    # 4. BILLING DOMAIN
    {
        'name': BILLING_ROOT_PERM, 'label': '计费域', 'description': '管理计费和订阅的权限根节点', 'type': ActionPermissionType.ABILITY, 'is_assignable': False,
        'children': [
            {
                'name': 'billing:read', 'label': '查看计费信息', 'type': ActionPermissionType.API, 'is_assignable': False,
                'children': [
                    {'name': 'billing:manage', 'label': '管理订阅与支付', 'type': ActionPermissionType.API, 'is_assignable': False},
                ]
            }
        ]
    },

    # 5. MODULE DOMAIN
    {
        'name': SERVICE_MODULE_ROOT_PERM, 'label': '服务模块域', 'description': '使用平台核心AI能力的权限根节点', 'type': ActionPermissionType.ABILITY, 'is_assignable': True,
        'children': []
    },

    # 6. WORKSPACE DOMAIN
    # 拥有workspace:read权限不代表拥有project权限，它们是and的关系，不是or的关系。要访问project必须同时具有[WORKSPACE_ROOT_PERM, 'workspace:read', RESOURCE_ROOT_PERM, 'project:read'],以此类推。
    {
        'name': WORKSPACE_ROOT_PERM, 'label': '工作空间域', 'description': '管理工作空间的权限根节点', 'type': ActionPermissionType.ABILITY, 'is_assignable': True,
        'children': [
            {'name': 'workspace:create', 'label': '创建工作空间', 'type': ActionPermissionType.API, 'is_assignable': True},
            {
                'name': 'workspace:read', 'label': '访问工作空间', 'description': '能够看到并进入一个工作空间。这是所有空间内操作的基础。', 'type': ActionPermissionType.ABILITY, 'is_assignable': True,
                'children': [
                    {'name': 'workspace:update', 'label': '更新工作空间设置', 'type': ActionPermissionType.API, 'is_assignable': True},
                    {'name': 'workspace:delete', 'label': '删除工作空间', 'type': ActionPermissionType.API, 'is_assignable': True},
                    {
                        'name': 'workspace:credential:servicemodule:read', 'label': '查看空间服务凭证', 'type': ActionPermissionType.API, 'is_assignable': True,
                        'children': [
                            # 用户提供的、与特定空间绑定的出站凭证，用户可以基于此自定义服务模块的凭证，否则默认消耗平台用量
                            {'name': 'workspace:credential:servicemodule:create', 'label': '创建空间服务凭证', 'type': ActionPermissionType.API, 'is_assignable': True},
                            {'name': 'workspace:credential:servicemodule:update', 'label': '更新空间服务凭证', 'type': ActionPermissionType.API, 'is_assignable': True},
                            {'name': 'workspace:credential:servicemodule:delete', 'label': '删除空间服务凭证', 'type': ActionPermissionType.API, 'is_assignable': True},
                        ]
                    },
                    # 7. PROJECT DOMAIN
                    {
                        'name': PROJECT_ROOT_PERM, 'label': '项目域', 'description': '管理项目的权限根节点', 'type': ActionPermissionType.ABILITY, 'is_assignable': True,
                        'children': [
                            {'name': 'project:create', 'label': '创建项目', 'type': ActionPermissionType.API, 'is_assignable': True},
                            {
                                'name': 'project:read', 'label': '访问项目', 'description': '能够看到并打开一个项目。这是所有项目内操作的基础。', 'type': ActionPermissionType.ABILITY, 'is_assignable': True,
                                'children': [
                                    {'name': 'project:update', 'label': '更新项目设置', 'type': ActionPermissionType.API, 'is_assignable': True},
                                    {'name': 'project:delete', 'label': '删除项目', 'type': ActionPermissionType.API, 'is_assignable': True},
                                    {'name': 'project:publish', 'label': '发布项目', 'type': ActionPermissionType.API, 'is_assignable': True, 
                                    'children': [
                                        {'name': 'project:publish:marketplace', 'label': '发布项目到市场', 'type': ActionPermissionType.API, 'is_assignable': True},
                                        {'name': 'project:publish:api', 'label': '发布项目到API', 'type': ActionPermissionType.API, 'is_assignable': True},
                                    ]
                                    },
                                    {'name': 'project:template:create', 'label': '创建项目模板', 'description': '将一个现有项目保存为模板。', 'type': ActionPermissionType.API, 'is_assignable': True},
                                    {'name': 'project:template:use', 'label': '使用项目模板', 'description': '从一个模板创建一个新的项目。', 'type': ActionPermissionType.API, 'is_assignable': True},
                                    {'name': 'project:share', 'label': '分享项目', 'description': '允许用户与特定用户或通过链接分享项目。', 'type': ActionPermissionType.API, 'is_assignable': True},
                                    {'name': 'project:comment:read', 'label': '查看项目评论', 'description': '读取项目中的评论。', 'type': ActionPermissionType.API, 'is_assignable': True},
                                    {'name': 'project:comment:write', 'label': '发表项目评论', 'description': '在项目中添加、编辑或删除自己的评论。', 'type': ActionPermissionType.API, 'is_assignable': True},
                                    # 8. RESOURCE DOMAIN
                                    {
                                        'name': RESOURCE_ROOT_PERM, 'label': '资源域', 'description': '管理资源的权限根节点', 'type': ActionPermissionType.ABILITY, 'is_assignable': True,
                                        'children': [
                                            {'name': 'resource:create', 'label': '创建资源', 'type': ActionPermissionType.API, 'is_assignable': True},
                                            {
                                                'name': 'resource:read', 'label': '访问资源', 'type': ActionPermissionType.API, 'is_assignable': True,
                                                'children': [
                                                    {'name': 'resource:update', 'label': '编辑资源', 'type': ActionPermissionType.API, 'is_assignable': True},
                                                    {'name': 'resource:delete', 'label': '删除资源', 'type': ActionPermissionType.API, 'is_assignable': True},
                                                    {'name': 'resource:publish', 'label': '发布资源', 'type': ActionPermissionType.API, 'is_assignable': True, 
                                                    'children': [
                                                        {'name': 'resource:publish:marketplace', 'label': '发布资源到市场', 'type': ActionPermissionType.API, 'is_assignable': True},
                                                        {'name': 'resource:publish:api', 'label': '发布到API', 'type': ActionPermissionType.API, 'is_assignable': True},
                                                    ]
                                                    },
                                                    {'name': 'resource:execute', 'label': '执行/调用资源', 'type': ActionPermissionType.API, 'is_assignable': True},
                                                    {'name': 'resource:share', 'label': '分享资源', 'description': '允许用户分享项目中的单个资源。', 'type': ActionPermissionType.API, 'is_assignable': True},
                                                ]
                                            }
                                        ]
                                    },
                                ]
                            },
                        ]
                    },
                ]
            },
        ]
    },
    
    # 9. AUDIT DOMAIN
    {
        'name': AUDIT_ROOT_PERM, 'label': '审计域', 'description': '审计与日志查看的权限根节点', 'type': ActionPermissionType.ABILITY, 'is_assignable': True,
        'children': [
            {'name': 'audit:trace:read', 'label': '查看应用调用日志', 'type': ActionPermissionType.API, 'is_assignable': True},
        ]
    },
    
    # 10. UI DOMAIN (PAGE & ACTION)
    {
        'name': UI_ROOT_PERM, 'label': 'UI域', 'description': '前端界面交互权限根节点', 'type': ActionPermissionType.ABILITY, 'is_assignable': True,
        'children': [
            {'name': 'page:dashboard', 'label': '访问仪表盘', 'type': ActionPermissionType.PAGE, 'is_assignable': True},
            # ... other page and action permissions can be added here ...
        ]
    },
]