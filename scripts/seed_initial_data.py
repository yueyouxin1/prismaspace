import asyncio
import json
import logging
import sys
from pathlib import Path
from typing import Type, List

from pydantic import BaseModel, ValidationError
from sqlalchemy import select, func
from sqlalchemy.ext.asyncio import create_async_engine, AsyncSession
from sqlalchemy.orm import sessionmaker

# --- Path Setup ---
# This ensures the script can be run from the project root and find the 'app' module.
# Example command from project root: `poetry run python src/scripts/seed_initial_data.py`
try:
    import app
except ImportError:
    sys.path.append(str(Path(__file__).resolve().parent.parent))

from app.core.config import settings
from app.models import Role  # Used for idempotency check

# --- Import all necessary Managers ---
from app.system.permission.permission_manager import PermissionManager
from app.system.permission.role_manager import RoleManager
from app.system.module.service_module_type_manager import ServiceModuleTypeManager
from app.system.module.service_module_provider_manager import ServiceModuleProviderManager
from app.system.module.service_module_manager import ServiceModuleManager
from app.system.vectordb.manager import SystemVectorManager
from app.system.resource.resource_type_manager import ResourceTypeManager
from app.system.product.feature_manager import FeatureManager
from app.system.product.product_manager import ProductManager
from app.system.product.price_manager import PriceManager
from app.system.product.product_entitlement_manager import ProductEntitlementManager
from app.system.resource.workflow.node_def_manager import NodeDefManager

# --- Import all necessary Create Schemas ---
from app.schemas.permission.permission_schemas import PermissionCreate
from app.schemas.permission.role_schemas import RoleCreate
from app.schemas.module.service_module_type_schemas import ServiceModuleTypeCreate
from app.schemas.module.service_module_provider_schemas import ServiceModuleProviderCreate
from app.schemas.module.service_module_schemas import ServiceModuleCreateFull
from app.schemas.resource.resource_type_schemas import ResourceTypeCreate
from app.schemas.product.product_schemas import FeatureCreate, ProductCreateFull
from app.engine.vector.main import VectorEngineManager, VectorEngineConfig

# --- Setup Logging ---
logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')
logger = logging.getLogger(__name__)

# --- Data Loading and Validation Helper ---
SEED_DATA_DIR = Path(__file__).resolve().parent.parent / "seed_data"

def _load_and_validate_data(file_name: str, schema: Type[BaseModel]) -> List[BaseModel]:
    """
    Helper to load, parse, and robustly validate data from a JSON file.
    """
    path = SEED_DATA_DIR / file_name
    if not path.exists():
        logger.error(f"Seed data file not found: {path}")
        raise FileNotFoundError(f"Seed data file not found: {path}")

    logger.info(f"  - Loading and validating {file_name}...")
    # Force UTF-8 to avoid platform default encoding issues (e.g. GBK on Windows).
    data = json.loads(path.read_text(encoding="utf-8"))
    try:
        # Pydantic's `parse_obj` is an alias for `model_validate` for lists
        return [schema.model_validate(item) for item in data]
    except ValidationError as e:
        logger.critical(f"FATAL: Validation failed for {file_name}. See details below.")
        # Provide detailed error context for easier debugging
        for error in e.errors():
            logger.critical(f"  - Location: {error['loc']} | Error: {error['msg']}")
        raise

# --- Modular Seeding Functions (in dependency order) ---

async def _seed_permissions(db: AsyncSession):
    manager = PermissionManager(db)
    data = _load_and_validate_data("permissions.json", PermissionCreate)
    for item in data:
        await manager.create_permission(item)

async def _seed_service_module_types(db: AsyncSession):
    manager = ServiceModuleTypeManager(db)
    data = _load_and_validate_data("service_module_types.json", ServiceModuleTypeCreate)
    for item in data:
        await manager.create_type(item)

async def _seed_service_module_providers(db: AsyncSession):
    manager = ServiceModuleProviderManager(db)
    data = _load_and_validate_data("service_module_providers.json", ServiceModuleProviderCreate)
    for item in data:
        await manager.create_provider(item)

async def _seed_roles(db: AsyncSession):
    manager = RoleManager(db)
    data = _load_and_validate_data("roles.json", RoleCreate)
    for item in data:
        await manager.create_role(item)  # team_id is None for system roles

async def _seed_resource_types(db: AsyncSession):
    manager = ResourceTypeManager(db)
    data = _load_and_validate_data("resource_types.json", ResourceTypeCreate)
    for item in data:
        await manager.create_resource_type(item)

async def _seed_service_modules(db: AsyncSession):
    manager = ServiceModuleManager(db)
    data = _load_and_validate_data("service_modules.json", ServiceModuleCreateFull)
    for item in data:
        await manager.create_module_with_versions(item)

async def _seed_system_collections(db: AsyncSession):
    """
    [NEW STEP] 初始化系统级向量集合。
    这一步必须在 _seed_service_modules 之后执行。
    """
    print("--- Seeding System Vector Collections ---")
    
    # 1. 临时初始化 VectorEngineManager (因为它通常在 main.py lifespan 中初始化)
    # 在 Seeding 脚本中我们需要手动构建它
    engine_configs = [VectorEngineConfig(**config_dict) for config_dict in settings.VECTOR_ENGINE_CONFIGS]
    vector_manager = VectorEngineManager(configs=engine_configs)
    
    try:
        # 启动连接
        await vector_manager.startup()
        
        # 2. 运行 Manager 逻辑
        manager = SystemVectorManager(db, vector_manager)
        await manager.initialize_system_collections()
        
    finally:
        # 关闭连接
        await vector_manager.shutdown()

async def _seed_features(db: AsyncSession):
    manager = FeatureManager(db)
    data = _load_and_validate_data("features.json", FeatureCreate)
    for item in data:
        await manager.create_feature(item)

async def _seed_products(db: AsyncSession):
    product_manager = ProductManager(db)
    data = _load_and_validate_data("products.json", ProductCreateFull)
    for item in data:
        new_product = await product_manager.create_full_product(item)

async def _seed_workflow_nodes(db: AsyncSession):
    node_manager = NodeDefManager(db)
    await node_manager.sync_nodes()

# --- Main Orchestrator ---

async def seed_all_data(db: AsyncSession):
    """Orchestrates the entire seeding process in the correct order."""
    
    # 1. Idempotency Check
    if await db.scalar(select(func.count(Role.id))) > 0:
        logger.warning("Data appears to be already seeded. Skipping.")
        return

    logger.info("Starting database seeding process...")
    
    seeding_steps = [
        ("Permissions", _seed_permissions),
        ("Service Module Types", _seed_service_module_types),
        ("Service Module Providers", _seed_service_module_providers),
        ("Roles", _seed_roles),
        ("Resource Types", _seed_resource_types),
        ("Service Modules", _seed_service_modules),
        ("System Vector Collections", _seed_system_collections),
        ("Features", _seed_features),
        ("Products", _seed_products),
        ("Workflow Nodes", _seed_workflow_nodes),
    ]

    for name, step_func in seeding_steps:
        logger.info(f"Step: Seeding {name}...")
        await step_func(db)
        logger.info(f"Step: {name} seeded successfully.")

    logger.info("\nDatabase seeding process completed successfully.")

# --- Main execution block ---

async def main():
    """Sets up the database connection and runs the seeding orchestrator within a single transaction."""
    engine = create_async_engine(settings.DATABASE_URL, echo=False)
    AsyncSessionLocal = sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)

    try:
        async with AsyncSessionLocal() as db:
            async with db.begin():  # Single transaction for the whole process
                await seed_all_data(db)
    except Exception as e:
        logger.critical(f"\nFATAL ERROR during seeding: An exception occurred, and the transaction has been rolled back.", exc_info=True)
        sys.exit(1) # Exit with a non-zero status code to signal failure in CI/CD
    finally:
        await engine.dispose()

if __name__ == "__main__":
    logger.info("Running seed script as a standalone process...")
    asyncio.run(main())
