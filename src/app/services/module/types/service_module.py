from dataclasses import dataclass
from typing import List, Optional
from app.models.product import Feature
from app.models.module import ServiceModule, ServiceModuleVersion
from .credential import ResolvedCredential

@dataclass
class ModuleRuntimeContext:
    """
    A secure, self-contained data class that provides everything needed
    to execute a specific service module version.
    It is the trusted result of the ServiceModuleService.get_runtime_context method.
    """
    module: ServiceModule
    version: ServiceModuleVersion
    features: List[Feature]
    credential: Optional[ResolvedCredential]