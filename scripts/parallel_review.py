#!/usr/bin/env python3
"""Validate and merge isolated discipline-agent review fragments.

This module does not start agents and does not approve engineering conclusions.
The main reviewer owns dispatch, cross-discipline review, QA, and publication.
"""
from __future__ import annotations

import argparse
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import shutil
import uuid

from validate_review import ARTIFACT_FIELDS, validate


COLLECTIONS = ("evidence", "checks", "issues", "relations")
FRAGMENT_KEYS = tuple("new_" + name for name in COLLECTIONS)
STATUSES = {"complete", "partial", "blocked"}


def read(path):
    return json.loads(Path(path).read_text(encoding="utf-8-sig"))


def sha(path):
    with Path(path).open("rb") as handle:
        return hashlib.file_digest(handle, "sha256").hexdigest()


def canonical(value):
    return hashlib.sha256(json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")).hexdigest()


def inside(root, value):
    path = (Path(root) / value).resolve()
    if not path.is_relative_to(Path(root).resolve()):
        raise ValueError("路径越出允许目录: " + str(value))
    return path


def unique_strings(value, label, allow_empty=True):
    if not isinstance(value, list) or any(not isinstance(item, str) or not item.strip() for item in value):
        raise ValueError(label + " 必须为非空字符串数组")
    if not allow_empty and not value:
        raise ValueError(label + " 不能为空")
    if len(value) != len(set(value)):
        raise ValueError(label + " 存在重复项")
    return value


def index(rows, key, label):
    if not isinstance(rows, list):
        raise ValueError(label + " 必须为数组")
    result = {}
    for row in rows:
        if not isinstance(row, dict) or not isinstance(row.get(key), str) or not row[key]:
            raise ValueError(label + " 缺少 " + key)
        if row[key] in result:
            raise ValueError(label + " ID重复: " + row[key])
        result[row[key]] = row
    return result


def load_plan(plan_path):
    plan_path = Path(plan_path).resolve()
    plan = read(plan_path)
    required = ("schema_version", "run_id", "base_review", "base_review_sha256", "target_round", "assignments")
    for key in required:
        if key not in plan:
            raise ValueError("并行计划缺少字段: " + key)
    if plan["schema_version"] != "1.0" or not all(isinstance(plan[k], str) and plan[k].strip() for k in required[:-1]):
        raise ValueError("并行计划版本或文本字段无效")
    base_path = Path(plan["base_review"])
    if not base_path.is_absolute():
        base_path = inside(plan_path.parent, base_path)
    base_path = base_path.resolve()
    if not base_path.is_file() or sha(base_path) != plan["base_review_sha256"]:
        raise ValueError("基线台账不存在或哈希不一致")
    base = read(base_path)
    validation = validate(base, True, base_path.parent)
    if not validation["valid"]:
        raise ValueError("基线台账校验失败: " + json.dumps(validation["errors"], ensure_ascii=False))

    scopes = index(base["scopes"], "scope_id", "scopes")
    rules = index(base["rules"], "rule_id", "rules")
    checks = index(base["checks"], "check_id", "checks")
    tasks = index(base.get("tasks", []), "task_id", "tasks")
    assignments = index(plan["assignments"], "assignment_id", "assignments")
    prefixes = []
    check_owners = {}
    fragment_paths = set()
    for aid, item in assignments.items():
        for field in ("discipline", "id_prefix", "fragment"):
            if not isinstance(item.get(field), str) or not item[field].strip():
                raise ValueError(f"任务 {aid} 缺少 {field}")
        owned_scopes = unique_strings(item.get("scope_ids"), aid + ".scope_ids", False)
        owned_rules = unique_strings(item.get("rule_ids"), aid + ".rule_ids", False)
        owned_checks = unique_strings(item.get("owned_check_ids", []), aid + ".owned_check_ids")
        unique_strings(item.get("exclusive_resources", []), aid + ".exclusive_resources")
        if not set(owned_scopes) <= set(scopes) or not set(owned_rules) <= set(rules):
            raise ValueError("任务引用未知范围或规则: " + aid)
        for check_id in owned_checks:
            if check_id not in checks:
                raise ValueError("任务引用未知检查: " + check_id)
            check = checks[check_id]
            if check["scope_id"] not in owned_scopes or check["rule_id"] not in owned_rules:
                raise ValueError("任务拥有的检查超出其范围或规则: " + check_id)
            if check_id in check_owners:
                raise ValueError("检查被多个任务拥有: " + check_id)
            check_owners[check_id] = aid
        prefix = item["id_prefix"]
        if any(prefix.startswith(old) or old.startswith(prefix) for old in prefixes):
            raise ValueError("任务ID前缀相互包含，可能造成归属歧义: " + prefix)
        prefixes.append(prefix)
        fragment_path = inside(plan_path.parent, item["fragment"])
        if fragment_path in fragment_paths:
            raise ValueError("多个任务共用同一成果文件")
        fragment_paths.add(fragment_path)
        task_id = item.get("task_id")
        if base.get("tasks") and (not isinstance(task_id, str) or task_id not in tasks):
            raise ValueError("基线启用tasks时，每个任务须指定现有task_id")
    return plan_path, plan, base_path, base, assignments


def waves(plan_path, worker_slots):
    if worker_slots < 1:
        raise ValueError("worker_slots 至少为1")
    _, _, _, _, assignments = load_plan(plan_path)
    pending = list(assignments.values())
    result = []
    while pending:
        wave, used = [], set()
        for item in list(pending):
            resources = set(item.get("exclusive_resources", []))
            if len(wave) < worker_slots and not resources & used:
                wave.append(item["assignment_id"])
                used |= resources
                pending.remove(item)
        if not wave:
            raise ValueError("无法生成并行批次")
        result.append(wave)
    return {"worker_slots": worker_slots, "waves": result}


def load_fragment(plan_path, assignment_id):
    plan_path, plan, base_path, base, assignments = load_plan(plan_path)
    if assignment_id not in assignments:
        raise ValueError("未知任务: " + assignment_id)
    assignment = assignments[assignment_id]
    path = inside(plan_path.parent, assignment["fragment"])
    if not path.is_file():
        raise ValueError("任务成果不存在: " + str(path))
    fragment = read(path)
    for field in ("schema_version", "run_id", "assignment_id", "base_review_sha256", "status"):
        if not isinstance(fragment.get(field), str) or not fragment[field]:
            raise ValueError("任务成果缺少字段: " + field)
    if fragment["schema_version"] != "1.0" or fragment["run_id"] != plan["run_id"] or fragment["assignment_id"] != assignment_id:
        raise ValueError("任务成果与计划身份不一致")
    if fragment["base_review_sha256"] != plan["base_review_sha256"] or fragment["status"] not in STATUSES:
        raise ValueError("任务成果基线或状态无效")
    unique_strings(fragment.get("notes", []), "notes")
    unique_strings(fragment.get("limitations", []), "limitations")
    for key in FRAGMENT_KEYS:
        if not isinstance(fragment.get(key, []), list):
            raise ValueError(key + " 必须为数组")
    if not isinstance(fragment.get("check_updates", []), list):
        raise ValueError("check_updates 必须为数组")
    validate_fragment_content(base, assignment, fragment, path.parent)
    return plan, base_path, base, assignment, path, fragment


def validate_fragment_content(base, assignment, fragment, fragment_dir):
    base_ids = {
        "evidence": set(index(base["evidence"], "evidence_id", "evidence")),
        "checks": set(index(base["checks"], "check_id", "checks")),
        "issues": set(index(base["issues"], "issue_id", "issues")),
        "relations": set(index(base.get("relations", []), "relation_id", "relations")),
    }
    keys = {"evidence": "evidence_id", "checks": "check_id", "issues": "issue_id", "relations": "relation_id"}
    new_ids = {}
    for name, key in keys.items():
        rows = fragment.get("new_" + name, [])
        rows_by_id = index(rows, key, "new_" + name)
        if set(rows_by_id) & base_ids[name]:
            raise ValueError("新增记录与基线ID冲突: " + name)
        if any(not value.startswith(assignment["id_prefix"]) for value in rows_by_id):
            raise ValueError("新增ID不符合任务前缀: " + name)
        new_ids[name] = set(rows_by_id)
    allowed_scopes, allowed_rules = set(assignment["scope_ids"]), set(assignment["rule_ids"])
    base_sources = set(index(base["sources"], "source_id", "sources"))
    base_sheets = set(index(base["sheets"], "sheet_id", "sheets"))
    for evidence in fragment.get("new_evidence", []):
        if evidence.get("source_id") not in base_sources or (evidence.get("sheet_id") is not None and evidence.get("sheet_id") not in base_sheets):
            raise ValueError("证据引用未知源文件或图面")
        for field in ARTIFACT_FIELDS:
            if field in evidence and not inside(fragment_dir, evidence[field]).is_file():
                raise ValueError("证据附件不存在: " + str(evidence[field]))
    all_evidence = base_ids["evidence"] | new_ids["evidence"]
    all_issues = base_ids["issues"] | new_ids["issues"]
    for check in fragment.get("new_checks", []):
        if check.get("scope_id") not in allowed_scopes or check.get("rule_id") not in allowed_rules:
            raise ValueError("新增检查超出任务范围或规则")
        if not set(check.get("evidence_ids", [])) <= all_evidence or not set(check.get("issue_ids", [])) <= all_issues:
            raise ValueError("新增检查引用未知证据或问题")
    for issue in fragment.get("new_issues", []):
        if not set(issue.get("scope_ids", [])) <= allowed_scopes or not set(issue.get("rule_ids", [])) <= allowed_rules:
            raise ValueError("新增问题超出任务范围或规则")
        if not set(issue.get("evidence_ids", [])) <= all_evidence:
            raise ValueError("新增问题引用未知证据")
    for relation in fragment.get("new_relations", []):
        if relation.get("scope_id") not in allowed_scopes or not set(relation.get("evidence_ids", [])) <= all_evidence:
            raise ValueError("新增关系超出任务范围或引用未知证据")
    base_checks = index(base["checks"], "check_id", "checks")
    updated = set()
    for update in fragment.get("check_updates", []):
        if not isinstance(update, dict) or set(update) != {"check_id", "expected_sha256", "value"}:
            raise ValueError("check_updates 项必须含check_id、expected_sha256、value")
        cid, value = update["check_id"], update["value"]
        if cid in updated or cid not in assignment.get("owned_check_ids", []):
            raise ValueError("检查更新重复或任务无所有权: " + str(cid))
        if not isinstance(value, dict) or value.get("check_id") != cid:
            raise ValueError("检查更新内容身份不一致")
        old = base_checks[cid]
        if update["expected_sha256"] != canonical(old):
            raise ValueError("检查更新的基线指纹不一致: " + cid)
        if value.get("scope_id") != old["scope_id"] or value.get("rule_id") != old["rule_id"]:
            raise ValueError("专业智能体不能改变既有检查的范围或规则")
        if not set(value.get("evidence_ids", [])) <= all_evidence or not set(value.get("issue_ids", [])) <= all_issues:
            raise ValueError("检查更新引用未知证据或问题")
        updated.add(cid)


def copy_artifacts(rows, source_root, destination_root, prefix=None):
    copied = {}
    for row in rows:
        for field in ARTIFACT_FIELDS:
            if field not in row:
                continue
            source = inside(source_root, row[field])
            if not source.is_file():
                raise ValueError("附件不存在: " + str(source))
            key = str(source)
            if key not in copied:
                if prefix:
                    relative = Path("evidence") / "parallel" / prefix / (sha(source)[:12] + "-" + source.name)
                else:
                    relative = source.relative_to(Path(source_root).resolve())
                destination = inside(destination_root, relative)
                destination.parent.mkdir(parents=True, exist_ok=True)
                shutil.copy2(source, destination)
                copied[key] = relative.as_posix()
            row[field] = copied[key]


def merge(plan_path, output):
    plan_path, plan, base_path, base, assignments = load_plan(plan_path)
    output = Path(output).resolve()
    if output.exists():
        raise ValueError("输出目录已存在，拒绝覆盖")
    output.parent.mkdir(parents=True, exist_ok=True)
    temp = output.parent / ("." + output.name + "." + uuid.uuid4().hex + ".tmp")
    temp.mkdir()
    try:
        merged = json.loads(json.dumps(base, ensure_ascii=False))
        copy_artifacts(merged["evidence"] + merged.get("relations", []), base_path.parent, temp)
        check_positions = {row["check_id"]: pos for pos, row in enumerate(merged["checks"])}
        task_positions = {row["task_id"]: row for row in merged.get("tasks", [])}
        audit = []
        for aid, assignment in assignments.items():
            _, _, _, _, fragment_path, fragment = load_fragment(plan_path, aid)
            additions = {name: json.loads(json.dumps(fragment.get("new_" + name, []), ensure_ascii=False)) for name in COLLECTIONS}
            copy_artifacts(additions["evidence"] + additions["relations"], fragment_path.parent, temp, aid)
            for name in COLLECTIONS:
                merged.setdefault(name, []).extend(additions[name])
            for update in fragment.get("check_updates", []):
                value = json.loads(json.dumps(update["value"], ensure_ascii=False))
                # Reuse rewritten paths only through evidence IDs; checks contain no artifact paths.
                merged["checks"][check_positions[update["check_id"]]] = value
            if merged.get("tasks") and additions["checks"]:
                task_positions[assignment["task_id"]].setdefault("check_ids", []).extend(row["check_id"] for row in additions["checks"])
            audit.append({"assignment_id": aid, "discipline": assignment["discipline"], "status": fragment["status"],
                          "fragment_sha256": sha(fragment_path), "notes": fragment.get("notes", []),
                          "limitations": fragment.get("limitations", [])})
        merged["project"] = dict(merged["project"], round=plan["target_round"], previous_round=str(base_path))
        merged["parallel_review"] = {"run_id": plan["run_id"], "base_review_sha256": plan["base_review_sha256"],
                                     "merged_at": datetime.now(timezone.utc).isoformat(), "assignments": audit,
                                     "publication_state": "candidate_requires_chief_review_and_qa"}
        review_path = temp / "review.json"
        review_path.write_text(json.dumps(merged, ensure_ascii=False, indent=2), encoding="utf-8", newline="\n")
        result = validate(merged, True, temp)
        if not result["valid"]:
            raise ValueError("合并候选台账校验失败: " + json.dumps(result["errors"], ensure_ascii=False))
        (temp / "merge-validation.json").write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8", newline="\n")
        os.replace(temp, output)
        return {"output": str(output), "review": str(output / "review.json"), "validation": result,
                "assignment_statuses": {row["assignment_id"]: row["status"] for row in audit},
                "publication_state": "candidate_requires_chief_review_and_qa"}
    except Exception:
        if temp.exists() and temp.is_relative_to(output.parent):
            shutil.rmtree(temp)
        raise


def main(argv=None):
    parser = argparse.ArgumentParser()
    sub = parser.add_subparsers(dest="command", required=True)
    p = sub.add_parser("validate-plan"); p.add_argument("plan")
    p = sub.add_parser("schedule"); p.add_argument("plan"); p.add_argument("--worker-slots", type=int, required=True)
    p = sub.add_parser("validate-fragment"); p.add_argument("plan"); p.add_argument("assignment_id")
    p = sub.add_parser("merge"); p.add_argument("plan"); p.add_argument("--output", required=True)
    args = parser.parse_args(argv)
    try:
        if args.command == "validate-plan":
            _, plan, base_path, _, assignments = load_plan(args.plan)
            result = {"valid": True, "run_id": plan["run_id"], "base_review": str(base_path), "assignments": list(assignments)}
        elif args.command == "schedule":
            result = waves(args.plan, args.worker_slots)
        elif args.command == "validate-fragment":
            _, _, _, _, path, fragment = load_fragment(args.plan, args.assignment_id)
            result = {"valid": True, "fragment": str(path), "status": fragment["status"]}
        else:
            result = merge(args.plan, args.output)
        print(json.dumps(result, ensure_ascii=False, indent=2)); return 0
    except (ValueError, OSError, KeyError, TypeError, json.JSONDecodeError) as exc:
        print(json.dumps({"valid": False, "error": str(exc)}, ensure_ascii=False, indent=2)); return 1


if __name__ == "__main__":
    raise SystemExit(main())
