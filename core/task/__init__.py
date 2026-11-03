"""任务与证据：任务状态、完成守卫、进展检测与轮次记录。"""

from .progress import ArtifactDecision, ArtifactProgress, ProgressDecision, ProgressMonitor
from .rounds import ROUND_SCHEMA_VERSION, build_round_record
from .state import (
    CompletionDecision,
    TaskState,
    classify_task,
    completion_decision,
    delivery_receipt,
)

__all__ = [
    "ArtifactDecision",
    "ArtifactProgress",
    "CompletionDecision",
    "ProgressDecision",
    "ProgressMonitor",
    "ROUND_SCHEMA_VERSION",
    "TaskState",
    "build_round_record",
    "classify_task",
    "completion_decision",
    "delivery_receipt",
]
