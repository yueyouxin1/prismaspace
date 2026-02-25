# src/app/api/router.py

from fastapi import APIRouter
from app.api.v1 import identity
from app.api.v1 import team
from app.api.v1 import workspace
from app.api.v1 import asset
from app.api.v1 import project
from app.api.v1 import resource_type
from app.api.v1 import resource
from app.api.v1 import uiapp
from app.api.v1 import tenantdb
from app.api.v1 import execution
from app.api.v1 import knowledge
from app.api.v1 import module
from app.api.v1 import module_type
from app.api.v1 import module_provider
from app.api.v1 import entitlement
from app.api.v1.credential import router as credential_workspace_router, supported_providers_router
from app.api.v1 import role
from app.api.v1 import permission
from app.api.v1 import feature
from app.api.v1 import product
from app.api.v1 import chat
from app.api.v1.agent import agent_api, agent_memory
from app.api.v1.workflow import workflow_api

# The main router for API v1
router = APIRouter(prefix="/api/v1")

# ===================================================================
# System & Platform Management Routes
# ===================================================================

router.include_router(
    permission.router,
    prefix="/permissions",
    tags=["System - Permissions"]
)
router.include_router(
    feature.router,
    prefix="/features",
    tags=["System - Products & Features"] # Grouped for clarity
)
router.include_router(
    product.router,
    prefix="/products",
    tags=["System - Products & Features"] # Grouped for clarity
)
router.include_router(
    module_type.router,
    prefix="/service-module-types", 
    tags=["System - Service Modules"] # Grouped for clarity
)
router.include_router(
    module_provider.router,
    prefix="/service-module-providers", 
    tags=["System - Service Modules"] # Grouped for clarity
)
router.include_router(
    module.router,
    prefix="/service-modules", 
    tags=["System - Service Modules"] # Grouped for clarity
)

# ===================================================================
# Identity, Team & Entitlement Routes
# ===================================================================

router.include_router(identity.router, prefix="/identity", tags=["Identity & Users"])
router.include_router(team.router, prefix="/teams", tags=["Teams"])
router.include_router(
    role.router,
    prefix="/teams/{team_uuid}/roles",
    tags=["Teams"] # Keep roles under the main Teams tag
)
# [NEW] Add Entitlement routes
router.include_router(
    entitlement.router,
    prefix="/entitlements",
    tags=["Entitlements"]
)


# ===================================================================
# Core Creation & Execution Workflow Routes
# ===================================================================

router.include_router(workspace.router, prefix="/workspaces", tags=["Workspaces & Projects"])
router.include_router(asset.router, prefix="/assets", tags=["Workspaces & Assets"])
# ===================================================================
# Credential Management Routes
# ===================================================================

router.include_router(
    credential_workspace_router,
    prefix="/workspaces/{workspace_uuid}/credentials/service-modules",
    tags=["Credentials"]
)
router.include_router(
    supported_providers_router,
    prefix="/credentials",
    tags=["Credentials"]
)
router.include_router(
    project.workspace_router, 
    prefix="/workspaces/{workspace_uuid}/projects", 
    tags=["Workspaces & Projects"]
)
router.include_router(project.router, prefix="/projects", tags=["Workspaces & Projects"])
router.include_router(
    resource_type.router,
    prefix="/resource-types",
    tags=["Resources"]
)
router.include_router(
    resource.workspace_router,
    prefix="/workspaces/{workspace_uuid}/resources",
    tags=["Resources"]
)
router.include_router(
    resource.project_router,
    prefix="/projects/{project_uuid}/resources",
    tags=["Resources"]
)
router.include_router(
    resource.resource_router,
    prefix="/resources",
    tags=["Resources"]
)
router.include_router(
    resource.instance_router,
    prefix="/instances",
    tags=["Resources"]
)

# Resource-specific routes
router.include_router(uiapp.router, prefix="/uiapps", tags=["Resources - UiApp"])
router.include_router(tenantdb.router, prefix="/tenantdb", tags=["Resource - TenantDB"])
router.include_router(knowledge.router, prefix="/knowledge", tags=["Resource - KnowledgeBase"])
router.include_router(agent_api.router, prefix="/agent", tags=["Resource - Agent"])
router.include_router(agent_memory.router, prefix="/agent/memory", tags=["Resource - Agent - Memory"])
router.include_router(workflow_api.router, prefix="/workflow", tags=["Resource - Workflow"])

# The universal execution endpoint
router.include_router(execution.router, prefix="/execute", tags=["Execution"])

router.include_router(chat.router, prefix="/chat", tags=["Interaction - Chat"])
