import os
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from database import CorpusDB, DomainError


class ReworkFlowTest(unittest.TestCase):
    def setUp(self):
        fd, self.path = tempfile.mkstemp(suffix=".db")
        os.close(fd)
        self.db = CorpusDB(self.path)
        self.a1 = self.db.add_user("甲", "annotator")
        self.a2 = self.db.add_user("乙", "annotator")
        self.arb = self.db.add_user("仲裁", "arbitrator")
        self.mgr = self.db.add_user("管理", "manager")
        self.g = self.db.add_guideline("v1", "独立标注")
        self.batch = self.db.create_batch("返工批次", self.g)
        self.item = self.db.add_item(self.batch, 1, "这个版本很快。")
        self.item2 = self.db.add_item(self.batch, 2, "没有明显变化。")
        self.db.assign(self.item, self.a1)
        self.db.assign(self.item, self.a2)
        self.db.assign(self.item2, self.a1)
        self.db.assign(self.item2, self.a2)
        self.db.submit_annotation(self.item, self.a1, "中性", "初版")
        self.db.submit_annotation(self.item, self.a2, "中性")
        self.db.submit_annotation(self.item2, self.a1, "中性")
        self.db.submit_annotation(self.item2, self.a2, "中性")

    def tearDown(self):
        self.db.close()
        os.unlink(self.path)

    def test_rework_blocks_freeze_and_resolves_on_resubmit(self):
        # 只有管理员可以发起返工，且必须写明意见
        with self.assertRaisesRegex(DomainError, "管理员"):
            self.db.create_rework(self.item, self.a1, self.arb, "标签可疑")
        with self.assertRaisesRegex(DomainError, "意见不能为空"):
            self.db.create_rework(self.item, self.a1, self.mgr, "  ")
        rw = self.db.create_rework(self.item, self.a1, self.mgr, "此处实为正向表达，请复核")
        pending = self.db.list_reworks(status="pending")
        self.assertEqual(1, len(pending))
        self.assertEqual("中性", pending[0]["previous_label"])
        self.assertIsNone(pending[0]["completed_at"])
        # 待处理期间同一标注不能重复建单
        with self.assertRaisesRegex(DomainError, "待处理"):
            self.db.create_rework(self.item, self.a1, self.mgr, "再次抽检")
        # 存在待返工时批次不能冻结
        with self.assertRaisesRegex(DomainError, "待返工"):
            self.db.freeze_batch(self.batch, self.mgr)
        # 标注员重新提交后返工单结束
        new_id = self.db.submit_annotation(self.item, self.a1, "正向", "按返工意见修正")
        self.assertFalse(self.db.list_reworks(status="pending"))
        done = self.db.list_reworks(status="done")
        self.assertEqual(1, len(done))
        self.assertEqual("正向", done[0]["new_label"])
        self.assertEqual(new_id, done[0]["resolved_annotation_id"])
        self.assertIsNotNone(done[0]["completed_at"])
        # 已处理的标注可以再次发起返工
        rw2 = self.db.create_rework(self.item, self.a1, self.mgr, "再抽一次确认")
        self.db.submit_annotation(self.item, self.a1, "正向")
        self.assertEqual(2, len(self.db.list_reworks(status="done")))

    def test_freeze_and_export_keep_every_label_reason_and_time(self):
        self.db.create_rework(self.item, self.a1, self.mgr, "初版误标，需要改正")
        self.db.submit_annotation(self.item, self.a1, "正向", "返工后版本")
        # 返工重提后与另一标注员产生新分歧，需仲裁后才能冻结
        with self.assertRaisesRegex(DomainError, "分歧"):
            self.db.freeze_batch(self.batch, self.mgr)
        self.db.adjudicate(self.item, "正向", "返工后标签经仲裁确认正确", self.arb)
        result = self.db.freeze_batch(self.batch, self.mgr)
        self.assertIsNotNone(result["frozen_at"])
        exported = self.db.export_gold(self.batch)
        first = exported["records"][0]
        # 每次标签都保留：a1 的中性（旧版 superseded=1）与正向（当前），以及 a2 的中性
        a1_labels = [(a["label"], a["superseded"]) for a in first["annotations"] if a["annotator_id"] == self.a1]
        self.assertEqual([("中性", 1), ("正向", 0)], a1_labels)
        self.assertEqual(3, len(first["annotations"]))
        self.assertEqual(1, len(first["reworks"]))
        rw = first["reworks"][0]
        self.assertEqual("初版误标，需要改正", rw["reason"])
        self.assertEqual("中性", rw["previous_label"])
        self.assertEqual("正向", rw["new_label"])
        self.assertIsNotNone(rw["completed_at"])
        # 冻结后不能再发起返工或修改标注
        with self.assertRaisesRegex(DomainError, "冻结"):
            self.db.create_rework(self.item, self.a2, self.mgr, "抽检出问题")
        with self.assertRaisesRegex(DomainError, "冻结"):
            self.db.submit_annotation(self.item, self.a2, "负向")

    def test_only_submitted_annotation_can_get_rework_and_filters_work(self):
        # 未提交的标注不能发起返工
        a3 = self.db.add_user("丙3", "annotator")
        self.db.assign(self.item2, a3)
        with self.assertRaisesRegex(DomainError, "已提交"):
            self.db.create_rework(self.item2, a3, self.mgr, "还没提交呢")
        self.db.create_rework(self.item, self.a1, self.mgr, "意见甲")
        self.db.create_rework(self.item2, self.a1, self.mgr, "意见乙")
        self.db.submit_annotation(self.item, self.a1, "正向")
        self.assertEqual(1, len(self.db.list_reworks(status="pending")))
        self.assertEqual(1, len(self.db.list_reworks(status="done")))
        self.assertEqual(2, len(self.db.list_reworks()))
        self.assertEqual(2, len(self.db.list_reworks(batch_id=self.batch)))
        with self.assertRaisesRegex(DomainError, "pending"):
            self.db.list_reworks(status="closed")

    def test_resubmit_keeps_label_history_in_current_views(self):
        self.db.submit_annotation(self.item, self.a1, "负向")
        self.db.submit_annotation(self.item, self.a1, "中性")
        view = self.db.get_item_for_user(self.item, self.a1)
        self.assertEqual("中性", view["own_annotation"]["label"])
        # 被取代的旧标签（负向）不参与一致性与分歧计算，当前标签两人一致
        self.assertEqual([], self.db.disagreements(self.batch))
        metrics = self.db.consistency(self.batch)
        self.assertEqual(1.0, metrics["pairwise_agreement"])


if __name__ == "__main__":
    unittest.main()
