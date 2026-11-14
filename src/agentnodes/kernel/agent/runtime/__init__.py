"""A5 agent runtime seams: model_io / telemetry.

U6-2（2026-11-14）：``policy.py``（StopPolicy/StopDecision/limits_from_config）
随 legacy 循环体退役——其唯一消费链 ``_check_budget`` 只被 legacy 调用；
统一引擎的预算走 ``deciders.BudgetDecider``。
"""

from .model_io import ModelIO
from .telemetry import Telemetry

__all__ = [
    "ModelIO",
    "Telemetry",
]
