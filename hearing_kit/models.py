"""听证记录整理的数据模型。

本模块只描述实体与枚举，不含业务流程。所有听证内容均为合成材料；
本工具用于整理与追溯文本，不替代行政决策。
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import Optional


class Stance(str, Enum):
    """意见元素的立场类别。"""

    SUPPORT = "support"            # 支持
    OPPOSE = "oppose"              # 反对
    CONDITION = "condition"        # 附条件（认可目标但附带前提）
    REASON = "reason"              # 理由说明
    ALTERNATIVE = "alternative"    # 替代建议


STANCE_LABELS = {
    Stance.SUPPORT: "支持",
    Stance.OPPOSE: "反对",
    Stance.CONDITION: "附条件",
    Stance.REASON: "理由",
    Stance.ALTERNATIVE: "替代建议",
}


class ResponseKind(str, Enum):
    """部门回应的性质。"""

    EXPLANATION = "explanation"    # 当场解释说明
    DEFERRED = "deferred"          # 待研究（“带回去研究”“下次说明”），不是正式答复
    COMMITMENT = "commitment"      # 明确承诺


RESPONSE_KIND_LABELS = {
    ResponseKind.EXPLANATION: "解释说明",
    ResponseKind.DEFERRED: "待研究",
    ResponseKind.COMMITMENT: "承诺",
}

#: 回应结束并写入纪要时使用的正式标签（“待研究”结束后以“研究结论”公布）。
RESPONSE_FORMAL_LABELS = {
    ResponseKind.EXPLANATION: "解释说明",
    ResponseKind.DEFERRED: "研究结论",
    ResponseKind.COMMITMENT: "承诺",
}


class ResponseStatus(str, Enum):
    """部门回应的办理状态。"""

    OPEN = "open"                  # 尚未结束
    NEEDS_REVIEW = "needs_review"  # 对象被更正或撤回，需重新核对
    CLOSED = "closed"              # 已正式答复并结束


RESPONSE_STATUS_LABELS = {
    ResponseStatus.OPEN: "进行中",
    ResponseStatus.NEEDS_REVIEW: "待重新核对",
    ResponseStatus.CLOSED: "已结束",
}


class OpinionStatus(str, Enum):
    ACTIVE = "active"
    WITHDRAWN = "withdrawn"


class StatementKind(str, Enum):
    ORAL = "oral"        # 现场发言
    WRITTEN = "written"  # 书面意见
    AGENCY = "agency"    # 部门当场说明的原始记录
    NOTE = "note"        # 记录员备注（如更正说明）


@dataclass(frozen=True)
class ParagraphRef:
    """指向某份发言某一版本中的具体段落，作为意见或回应的依据段落。"""

    statement_id: str
    version: int
    paragraph: int  # 从 1 开始


@dataclass
class Plan:
    """一个被讨论的方案（拟议路线、备选路线等）。"""

    id: str
    title: str
    description: str = ""


@dataclass
class Speaker:
    id: str
    name: str
    role: str = "resident"  # resident / agency / staff


@dataclass
class StatementVersion:
    version: int
    paragraphs: tuple[str, ...]
    note: str = ""


@dataclass
class Statement:
    """一份发言或书面意见，保留全部版本；更正追加新版本而不改写旧版本。"""

    id: str
    speaker_id: str
    kind: StatementKind
    versions: list[StatementVersion] = field(default_factory=list)

    @property
    def current(self) -> StatementVersion:
        return self.versions[-1]


@dataclass
class OpinionRevision:
    version: int
    text: str
    note: str = ""


@dataclass
class Opinion:
    """一条结构化意见：立场 + 具体方案 + 提出者 + 依据段落。

    每位居民的每条意见都是独立记录；相似意见只能用 related_ids 互相
    参照，绝不自动合并为同一份联署意见。
    """

    id: str
    statement_id: str
    proposer_id: str
    plan_id: str
    stance: Stance
    basis: tuple[ParagraphRef, ...]
    revisions: list[OpinionRevision] = field(default_factory=list)
    status: OpinionStatus = OpinionStatus.ACTIVE
    withdrawn_reason: Optional[str] = None
    related_ids: list[str] = field(default_factory=list)

    @property
    def current_text(self) -> str:
        return self.revisions[-1].text

    @property
    def current_version(self) -> int:
        return self.revisions[-1].version


@dataclass
class ResponseEvent:
    at: str
    action: str  # created / flagged / rechecked / updated / closed
    note: str = ""


@dataclass
class Response:
    """部门回应。

    严格限定在单一方案内，只答复 opinion_ids 列出的意见；对某一备选
    方案的答复不会扩大到其他方案。
    """

    id: str
    plan_id: str
    opinion_ids: tuple[str, ...]
    kind: ResponseKind
    text: str
    unit: str  # 责任单位
    status: ResponseStatus = ResponseStatus.OPEN
    basis: tuple[ParagraphRef, ...] = ()
    history: list[ResponseEvent] = field(default_factory=list)

    @property
    def version(self) -> int:
        """文本版本：创建为第 1 版，每次结束时改写文本累加一版。"""
        return 1 + sum(1 for e in self.history if e.action == "updated")


# ---------------------------------------------------------------------------
# 承办人员视图
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class OpinionDigest:
    id: str
    stance: Stance
    text: str
    proposer: str
    status: OpinionStatus


@dataclass(frozen=True)
class PendingItem:
    """尚待答复/待办事项。"""

    kind: str  # unanswered_opinion / deferred_response / open_response / response_needs_review
    ref_id: str
    summary: str
    detail: str = ""


@dataclass(frozen=True)
class CommitmentItem:
    response_id: str
    unit: str
    text: str
    status: ResponseStatus
    opinion_ids: tuple[str, ...]


@dataclass(frozen=True)
class UnitDuty:
    unit: str
    response_id: str
    kind: ResponseKind
    status: ResponseStatus


@dataclass(frozen=True)
class BasisItem:
    """依据段落：意见或回应所依据的发言原文（按引用时的版本）。"""

    used_by: str
    ref: ParagraphRef
    text: str


@dataclass(frozen=True)
class PlanView:
    """按方案汇总的工作视图：待答复问题、部门承诺、责任单位、依据段落。"""

    plan_id: str
    plan_title: str
    opinions: tuple[OpinionDigest, ...]
    pending: tuple[PendingItem, ...]
    commitments: tuple[CommitmentItem, ...]
    unit_duties: tuple[UnitDuty, ...]
    basis: tuple[BasisItem, ...]


@dataclass(frozen=True)
class OpinionTrace:
    opinion: OpinionDigest
    response_ids: tuple[str, ...]


@dataclass(frozen=True)
class StatementTrace:
    """从原始发言追到结构化意见，再追到部门回应。"""

    statement_id: str
    speaker: str
    current_version: int
    current_text: str
    opinions: tuple[OpinionTrace, ...]
