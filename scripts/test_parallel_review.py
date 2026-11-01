#!/usr/bin/env python3
"""Synthetic tests for isolated discipline-agent planning and merging."""
import copy
import json
from pathlib import Path
import tempfile
import unittest

from parallel_review import canonical, load_fragment, load_plan, merge, sha, waves
from test_validate_review import fixture


class ParallelReviewTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.source = self.root / "source.txt"
        self.source.write_text("synthetic source, not an engineering drawing", encoding="utf-8")
        self.base = fixture(self.source)
        self.base_path = self.root / "base" / "review.json"
        self.base_path.parent.mkdir()
        self.base_path.write_text(json.dumps(self.base), encoding="utf-8")
        self.plan_path = self.root / "run" / "parallel-plan.json"
        self.plan_path.parent.mkdir()
        self.plan = {
            "schema_version": "1.0",
            "run_id": "P1",
            "base_review": str(self.base_path),
            "base_review_sha256": sha(self.base_path),
            "target_round": "R2 parallel candidate",
            "assignments": [
                {"assignment_id": "PLB", "discipline": "plumbing", "scope_ids": ["SYS1"], "rule_ids": ["R1"],
                 "owned_check_ids": ["C1"], "id_prefix": "P1-PLB-", "fragment": "agents/PLB/fragment.json",
                 "exclusive_resources": ["autocad-session"]},
                {"assignment_id": "IFC", "discipline": "interface", "scope_ids": ["SYS1"], "rule_ids": ["R1"],
                 "owned_check_ids": [], "id_prefix": "P1-IFC-", "fragment": "agents/IFC/fragment.json",
                 "exclusive_resources": ["autocad-session"]},
            ],
        }
        self.plan_path.write_text(json.dumps(self.plan), encoding="utf-8")
        self._write_fragments()

    def _write_fragments(self):
        plb_dir = self.plan_path.parent / "agents" / "PLB"
        ifc_dir = self.plan_path.parent / "agents" / "IFC"
        plb_dir.mkdir(parents=True); ifc_dir.mkdir(parents=True)
        image = plb_dir / "detail.png"
        image.write_bytes(b"synthetic image bytes")
        evidence = dict(self.base["evidence"][0], evidence_id="P1-PLB-E1", locator="agent crop", image="detail.png")
        issue = {"issue_id": "P1-PLB-I1", "title": "Candidate interface question", "scope_ids": ["SYS1"],
                 "rule_ids": ["R1"], "severity": "medium", "judgement": "needs_confirmation", "status": "open",
                 "finding": "Synthetic finding", "impact": "Synthetic impact", "question_to_designer": "Confirm",
                 "evidence_ids": ["P1-PLB-E1"], "closure_evidence_ids": [], "recheck_ids": []}
        changed = copy.deepcopy(self.base["checks"][0])
        changed.update(result="needs_confirmation", evidence_ids=["P1-PLB-E1"], issue_ids=["P1-PLB-I1"])
        plb = {"schema_version": "1.0", "run_id": "P1", "assignment_id": "PLB",
               "base_review_sha256": self.plan["base_review_sha256"], "status": "partial",
               "notes": ["Synthetic discipline result"], "limitations": ["Not engineering evidence"],
               "new_evidence": [evidence], "new_checks": [], "new_issues": [issue], "new_relations": [],
               "check_updates": [{"check_id": "C1", "expected_sha256": canonical(self.base["checks"][0]), "value": changed}]}
        empty = {"schema_version": "1.0", "run_id": "P1", "assignment_id": "IFC",
                 "base_review_sha256": self.plan["base_review_sha256"], "status": "blocked", "notes": [],
                 "limitations": ["No interface input"], "new_evidence": [], "new_checks": [], "new_issues": [],
                 "new_relations": [], "check_updates": []}
        (plb_dir / "fragment.json").write_text(json.dumps(plb), encoding="utf-8")
        (ifc_dir / "fragment.json").write_text(json.dumps(empty), encoding="utf-8")

    def test_plan_and_resource_schedule(self):
        _, _, _, _, assignments = load_plan(self.plan_path)
        self.assertEqual(set(assignments), {"PLB", "IFC"})
        self.assertEqual(waves(self.plan_path, 2)["waves"], [["PLB"], ["IFC"]])

    def test_fragment_validation_and_merge_candidate(self):
        _, _, _, _, _, fragment = load_fragment(self.plan_path, "PLB")
        self.assertEqual(fragment["status"], "partial")
        output = self.root / "candidate"
        result = merge(self.plan_path, output)
        self.assertEqual(result["publication_state"], "candidate_requires_chief_review_and_qa")
        self.assertTrue(result["validation"]["valid"], result["validation"]["errors"])
        merged = json.loads((output / "review.json").read_text(encoding="utf-8"))
        self.assertEqual(merged["checks"][0]["result"], "needs_confirmation")
        path = merged["evidence"][-1]["image"]
        self.assertTrue((output / path).is_file())
        self.assertTrue(path.startswith("evidence/parallel/PLB/"))
        self.assertEqual(merged["parallel_review"]["publication_state"], "candidate_requires_chief_review_and_qa")

    def test_rejects_duplicate_check_owner(self):
        self.plan["assignments"][1]["owned_check_ids"] = ["C1"]
        self.plan_path.write_text(json.dumps(self.plan), encoding="utf-8")
        with self.assertRaisesRegex(ValueError, "多个任务"):
            load_plan(self.plan_path)

    def test_rejects_overlapping_id_prefixes(self):
        self.plan["assignments"][1]["id_prefix"] = "P1-PLB-X-"
        self.plan_path.write_text(json.dumps(self.plan), encoding="utf-8")
        with self.assertRaisesRegex(ValueError, "前缀"):
            load_plan(self.plan_path)

    def test_rejects_fragment_scope_escape(self):
        path = self.plan_path.parent / "agents" / "PLB" / "fragment.json"
        fragment = json.loads(path.read_text(encoding="utf-8"))
        fragment["new_issues"][0]["scope_ids"] = ["OUTSIDE"]
        path.write_text(json.dumps(fragment), encoding="utf-8")
        with self.assertRaisesRegex(ValueError, "超出任务"):
            load_fragment(self.plan_path, "PLB")

    def test_rejects_stale_baseline_and_preserves_output(self):
        path = self.plan_path.parent / "agents" / "PLB" / "fragment.json"
        fragment = json.loads(path.read_text(encoding="utf-8"))
        fragment["base_review_sha256"] = "0" * 64
        path.write_text(json.dumps(fragment), encoding="utf-8")
        output = self.root / "candidate"
        with self.assertRaisesRegex(ValueError, "基线"):
            merge(self.plan_path, output)
        self.assertFalse(output.exists())

    def test_rejects_existing_output(self):
        output = self.root / "candidate"; output.mkdir()
        with self.assertRaisesRegex(ValueError, "拒绝覆盖"):
            merge(self.plan_path, output)


if __name__ == "__main__":
    unittest.main()
