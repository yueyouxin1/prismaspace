# app/constants/role_constants.py

from .product_constants import (
    PLAN_FREE, 
    PLAN_PRO, 
    PLAN_TEAM, 
    PLAN_ENTERPRISE
)

# --- System Role Names ---
# 通用ROLE_前缀
# 不可编辑权限的系统角色，PLAN角色必须和product name一致
ROLE_PLAN_FREE = PLAN_FREE
ROLE_PLAN_PRO = PLAN_PRO
ROLE_PLAN_TEAM = PLAN_TEAM
ROLE_PLAN_ENTERPRISE = PLAN_ENTERPRISE
ROLE_TEAM_OWNER = "team:owner"
# 可编辑权限的预设角色
ROLE_TEAM_ADMIN = "team:admin"
ROLE_TEAM_MEMBER = "team:member"
ROLE_TEAM_BILLING_MANAGER = "team:billing_manager"