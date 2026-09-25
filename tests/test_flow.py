import json
import os
import sys
import tempfile
import threading
import unittest
import urllib.request
from http.server import ThreadingHTTPServer
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import app as app_module
from database import CorpusDB, DomainError


class CorpusFlowTest(unittest.TestCase):
    def setUp(self):
        fd, self.path = tempfile.mkstemp(suffix=".db")
        os.close(fd)
        self.db = CorpusDB(self.path)
        self.a1 = self.db.add_user("甲", "annotator")
        self.a2 = self.db.add_user("乙", "annotator")
        self.arb = self.db.add_user("仲裁", "arbitrator")
        self.mgr = self.db.add_user("管理", "manager")
        self.g = self.db.add_guideline("v1", "独立标注")
        self.batch = self.db.create_batch("测试批次", self.g)
        self.item1 = self.db.add_item(self.batch, 1, "这个版本很快。")
        self.item2 = self.db.add_item(self.batch, 2, "没有明显变化。")

    def tearDown(self):
        self.db.close()
        os.unlink(self.path)

    def test_full_annotation_disagreement_adjudication_freeze_flow(self):
        for item in (self.item1, self.item2):
            self.db.assign(item, self.a1)
            self.db.assign(item, self.a2)
        self.db.submit_annotation(self.item1, self.a1, "正向")
        self.db.submit_annotation(self.item1, self.a2, "中性")
        self.db.submit_annotation(self.item2, self.a1, "中性")
        self.db.submit_annotation(self.item2, self.a2, "中性")
        self.assertEqual(1, len(self.db.disagreements(self.batch)))
        with self.assertRaisesRegex(DomainError, "分歧"):
            self.db.freeze_batch(self.batch, self.mgr)
        self.db.adjudicate(self.item1, "正向", "速度描述构成明确正向倾向", self.arb)
        result = self.db.freeze_batch(self.batch, self.mgr)
        self.assertIsNotNone(result["metrics"]["pairwise_agreement"])
        exported = self.db.export_gold(self.batch)
        self.assertEqual(2, len(exported["records"]))
        self.assertEqual("adjudication", exported["records"][0]["source"])

    def test_answer_isolation_and_role_validation(self):
        self.db.assign(self.item1, self.a1)
        self.db.assign(self.item1, self.a2)
        self.db.add_discussion(self.item1, self.a2, "我认为是正向", True)
        secret = self.db.get_item_for_user(self.item1, self.a1)
        self.assertTrue(secret["discussions"][0]["hidden"])
        self.db.submit_annotation(self.item1, self.a1, "负向")
        visible = self.db.get_item_for_user(self.item1, self.a1)
        self.assertFalse(visible["discussions"][0].get("hidden", False))
        with self.assertRaisesRegex(DomainError, "标注员"):
            self.db.assign(self.item2, self.arb)

    def _annotated_batch_ready_for_freeze(self):
        for item in (self.item1, self.item2):
            self.db.assign(item, self.a1)
            self.db.assign(item, self.a2)
        self.db.submit_annotation(self.item1, self.a1, "正向")
        self.db.submit_annotation(self.item1, self.a2, "正向")
        self.db.submit_annotation(self.item2, self.a1, "中性")
        self.db.submit_annotation(self.item2, self.a2, "中性")

    def test_rework_full_flow_blocks_freeze_and_resubmit_closes(self):
        self._annotated_batch_ready_for_freeze()
        rw = self.db.create_rework(self.item1, self.a1, self.mgr, "疑似误标，请复核语气强度")
        self.assertEqual("pending", self.db.list_reworks(self.batch)[0]["status"])
        with self.assertRaisesRegex(DomainError, "抽检返工"):
            self.db.freeze_batch(self.batch, self.mgr)
        with self.assertRaisesRegex(DomainError, "待返工"):
            self.db.create_rework(self.item1, self.a1, self.mgr, "再次建单应被拒绝")
        pending = self.db.get_item_for_user(self.item1, self.a1)["pending_rework"]
        self.assertEqual(rw, pending["id"])
        self.db.submit_annotation(self.item1, self.a1, "正向", "复核后维持原标签")
        records = self.db.list_reworks(self.batch)
        self.assertEqual("done", records[0]["status"])
        self.assertIsNotNone(records[0]["resolved_at"])
        self.assertEqual([], self.db.list_reworks(self.batch, "pending"))
        self.assertEqual(1, len(self.db.list_reworks(self.batch, "done")))
        self.db.freeze_batch(self.batch, self.mgr)

    def test_rework_requires_submitted_annotation_and_manager(self):
        self.db.assign(self.item1, self.a1)
        with self.assertRaisesRegex(DomainError, "已提交"):
            self.db.create_rework(self.item1, self.a1, self.mgr, "还没有标注")
        self.db.submit_annotation(self.item1, self.a1, "正向")
        with self.assertRaisesRegex(DomainError, "管理员"):
            self.db.create_rework(self.item1, self.a1, self.a2, "标注员不能发起返工")

    def test_freeze_export_keeps_labels_reasons_and_times(self):
        self._annotated_batch_ready_for_freeze()
        self.db.create_rework(self.item1, self.a1, self.mgr, "语气判断依据不足，请重标")
        self.db.submit_annotation(self.item1, self.a1, "负向", "复核后确认为反讽")
        self.db.adjudicate(self.item1, "负向", "上下文显示该句为反讽表达", self.arb)
        self.db.freeze_batch(self.batch, self.mgr)
        exported = self.db.export_gold(self.batch)
        a1_labels = [a["label"] for a in exported["annotations"]
                     if a["item_id"] == self.item1 and a["annotator_id"] == self.a1]
        self.assertEqual(["正向", "负向"], a1_labels)
        self.assertEqual(1, len(exported["reworks"]))
        rework = exported["reworks"][0]
        self.assertEqual("语气判断依据不足，请重标", rework["reason"])
        self.assertIsNotNone(rework["resolved_at"])
        self.assertTrue(all(a["created_at"] for a in exported["annotations"]))

    def test_rework_api_endpoints(self):
        fd, path = tempfile.mkstemp(suffix=".db")
        os.close(fd)
        test_db = CorpusDB(path)
        original = app_module.Handler.db
        app_module.Handler.db = test_db
        server = ThreadingHTTPServer(("127.0.0.1", 0), app_module.Handler)
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        base = f"http://127.0.0.1:{server.server_port}"

        def call(method, url, body=None):
            data = json.dumps(body).encode() if body is not None else None
            req = urllib.request.Request(base + url, data=data, method=method,
                                         headers={"Content-Type": "application/json"})
            with urllib.request.urlopen(req) as resp:
                return resp.status, json.loads(resp.read())

        try:
            a1 = test_db.add_user("接口标注员", "annotator")
            mgr = test_db.add_user("接口管理员", "manager")
            g = test_db.add_guideline("api-v1", "规则")
            batch = test_db.create_batch("接口批次", g)
            item = test_db.add_item(batch, 1, "需要返工的文本。")
            test_db.assign(item, a1)
            test_db.submit_annotation(item, a1, "正向")
            status, payload = call("POST", "/api/reworks",
                                   {"item_id": item, "annotator_id": a1, "manager_id": mgr, "reason": "抽检发现误标"})
            self.assertEqual(201, status)
            status, payload = call("GET", f"/api/batches/{batch}/reworks?status=pending")
            self.assertEqual(200, status)
            self.assertEqual(1, len(payload["reworks"]))
            status, payload = call("POST", "/api/annotations",
                                   {"item_id": item, "annotator_id": a1, "label": "负向"})
            self.assertEqual(201, status)
            status, payload = call("GET", f"/api/batches/{batch}/reworks?status=done")
            self.assertEqual("done", payload["reworks"][0]["status"])
        finally:
            server.shutdown()
            server.server_close()
            app_module.Handler.db = original
            test_db.close()
            os.unlink(path)


if __name__ == "__main__":
    unittest.main()
