import pytest

from app.services.exceptions import ServiceException
from app.services.resource.workflow.protocol_bridge import WorkflowProtocolBridgeService


def test_resolve_workflow_protocol_adapter_defaults_to_wrp():
    adapter = WorkflowProtocolBridgeService.resolve_adapter(None)

    assert adapter.protocol == "wrp"


def test_resolve_workflow_protocol_adapter_rejects_unimplemented_protocol():
    with pytest.raises(ServiceException, match="reserved but not implemented yet"):
        WorkflowProtocolBridgeService.resolve_adapter("chatflow-ag-ui")
