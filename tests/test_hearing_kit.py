"""hearing_kit 的行为测试：覆盖任务要求的各项不变量。"""

import unittest

from hearing_kit import (
    HearingStore,
    OpinionStatus,
    PlanScopeError,
    ResponseKind,
    ResponseStatus,
    Stance,
    StateError,
    load_source_records,
)
from pathlib import Path

DATA = Path(__file__).parents[1] / "data" / "hearings.json"


def make_store() -> HearingStore:
    """搭好一场合成听证：两个方案、两位居民、一个部门。"""
    store = HearingStore(clock=lambda: "2026-11-22")
    store.add_plan("方案甲：滨河步道贴近学校围墙的拟议路线", plan_id="P1")
    store.add_plan("方案乙：绕行绿地的替代路线", plan_id="P2")
    store.add_speaker("林女士", speaker_id="SP1")
    store.add_speaker("陈先生", speaker_id="SP2")
    store.add_speaker("市交通局代表", role="agency", speaker_id="SP9")
    store.add_statement(
        "SP1",
        "oral",
        ["支持改善河岸通行，但不赞成方案甲把步道贴近学校围墙。", "建议比较方案乙。"],
        statement_id="S1",
    )
    store.add_statement("SP2", "written", "我也建议改走方案乙，避开学校。", statement_id="S2")
    return store


class OpinionLinkTests(unittest.TestCase):
    def test_stances_link_to_plan_and_proposer(self):
        store = make_store()
        o1 = store.add_opinion("S1", "SP1", "P1", Stance.CONDITION, "认可改善目标，但反对步道贴近学校围墙", paragraphs=(1,))
        o2 = store.add_opinion("S1", "SP1", "P2", Stance.ALTERNATIVE, "建议比较方案乙绕行绿地路线", paragraphs=(2,))
        self.assertEqual(o1.plan_id, "P1")
        self.assertEqual(o2.plan_id, "P2")
        self.assertEqual(o1.proposer_id, "SP1")
        self.assertEqual(o1.stance, Stance.CONDITION)
        # 依据段落固定到登记时的发言版本
        self.assertEqual(o1.basis[0].statement_id, "S1")
        self.assertEqual(o1.basis[0].version, 1)
        self.assertEqual(o1.basis[0].paragraph, 1)

    def test_statement_trace_reaches_responses(self):
        store = make_store()
        o1 = store.add_opinion("S1", "SP1", "P1", "condition", "反对贴近围墙", paragraphs=(1,))
        store.add_response("P1", [o1.id], "deferred", "围墙段影响带回去研究", "市交通局")
        trace = store.statement_trace("S1")
        self.assertEqual(trace.speaker, "林女士")
        self.assertEqual(trace.opinions[0].opinion.id, o1.id)
        self.assertEqual(trace.opinions[0].response_ids, ("R1",))


class DeferredResponseTests(unittest.TestCase):
    def test_deferred_is_not_a_formal_answer(self):
        store = make_store()
        o2 = store.add_opinion("S1", "SP1", "P2", "alternative", "建议比较方案乙", paragraphs=(2,))
        store.add_response("P2", [o2.id], ResponseKind.DEFERRED, "方案乙接驳影响会在下次说明", "市交通局")
        view = store.plan_view("P2")
        kinds = {item.kind for item in view.pending}
        # “下次说明”仍是待办：意见未获正式答复，且有待研究事项
        self.assertIn("unanswered_opinion", kinds)
        self.assertIn("deferred_response", kinds)
        minutes = store.publish_minutes("第一次听证纪要")
        self.assertEqual(minutes.formal_responses(), ())
        self.assertEqual(len(minutes.open_notes()), 1)
        self.assertIn("非正式答复", minutes.open_notes()[0].label)

    def test_commitment_listed_with_unit(self):
        store = make_store()
        o1 = store.add_opinion("S1", "SP1", "P1", "oppose", "反对夜间施工", paragraphs=(1,))
        store.add_response("P1", [o1.id], ResponseKind.COMMITMENT, "承诺夜间十点后停止施工", "市工程处")
        view = store.plan_view("P1")
        self.assertEqual(len(view.commitments), 1)
        self.assertEqual(view.commitments[0].unit, "市工程处")
        self.assertIn(("市工程处", "R1", ResponseKind.COMMITMENT, ResponseStatus.OPEN),
                      [(d.unit, d.response_id, d.kind, d.status) for d in view.unit_duties])


class CorrectionAndWithdrawalTests(unittest.TestCase):
    def test_correction_flags_open_response_and_requires_recheck(self):
        store = make_store()
        o1 = store.add_opinion("S1", "SP1", "P1", "condition", "反对贴近围墙", paragraphs=(1,))
        store.add_response("P1", [o1.id], "deferred", "带回去研究", "市交通局")
        store.correct_statement("S1", ["支持改善河岸通行，但反对的是夜间施工安排。", "建议比较方案乙。"], note="发言人更正")
        response = store.responses["R1"]
        self.assertEqual(response.status, ResponseStatus.NEEDS_REVIEW)
        # 未核对前不能结束
        with self.assertRaises(StateError):
            store.close_response("R1")
        store.recheck_response("R1", "已核对：更正后意见对象不变")
        store.close_response("R1", final_text="研究结论：夜间十点后停止施工")
        self.assertEqual(store.responses["R1"].status, ResponseStatus.CLOSED)
        # 已结束的答复不再受后续更正影响
        store.correct_opinion(o1.id, "反对夜间施工与围墙安全距离", note="补充")
        self.assertEqual(store.responses["R1"].status, ResponseStatus.CLOSED)

    def test_withdrawal_flags_response_and_blocks_new_response(self):
        store = make_store()
        o2 = store.add_opinion("S1", "SP1", "P2", "alternative", "建议比较方案乙", paragraphs=(2,))
        store.add_response("P2", [o2.id], "deferred", "接驳影响下次说明", "市交通局")
        store.withdraw_opinion(o2.id, "发言人撤回对方案乙的支持理由")
        self.assertEqual(store.responses["R1"].status, ResponseStatus.NEEDS_REVIEW)
        with self.assertRaises(StateError):
            store.add_response("P2", [o2.id], "explanation", "补充说明", "市交通局")
        # 撤回的意见不再出现在待答复清单与纪要中
        view = store.plan_view("P2")
        self.assertNotIn(o2.id, {i.ref_id for i in view.pending if i.kind == "unanswered_opinion"})
        minutes = store.publish_minutes("纪要")
        self.assertNotIn(o2.id, {i.ref_id for i in minutes.opinions()})

    def test_published_minutes_freeze_quoted_text_and_version(self):
        store = make_store()
        o1 = store.add_opinion("S1", "SP1", "P1", "condition", "反对贴近围墙", paragraphs=(1,))
        minutes = store.publish_minutes("第一次纪要", published_at="2026-11-22")
        quoted = {i.ref_id: i for i in minutes.opinions()}[o1.id]
        self.assertEqual(quoted.quoted_text, "反对贴近围墙")
        self.assertEqual(quoted.quoted_version, 1)
        # 发布后更正意见与发言，已发布纪要保留当时文本与版本
        store.correct_opinion(o1.id, "反对夜间施工安排", note="发言人更正")
        store.correct_statement("S1", ["更正后的发言。"])
        kept = store.get_minutes(minutes.id)
        kept_item = {i.ref_id: i for i in kept.opinions()}[o1.id]
        self.assertEqual(kept_item.quoted_text, "反对贴近围墙")
        self.assertEqual(kept_item.quoted_version, 1)
        # 新发布的纪要引用新版本
        new_minutes = store.publish_minutes("第二次纪要")
        new_item = {i.ref_id: i for i in new_minutes.opinions()}[o1.id]
        self.assertEqual(new_item.quoted_text, "反对夜间施工安排")
        self.assertEqual(new_item.quoted_version, 2)


class NoMergeAndScopeTests(unittest.TestCase):
    def test_similar_opinions_stay_separate(self):
        store = make_store()
        o_lin = store.add_opinion("S1", "SP1", "P2", "alternative", "建议比较方案乙", paragraphs=(2,))
        o_chen = store.add_opinion("S2", "SP2", "P2", "alternative", "我也建议改走方案乙，避开学校", paragraphs=(1,))
        store.mark_related(o_lin.id, o_chen.id)
        # 只互相参照，不合并：两条记录各自独立、提出者不同
        self.assertIn(o_chen.id, store.opinions[o_lin.id].related_ids)
        self.assertEqual(len([o for o in store.opinions.values() if o.plan_id == "P2"]), 2)
        # 部门只答复林女士的建议，陈先生的相似建议仍未答复
        store.add_response("P2", [o_lin.id], "commitment", "下一轮提供方案乙比较表", "市交通局")
        store.close_response("R1")
        view = store.plan_view("P2")
        unanswered = {i.ref_id for i in view.pending if i.kind == "unanswered_opinion"}
        self.assertIn(o_chen.id, unanswered)
        self.assertNotIn(o_lin.id, unanswered)
        # 纪要中两条意见分别列出，不会并成联署意见
        minutes = store.publish_minutes("纪要")
        proposers = {i.proposer for i in minutes.opinions() if i.plan_id == "P2"}
        self.assertEqual(proposers, {"林女士", "陈先生"})

    def test_response_cannot_cross_plans(self):
        store = make_store()
        o1 = store.add_opinion("S1", "SP1", "P1", "condition", "反对贴近围墙", paragraphs=(1,))
        with self.assertRaises(PlanScopeError):
            store.add_response("P2", [o1.id], "explanation", "试图并到方案乙答复", "市交通局")
        # 对方案甲的答复不会出现在方案乙的视图里
        store.add_response("P1", [o1.id], "commitment", "承诺夜间停工", "市工程处")
        store.close_response("R1")
        view_b = store.plan_view("P2")
        self.assertEqual(view_b.commitments, ())
        self.assertEqual(view_b.unit_duties, ())
        minutes = store.publish_minutes("纪要", plan_ids=["P2"])
        self.assertEqual(minutes.formal_responses(), ())


class PlanViewAndPersistenceTests(unittest.TestCase):
    def test_plan_view_basis_resolves_quoted_version(self):
        store = make_store()
        o1 = store.add_opinion("S1", "SP1", "P1", "condition", "反对贴近围墙", paragraphs=(1,))
        store.add_response("P1", [o1.id], "deferred", "带回去研究", "市交通局")
        store.correct_statement("S1", ["更正后的第一段。", "建议比较方案乙。"])
        view = store.plan_view("P1")
        basis = {b.used_by: b for b in view.basis}
        # 依据段落仍取登记时引用的第 1 版原文
        self.assertEqual(basis[o1.id].text, "支持改善河岸通行，但不赞成方案甲把步道贴近学校围墙。")
        self.assertEqual(basis[o1.id].ref.version, 1)

    def test_json_round_trip(self):
        store = make_store()
        o1 = store.add_opinion("S1", "SP1", "P1", "condition", "反对贴近围墙", paragraphs=(1,))
        store.add_response("P1", [o1.id], "commitment", "承诺夜间停工", "市工程处")
        store.publish_minutes("纪要")
        payload = store.dumps()
        restored = HearingStore.loads(payload)
        self.assertEqual(restored.dumps(), payload)
        view = restored.plan_view("P1")
        self.assertEqual(len(view.commitments), 1)
        self.assertEqual(view.basis[0].text, "支持改善河岸通行，但不赞成方案甲把步道贴近学校围墙。")
        self.assertEqual(len(restored.get_minutes("M1").opinions()), 1)


class SourceLoaderTests(unittest.TestCase):
    def test_load_synthetic_source_records(self):
        store = HearingStore(clock=lambda: "2026-11-22")
        mapping = load_source_records(DATA, store)
        self.assertEqual(len(mapping), 4)
        self.assertEqual(store.statements[mapping["hearing-1"]].kind.value, "oral")
        self.assertEqual(store.statements[mapping["hearing-2"]].kind.value, "agency")
        self.assertEqual(store.statements[mapping["hearing-3"]].kind.value, "written")
        self.assertEqual(store.statements[mapping["hearing-4"]].kind.value, "note")
        # 导入后可在原始发言上登记意见并追溯
        store.add_plan("方案甲", plan_id="P1")
        opinion = store.add_opinion(mapping["hearing-1"], "src-hearing-1", "P1", "condition", "支持目标但反对贴近围墙", paragraphs=(1,))
        self.assertEqual(store.resolve_ref(opinion.basis[0]), "支持改善河岸通行，但不赞成方案甲把步道贴近学校围墙；建议比较方案乙。")


if __name__ == "__main__":
    unittest.main()
