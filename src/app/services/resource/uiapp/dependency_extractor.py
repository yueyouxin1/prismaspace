# src/app/services/resource/uiapp/dependency_extractor.py

import logging
from typing import List
from app.schemas.resource.uiapp.node import UiNode
from app.schemas.resource.uiapp.action import UiAction, ExecuteWorkflowAction, ConditionAction
from app.schemas.resource.resource_ref_schemas import ReferenceCreate

logger = logging.getLogger(__name__)

class DependencyExtractor:
    def extract_from_nodes(self, nodes: List[UiNode]) -> List[ReferenceCreate]:
        refs = []
        if not nodes:
            return refs
            
        for node in nodes:
            # [IMPROVEMENT] Add context to error logs
            try:
                refs.extend(self._process_node(node))
            except Exception as e:
                logger.error(f"Error extracting dependencies from node {getattr(node, 'id', 'unknown')} ({getattr(node, 'semanticRole', 'unknown')}): {e}", exc_info=True)
                # Re-raise ensures we don't silently save corrupt state causing data loss
                raise e 
        return refs

    def _process_node(self, node: UiNode) -> List[ReferenceCreate]:
        refs = []
        # 1. Events (DSL: event)
        if node.event:
            for trigger, actions in node.event.items():
                if not actions: continue
                for action in actions:
                    refs.extend(self._process_action(action, source_node_id=node.id))
        
        # 2. Children
        if node.children:
            for child in node.children:
                refs.extend(self._process_node(child))
        return refs

    def _process_action(self, action: UiAction, source_node_id: str) -> List[ReferenceCreate]:
        refs = []
        
        # Accessing snake_case properties provided by CamelModel
        if isinstance(action, ExecuteWorkflowAction):
            # action.params is ExecuteWorkflowActionParams
            wf_id_prop = action.params.workflow_id 
            
            # workflow_id is a ValueProperty, check its value
            if wf_id_prop and wf_id_prop.value:
                refs.append(ReferenceCreate(
                    target_instance_uuid=wf_id_prop.value,
                    source_node_uuid=source_node_id,
                    alias="Call Workflow",
                    options={"action": "executeWorkflow"}
                ))

        # Recursive processing
        if action.on_success:
            for sub in action.on_success: 
                refs.extend(self._process_action(sub, source_node_id))
        if action.on_error:
            for sub in action.on_error: 
                refs.extend(self._process_action(sub, source_node_id))
        
        if isinstance(action, ConditionAction):
            for sub in action.params.then_actions: 
                refs.extend(self._process_action(sub, source_node_id))
            if action.params.else_actions:
                for sub in action.params.else_actions: 
                    refs.extend(self._process_action(sub, source_node_id))

        return refs