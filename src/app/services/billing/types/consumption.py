# src/app/services/billing/types/consumption.py

from decimal import Decimal
from typing import NamedTuple

class Budget(NamedTuple):
    """代表一份已预留的预算"""
    total_usage_limit: Decimal
    lock_key: str