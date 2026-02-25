import logging
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.dialects.postgresql import insert
from app.services.resource.workflow import nodes # 触发 import 注册
from app.models.resource.workflow import WorkflowNodeDef
from app.engine.workflow.registry import default_node_registry

logger = logging.getLogger(__name__)

class NodeDefManager:
    def __init__(self, db: AsyncSession):
        self.db = db

    async def sync_nodes(self):
        """
        从代码注册表中提取 NodeTemplate，同步到数据库。
        """
        all_templates = default_node_registry.get_all_templates()
        
        for node_method, template in all_templates.items():
            
            # 1. 序列化 node (Payload)
            node_data_json = template.data.model_dump(mode='json', exclude_none=True)
            
            # 2. 序列化表单定义
            forms_data = [f.model_dump(mode='json', exclude_none=True) for f in template.forms]
            
            # 3. Upsert
            stmt = insert(WorkflowNodeDef).values(
                registry_id=template.registry_id,
                category=template.category.value,
                icon=template.icon,
                display_order=template.display_order,
                is_active=template.is_active,
                data=node_data_json, # [Change] 存入 data
                forms=forms_data
            ).on_conflict_do_update(
                index_elements=['registry_id'],
                set_=dict(
                    category=template.category.value,
                    icon=template.icon,
                    display_order=template.display_order,
                    is_active=template.is_active,
                    data=node_data_json, # [Change] 存入 data
                    forms=forms_data
                )
            )

            await self.db.execute(stmt)
        
        await self.db.flush()
        logger.info(f"Synced {len(all_templates)} workflow node templates to database.")