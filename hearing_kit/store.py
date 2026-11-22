"""听证记录的核心登记与查询逻辑。

不变量：
- 每条意见只属于一位提出者、一个方案，绝不自动合并相似意见；
- 部门回应只答复其 opinion_ids 列出的意见，且不得跨方案；
- 意见被更正或撤回时，未结束的回应一律标记为“待重新核对”；
- 已发布的纪要冻结当时的引用文本与版本，之后不再变化。
"""

from __future__ import annotations

import json
from datetime import date
from pathlib import Path
from typing import Callable, Optional, Sequence, Union

from .minutes import Minutes, MinutesItem
from .models import (
    RESPONSE_FORMAL_LABELS,
    RESPONSE_KIND_LABELS,
    STANCE_LABELS,
    BasisItem,
    CommitmentItem,
    Opinion,
    OpinionDigest,
    OpinionRevision,
    OpinionStatus,
    OpinionTrace,
    ParagraphRef,
    PendingItem,
    Plan,
    PlanView,
    Response,
    ResponseEvent,
    ResponseKind,
    ResponseStatus,
    Speaker,
    Statement,
    StatementKind,
    StatementTrace,
    StatementVersion,
    Stance,
    UnitDuty,
)


class HearingError(Exception):
    """听证记录操作的基础异常。"""


class NotFoundError(HearingError):
    """引用的对象不存在。"""


class PlanScopeError(HearingError):
    """回应试图跨方案答复，被拒绝。"""


class StateError(HearingError):
    """对象当前状态不允许该操作。"""


def _sort_key(rid: str) -> tuple[str, int]:
    prefix = "".join(ch for ch in rid if not ch.isdigit())
    num = "".join(ch for ch in rid if ch.isdigit())
    return (prefix, int(num) if num else 0)


def _normalize_paragraphs(paragraphs: Union[str, Sequence[str]]) -> tuple[str, ...]:
    if isinstance(paragraphs, str):
        paras = tuple(p.strip() for p in paragraphs.splitlines() if p.strip())
    else:
        paras = tuple(str(p).strip() for p in paragraphs if str(p).strip())
    if not paras:
        raise HearingError("发言内容不能为空")
    return paras


def _bigrams(text: str) -> set[str]:
    compact = "".join(text.split())
    if len(compact) < 2:
        return {compact} if compact else set()
    return {compact[i : i + 2] for i in range(len(compact) - 1)}


class HearingStore:
    """一场（或多场）听证的登记簿。"""

    def __init__(self, clock: Optional[Callable[[], str]] = None):
        self._clock = clock or (lambda: date.today().isoformat())
        self.plans: dict[str, Plan] = {}
        self.speakers: dict[str, Speaker] = {}
        self.statements: dict[str, Statement] = {}
        self.opinions: dict[str, Opinion] = {}
        self.responses: dict[str, Response] = {}
        self.minutes_book: dict[str, Minutes] = {}
        self._seq: dict[str, int] = {}

    # ------------------------------------------------------------------
    # 内部工具
    # ------------------------------------------------------------------

    def _next_id(self, kind: str, prefix: str) -> str:
        self._seq[kind] = self._seq.get(kind, 0) + 1
        return f"{prefix}{self._seq[kind]}"

    def _get(self, mapping: dict, key: str, what: str):
        try:
            return mapping[key]
        except KeyError:
            raise NotFoundError(f"{what}不存在：{key}") from None

    def _flag_open_responses(self, opinion_ids: Sequence[str], reason: str) -> None:
        """意见对象发生变化时，把尚未结束的回应标记为“待重新核对”。

        已结束的正式答复保持原样，由已发布纪要留存当时文本。
        """
        targets = set(opinion_ids)
        if not targets:
            return
        for resp in self.responses.values():
            if not targets.intersection(resp.opinion_ids):
                continue
            if resp.status is ResponseStatus.CLOSED:
                continue
            resp.status = ResponseStatus.NEEDS_REVIEW
            resp.history.append(ResponseEvent(self._clock(), "flagged", reason))

    # ------------------------------------------------------------------
    # 登记
    # ------------------------------------------------------------------

    def add_plan(self, title: str, description: str = "", plan_id: Optional[str] = None) -> Plan:
        pid = plan_id or self._next_id("plan", "P")
        if pid in self.plans:
            raise StateError(f"方案编号已存在：{pid}")
        plan = Plan(id=pid, title=title, description=description)
        self.plans[pid] = plan
        return plan

    def add_speaker(self, name: str, role: str = "resident", speaker_id: Optional[str] = None) -> Speaker:
        sid = speaker_id or self._next_id("speaker", "SP")
        if sid in self.speakers:
            raise StateError(f"发言人编号已存在：{sid}")
        speaker = Speaker(id=sid, name=name, role=role)
        self.speakers[sid] = speaker
        return speaker

    def add_statement(
        self,
        speaker_id: str,
        kind: Union[StatementKind, str],
        paragraphs: Union[str, Sequence[str]],
        note: str = "",
        statement_id: Optional[str] = None,
    ) -> Statement:
        self._get(self.speakers, speaker_id, "发言人")
        sid = statement_id or self._next_id("statement", "S")
        if sid in self.statements:
            raise StateError(f"发言编号已存在：{sid}")
        st = Statement(
            id=sid,
            speaker_id=speaker_id,
            kind=StatementKind(kind),
            versions=[StatementVersion(1, _normalize_paragraphs(paragraphs), note)],
        )
        self.statements[sid] = st
        return st

    def correct_statement(
        self,
        statement_id: str,
        paragraphs: Union[str, Sequence[str]],
        note: str = "",
    ) -> StatementVersion:
        """更正发言记录：追加新版本，未结束的回应需重新核对其对象。"""
        st = self._get(self.statements, statement_id, "发言")
        version = len(st.versions) + 1
        st.versions.append(StatementVersion(version, _normalize_paragraphs(paragraphs), note))
        affected = [o.id for o in self.opinions.values() if o.statement_id == statement_id]
        self._flag_open_responses(affected, f"发言 {statement_id} 已更正为第 {version} 版")
        return st.current

    def add_opinion(
        self,
        statement_id: str,
        proposer_id: str,
        plan_id: str,
        stance: Union[Stance, str],
        text: str,
        paragraphs: Sequence[int] = (1,),
        note: str = "",
        opinion_id: Optional[str] = None,
    ) -> Opinion:
        """从发言中登记一条结构化意见，关联到具体方案与提出者。

        ``paragraphs`` 是来源发言当前版本中的段落序号（从 1 开始），
        作为该意见的依据段落固定下来。
        """
        st = self._get(self.statements, statement_id, "发言")
        self._get(self.speakers, proposer_id, "提出者")
        self._get(self.plans, plan_id, "方案")
        if not text.strip():
            raise HearingError("意见内容不能为空")
        current = st.current
        refs: list[ParagraphRef] = []
        for p in paragraphs:
            if not 1 <= p <= len(current.paragraphs):
                raise HearingError(
                    f"发言 {statement_id} 第 {current.version} 版没有第 {p} 段"
                )
            ref = ParagraphRef(statement_id, current.version, p)
            if ref not in refs:
                refs.append(ref)
        oid = opinion_id or self._next_id("opinion", "O")
        if oid in self.opinions:
            raise StateError(f"意见编号已存在：{oid}")
        opinion = Opinion(
            id=oid,
            statement_id=statement_id,
            proposer_id=proposer_id,
            plan_id=plan_id,
            stance=Stance(stance),
            basis=tuple(refs),
            revisions=[OpinionRevision(1, text.strip(), note)],
        )
        self.opinions[oid] = opinion
        return opinion

    def correct_opinion(self, opinion_id: str, text: str, note: str = "") -> OpinionRevision:
        """更正意见文本：追加新版本，未结束的回应需重新核对。"""
        opinion = self._get(self.opinions, opinion_id, "意见")
        if opinion.status is OpinionStatus.WITHDRAWN:
            raise StateError(f"意见 {opinion_id} 已撤回，不能再更正")
        if not text.strip():
            raise HearingError("意见内容不能为空")
        revision = OpinionRevision(opinion.current_version + 1, text.strip(), note)
        opinion.revisions.append(revision)
        self._flag_open_responses([opinion_id], f"意见 {opinion_id} 已更正为第 {revision.version} 版")
        return revision

    def withdraw_opinion(self, opinion_id: str, reason: str) -> Opinion:
        """居民撤回某项意见：未结束的回应需重新核对其对象。"""
        opinion = self._get(self.opinions, opinion_id, "意见")
        if opinion.status is OpinionStatus.WITHDRAWN:
            raise StateError(f"意见 {opinion_id} 已经撤回")
        opinion.status = OpinionStatus.WITHDRAWN
        opinion.withdrawn_reason = reason
        self._flag_open_responses([opinion_id], f"意见 {opinion_id} 已撤回：{reason}")
        return opinion

    def mark_related(self, opinion_id_a: str, opinion_id_b: str) -> None:
        """把两条相似意见互相标记为“相关”，仅供参照，绝不合并。"""
        a = self._get(self.opinions, opinion_id_a, "意见")
        b = self._get(self.opinions, opinion_id_b, "意见")
        if a.id == b.id:
            raise HearingError("不能把意见与自身标记为相关")
        if b.id not in a.related_ids:
            a.related_ids.append(b.id)
        if a.id not in b.related_ids:
            b.related_ids.append(a.id)

    def suggest_related(
        self,
        text: str,
        plan_id: Optional[str] = None,
        threshold: float = 0.6,
    ) -> list[tuple[str, float]]:
        """按字二元组 Jaccard 相似度提示可能相关的意见。

        仅供承办人员人工核对，工具不会据此合并任何意见。
        """
        target = _bigrams(text)
        if not target:
            return []
        scored: list[tuple[str, float]] = []
        for opinion in self.opinions.values():
            if opinion.status is not OpinionStatus.ACTIVE:
                continue
            if plan_id is not None and opinion.plan_id != plan_id:
                continue
            other = _bigrams(opinion.current_text)
            if not other:
                continue
            score = len(target & other) / len(target | other)
            if score >= threshold:
                scored.append((opinion.id, round(score, 3)))
        scored.sort(key=lambda item: (-item[1], _sort_key(item[0])))
        return scored

    # ------------------------------------------------------------------
    # 部门回应
    # ------------------------------------------------------------------

    def add_response(
        self,
        plan_id: str,
        opinion_ids: Sequence[str],
        kind: Union[ResponseKind, str],
        text: str,
        unit: str,
        basis: Sequence[ParagraphRef] = (),
        response_id: Optional[str] = None,
    ) -> Response:
        """登记部门回应。

        回应严格限定在 ``plan_id`` 方案内，只答复 ``opinion_ids`` 列出的
        意见；对某一备选方案的答复不能扩大到其他方案。``opinion_ids``
        为空时表示该回应是对方案的整体说明，不视为答复任何具体意见。
        """
        self._get(self.plans, plan_id, "方案")
        if not unit.strip():
            raise HearingError("必须填写责任单位")
        if not text.strip():
            raise HearingError("回应内容不能为空")
        opinions: list[Opinion] = []
        for oid in opinion_ids:
            opinion = self._get(self.opinions, oid, "意见")
            if opinion.plan_id != plan_id:
                raise PlanScopeError(
                    f"回应不得跨方案：意见 {oid} 属于方案 {opinion.plan_id}，"
                    f"不能并入方案 {plan_id} 的答复"
                )
            if opinion.status is OpinionStatus.WITHDRAWN:
                raise StateError(f"意见 {oid} 已撤回，需先核对再决定是否答复")
            opinions.append(opinion)
        refs = list(basis)
        if not refs:
            seen: list[ParagraphRef] = []
            for opinion in opinions:
                for ref in opinion.basis:
                    if ref not in seen:
                        seen.append(ref)
            refs = seen
        else:
            for ref in refs:
                self._get(self.statements, ref.statement_id, "发言")
        rid = response_id or self._next_id("response", "R")
        if rid in self.responses:
            raise StateError(f"回应编号已存在：{rid}")
        response = Response(
            id=rid,
            plan_id=plan_id,
            opinion_ids=tuple(opinion_ids),
            kind=ResponseKind(kind),
            text=text.strip(),
            unit=unit.strip(),
            basis=tuple(refs),
            history=[ResponseEvent(self._clock(), "created", "")],
        )
        self.responses[rid] = response
        return response

    def recheck_response(self, response_id: str, note: str) -> Response:
        """对象被更正或撤回后，承办人员重新核对回应对象。"""
        response = self._get(self.responses, response_id, "回应")
        if response.status is not ResponseStatus.NEEDS_REVIEW:
            raise StateError(f"回应 {response_id} 当前无需重新核对")
        response.status = ResponseStatus.OPEN
        response.history.append(ResponseEvent(self._clock(), "rechecked", note))
        return response

    def close_response(
        self,
        response_id: str,
        final_text: Optional[str] = None,
        note: str = "",
    ) -> Response:
        """结束回应，形成正式答复；可在结束时改写为最终文本。"""
        response = self._get(self.responses, response_id, "回应")
        if response.status is ResponseStatus.CLOSED:
            raise StateError(f"回应 {response_id} 已结束")
        if response.status is ResponseStatus.NEEDS_REVIEW:
            raise StateError(
                f"回应 {response_id} 的对象曾被更正或撤回，请先 recheck_response 重新核对"
            )
        if final_text is not None:
            if not final_text.strip():
                raise HearingError("最终文本不能为空")
            response.text = final_text.strip()
            response.history.append(ResponseEvent(self._clock(), "updated", "结束时定稿"))
        response.status = ResponseStatus.CLOSED
        response.history.append(ResponseEvent(self._clock(), "closed", note))
        return response

    # ------------------------------------------------------------------
    # 纪要
    # ------------------------------------------------------------------

    def publish_minutes(
        self,
        title: str,
        published_at: Optional[str] = None,
        plan_ids: Optional[Sequence[str]] = None,
    ) -> Minutes:
        """发布听证纪要：冻结当时引用的文本与版本，之后不再变化。

        纪要按类别标明哪些内容属于居民意见、哪些属于部门正式答复；
        待研究或待核对的事项单列为“非正式答复”。
        """
        pids = list(plan_ids) if plan_ids else sorted(self.plans, key=_sort_key)
        items: list[MinutesItem] = []
        for pid in pids:
            self._get(self.plans, pid, "方案")
            opinions = sorted(
                (o for o in self.opinions.values() if o.plan_id == pid),
                key=lambda o: _sort_key(o.id),
            )
            for opinion in opinions:
                if opinion.status is OpinionStatus.WITHDRAWN:
                    continue
                items.append(
                    MinutesItem(
                        category="opinion",
                        label=f"居民意见（{STANCE_LABELS[opinion.stance]}）",
                        ref_id=opinion.id,
                        plan_id=pid,
                        quoted_text=opinion.current_text,
                        quoted_version=opinion.current_version,
                        source=self._format_basis(opinion.basis),
                        proposer=self.speakers[opinion.proposer_id].name,
                    )
                )
            responses = sorted(
                (r for r in self.responses.values() if r.plan_id == pid),
                key=lambda r: _sort_key(r.id),
            )
            for response in responses:
                if response.status is ResponseStatus.CLOSED:
                    category = "formal_response"
                    label = f"部门正式答复（{RESPONSE_FORMAL_LABELS[response.kind]}）"
                else:
                    category = "open_note"
                    if response.status is ResponseStatus.NEEDS_REVIEW:
                        label = "待核对事项（意见已更正或撤回，非正式答复）"
                    elif response.kind is ResponseKind.DEFERRED:
                        label = "待研究事项（非正式答复）"
                    else:
                        label = "进行中的回应（非正式答复）"
                items.append(
                    MinutesItem(
                        category=category,
                        label=label,
                        ref_id=response.id,
                        plan_id=pid,
                        quoted_text=response.text,
                        quoted_version=response.version,
                        source=f"回应 {response.id}（责任单位：{response.unit}）",
                        unit=response.unit,
                    )
                )
        mid = self._next_id("minutes", "M")
        minutes = Minutes(
            id=mid,
            title=title,
            published_at=published_at or self._clock(),
            items=tuple(items),
        )
        self.minutes_book[mid] = minutes
        return minutes

    def get_minutes(self, minutes_id: str) -> Minutes:
        return self._get(self.minutes_book, minutes_id, "纪要")

    # ------------------------------------------------------------------
    # 查询
    # ------------------------------------------------------------------

    def resolve_ref(self, ref: ParagraphRef) -> str:
        """按引用时的版本取回依据段落原文。"""
        st = self._get(self.statements, ref.statement_id, "发言")
        for version in st.versions:
            if version.version == ref.version:
                if 1 <= ref.paragraph <= len(version.paragraphs):
                    return version.paragraphs[ref.paragraph - 1]
                return "（该版本无此段落）"
        return "（无此版本）"

    def plan_view(self, plan_id: str) -> PlanView:
        """按方案汇总：尚待答复的问题、部门承诺、责任单位与依据段落。"""
        plan = self._get(self.plans, plan_id, "方案")
        opinions = sorted(
            (o for o in self.opinions.values() if o.plan_id == plan_id),
            key=lambda o: _sort_key(o.id),
        )
        responses = sorted(
            (r for r in self.responses.values() if r.plan_id == plan_id),
            key=lambda r: _sort_key(r.id),
        )
        closed_cover: dict[str, list[str]] = {}
        open_cover: dict[str, list[str]] = {}
        for response in responses:
            cover = closed_cover if response.status is ResponseStatus.CLOSED else open_cover
            for oid in response.opinion_ids:
                cover.setdefault(oid, []).append(response.id)

        pending: list[PendingItem] = []
        for opinion in opinions:
            if opinion.status is OpinionStatus.WITHDRAWN:
                continue
            if opinion.id in closed_cover:
                continue
            proposer = self.speakers[opinion.proposer_id].name
            detail = f"{STANCE_LABELS[opinion.stance]}｜提出者 {proposer}"
            if opinion.id in open_cover:
                detail += "｜已有进行中的回应：" + "、".join(open_cover[opinion.id])
            pending.append(
                PendingItem("unanswered_opinion", opinion.id, opinion.current_text, detail)
            )
        for response in responses:
            if response.status is ResponseStatus.CLOSED:
                continue
            if response.status is ResponseStatus.NEEDS_REVIEW:
                pending.append(
                    PendingItem(
                        "response_needs_review",
                        response.id,
                        f"回应 {response.id} 的对象已更正或撤回，需重新核对",
                        f"责任单位 {response.unit}",
                    )
                )
            elif response.kind is ResponseKind.DEFERRED:
                pending.append(
                    PendingItem(
                        "deferred_response",
                        response.id,
                        f"部门待研究：{response.text}",
                        f"责任单位 {response.unit}",
                    )
                )
            else:
                pending.append(
                    PendingItem(
                        "open_response",
                        response.id,
                        f"进行中的回应：{response.text}",
                        f"责任单位 {response.unit}",
                    )
                )

        commitments = tuple(
            CommitmentItem(r.id, r.unit, r.text, r.status, r.opinion_ids)
            for r in responses
            if r.kind is ResponseKind.COMMITMENT
        )
        unit_duties = tuple(
            UnitDuty(r.unit, r.id, r.kind, r.status) for r in responses
        )
        basis_items: list[BasisItem] = []
        for opinion in opinions:
            if opinion.status is OpinionStatus.WITHDRAWN:
                continue
            for ref in opinion.basis:
                basis_items.append(BasisItem(opinion.id, ref, self.resolve_ref(ref)))
        for response in responses:
            for ref in response.basis:
                basis_items.append(BasisItem(response.id, ref, self.resolve_ref(ref)))

        digests = tuple(
            OpinionDigest(
                o.id,
                o.stance,
                o.current_text,
                self.speakers[o.proposer_id].name,
                o.status,
            )
            for o in opinions
        )
        return PlanView(
            plan_id=plan_id,
            plan_title=plan.title,
            opinions=digests,
            pending=tuple(pending),
            commitments=commitments,
            unit_duties=unit_duties,
            basis=tuple(basis_items),
        )

    def statement_trace(self, statement_id: str) -> StatementTrace:
        """从原始发言追到结构化意见，再追到部门回应。"""
        st = self._get(self.statements, statement_id, "发言")
        traces: list[OpinionTrace] = []
        opinions = sorted(
            (o for o in self.opinions.values() if o.statement_id == statement_id),
            key=lambda o: _sort_key(o.id),
        )
        for opinion in opinions:
            response_ids = tuple(
                r.id
                for r in sorted(self.responses.values(), key=lambda r: _sort_key(r.id))
                if opinion.id in r.opinion_ids
            )
            digest = OpinionDigest(
                opinion.id,
                opinion.stance,
                opinion.current_text,
                self.speakers[opinion.proposer_id].name,
                opinion.status,
            )
            traces.append(OpinionTrace(digest, response_ids))
        return StatementTrace(
            statement_id=statement_id,
            speaker=self.speakers[st.speaker_id].name,
            current_version=st.current.version,
            current_text="\n".join(st.current.paragraphs),
            opinions=tuple(traces),
        )

    def _format_basis(self, refs: Sequence[ParagraphRef]) -> str:
        if not refs:
            return "（无依据段落）"
        return "；".join(
            f"发言 {ref.statement_id} 第{ref.version}版 第{ref.paragraph}段" for ref in refs
        )

    # ------------------------------------------------------------------
    # 序列化
    # ------------------------------------------------------------------

    def to_dict(self) -> dict:
        return {
            "seq": dict(self._seq),
            "plans": [
                {"id": p.id, "title": p.title, "description": p.description}
                for p in self.plans.values()
            ],
            "speakers": [
                {"id": s.id, "name": s.name, "role": s.role}
                for s in self.speakers.values()
            ],
            "statements": [
                {
                    "id": st.id,
                    "speaker_id": st.speaker_id,
                    "kind": st.kind.value,
                    "versions": [
                        {"version": v.version, "paragraphs": list(v.paragraphs), "note": v.note}
                        for v in st.versions
                    ],
                }
                for st in self.statements.values()
            ],
            "opinions": [
                {
                    "id": o.id,
                    "statement_id": o.statement_id,
                    "proposer_id": o.proposer_id,
                    "plan_id": o.plan_id,
                    "stance": o.stance.value,
                    "basis": [
                        {
                            "statement_id": ref.statement_id,
                            "version": ref.version,
                            "paragraph": ref.paragraph,
                        }
                        for ref in o.basis
                    ],
                    "revisions": [
                        {"version": r.version, "text": r.text, "note": r.note}
                        for r in o.revisions
                    ],
                    "status": o.status.value,
                    "withdrawn_reason": o.withdrawn_reason,
                    "related_ids": list(o.related_ids),
                }
                for o in self.opinions.values()
            ],
            "responses": [
                {
                    "id": r.id,
                    "plan_id": r.plan_id,
                    "opinion_ids": list(r.opinion_ids),
                    "kind": r.kind.value,
                    "text": r.text,
                    "unit": r.unit,
                    "status": r.status.value,
                    "basis": [
                        {
                            "statement_id": ref.statement_id,
                            "version": ref.version,
                            "paragraph": ref.paragraph,
                        }
                        for ref in r.basis
                    ],
                    "history": [
                        {"at": e.at, "action": e.action, "note": e.note}
                        for e in r.history
                    ],
                }
                for r in self.responses.values()
            ],
            "minutes": [
                {
                    "id": m.id,
                    "title": m.title,
                    "published_at": m.published_at,
                    "items": [
                        {
                            "category": i.category,
                            "label": i.label,
                            "ref_id": i.ref_id,
                            "plan_id": i.plan_id,
                            "quoted_text": i.quoted_text,
                            "quoted_version": i.quoted_version,
                            "source": i.source,
                            "proposer": i.proposer,
                            "unit": i.unit,
                        }
                        for i in m.items
                    ],
                }
                for m in self.minutes_book.values()
            ],
        }

    @classmethod
    def from_dict(cls, data: dict) -> "HearingStore":
        store = cls()
        store._seq = dict(data.get("seq", {}))
        for p in data.get("plans", []):
            store.plans[p["id"]] = Plan(id=p["id"], title=p["title"], description=p.get("description", ""))
        for s in data.get("speakers", []):
            store.speakers[s["id"]] = Speaker(id=s["id"], name=s["name"], role=s.get("role", "resident"))
        for s in data.get("statements", []):
            store.statements[s["id"]] = Statement(
                id=s["id"],
                speaker_id=s["speaker_id"],
                kind=StatementKind(s["kind"]),
                versions=[
                    StatementVersion(v["version"], tuple(v["paragraphs"]), v.get("note", ""))
                    for v in s["versions"]
                ],
            )
        for o in data.get("opinions", []):
            store.opinions[o["id"]] = Opinion(
                id=o["id"],
                statement_id=o["statement_id"],
                proposer_id=o["proposer_id"],
                plan_id=o["plan_id"],
                stance=Stance(o["stance"]),
                basis=tuple(ParagraphRef(**ref) for ref in o.get("basis", [])),
                revisions=[
                    OpinionRevision(r["version"], r["text"], r.get("note", ""))
                    for r in o.get("revisions", [])
                ],
                status=OpinionStatus(o.get("status", "active")),
                withdrawn_reason=o.get("withdrawn_reason"),
                related_ids=list(o.get("related_ids", [])),
            )
        for r in data.get("responses", []):
            store.responses[r["id"]] = Response(
                id=r["id"],
                plan_id=r["plan_id"],
                opinion_ids=tuple(r.get("opinion_ids", [])),
                kind=ResponseKind(r["kind"]),
                text=r["text"],
                unit=r["unit"],
                status=ResponseStatus(r.get("status", "open")),
                basis=tuple(ParagraphRef(**ref) for ref in r.get("basis", [])),
                history=[
                    ResponseEvent(e["at"], e["action"], e.get("note", ""))
                    for e in r.get("history", [])
                ],
            )
        for m in data.get("minutes", []):
            store.minutes_book[m["id"]] = Minutes(
                id=m["id"],
                title=m["title"],
                published_at=m["published_at"],
                items=tuple(MinutesItem(**item) for item in m.get("items", [])),
            )
        return store

    def dumps(self, **kwargs) -> str:
        kwargs.setdefault("ensure_ascii", False)
        kwargs.setdefault("indent", 2)
        return json.dumps(self.to_dict(), **kwargs)

    @classmethod
    def loads(cls, payload: str) -> "HearingStore":
        return cls.from_dict(json.loads(payload))

    def save(self, path: Union[str, Path]) -> None:
        Path(path).write_text(self.dumps(), encoding="utf-8")

    @classmethod
    def load(cls, path: Union[str, Path]) -> "HearingStore":
        return cls.loads(Path(path).read_text(encoding="utf-8"))
