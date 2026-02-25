# src/app/services/permission/hierarchy.py

import logging
from typing import Dict, Set, List
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select
from sqlalchemy.orm import selectinload
from app.models import ActionPermission

async def preload_permission_hierarchy(db: AsyncSession) -> Dict[str, Set[str]]:
    """
    Loads the entire permission tree from the database and builds an efficient,
    in-memory ancestry map. This map is the engine for our permission inheritance.
    
    The map's key is a permission name, and its value is a set of all its
    ancestor permission names.
    
    This function should be called once at application startup.
    """
    logging.info("Preloading permission hierarchy into memory...")
    
    try:
        # Eagerly load all permissions and their parent relationships in one go.
        # This is more efficient than lazy loading within a loop.
        stmt = select(ActionPermission).options(selectinload(ActionPermission.parent))
        result = await db.execute(stmt)
        all_perms: List[ActionPermission] = result.scalars().unique().all()
        
        # Use a dictionary for quick access by name
        perms_map: Dict[str, ActionPermission] = {p.name: p for p in all_perms}
        ancestry_map: Dict[str, Set[str]] = {}

        # For each permission, traverse up its parent chain to build the ancestry set.
        for name, perm in perms_map.items():
            ancestors = set()
            current_perm = perm
            # The loop terminates when a permission has no parent (i.e., it's a root).
            while current_perm.parent:
                parent_perm = current_perm.parent
                ancestors.add(parent_perm.name)
                # Move up the tree. Because we eager-loaded, this is a fast, in-memory access.
                current_perm = parent_perm
            
            ancestry_map[name] = ancestors
            
        logging.info(f"  -> Permission hierarchy loaded successfully with {len(ancestry_map)} entries.")
        return ancestry_map
        
    except Exception as e:
        print(f"FATAL: Failed to preload permission hierarchy: {e}", exc_info=True)
        # In a real application, you might want to prevent startup if this fails.
        return {}