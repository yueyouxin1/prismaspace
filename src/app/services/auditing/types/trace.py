from pydantic import BaseModel, Field
from typing import Optional, Dict, Any
from decimal import Decimal
import uuid
from app.models import TraceStatus

class TraceCreateParams(BaseModel):
    pass