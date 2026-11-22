"""公共规划听证记录整理工具。

从原始发言与书面意见出发，把支持、反对、条件、理由与替代建议分别
关联到具体方案及提出者；区分部门的解释、待研究事项与明确承诺；
支持更正、撤回与纪要发布的版本追踪。所有听证内容均为合成材料，
本工具不替代行政决策。
"""

from .minutes import Minutes, MinutesItem
from .models import (
    RESPONSE_FORMAL_LABELS,
    RESPONSE_KIND_LABELS,
    RESPONSE_STATUS_LABELS,
    STANCE_LABELS,
    BasisItem,
    CommitmentItem,
    Opinion,
    OpinionDigest,
    OpinionStatus,
    OpinionTrace,
    ParagraphRef,
    PendingItem,
    Plan,
    PlanView,
    Response,
    ResponseKind,
    ResponseStatus,
    Speaker,
    Statement,
    StatementKind,
    StatementTrace,
    Stance,
    UnitDuty,
)
from .sources import load_source_records
from .store import (
    HearingError,
    HearingStore,
    NotFoundError,
    PlanScopeError,
    StateError,
)

__all__ = [
    "BasisItem",
    "CommitmentItem",
    "HearingError",
    "HearingStore",
    "Minutes",
    "MinutesItem",
    "NotFoundError",
    "Opinion",
    "OpinionDigest",
    "OpinionStatus",
    "OpinionTrace",
    "ParagraphRef",
    "PendingItem",
    "Plan",
    "PlanScopeError",
    "PlanView",
    "RESPONSE_FORMAL_LABELS",
    "RESPONSE_KIND_LABELS",
    "RESPONSE_STATUS_LABELS",
    "Response",
    "ResponseKind",
    "ResponseStatus",
    "STANCE_LABELS",
    "Speaker",
    "StateError",
    "Statement",
    "StatementKind",
    "StatementTrace",
    "Stance",
    "UnitDuty",
    "load_source_records",
]
