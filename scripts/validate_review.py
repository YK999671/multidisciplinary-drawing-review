#!/usr/bin/env python3
"""Validate declared review data; does not validate engineering conclusions."""
from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
import re


FIELDS = {
    'sources': 'source_id path sha256 read_status limitations',
    'sheets': 'sheet_id source_id drawing_no revision status locator',
    'rules': 'rule_id package title basis_type basis verified',
    'scopes': 'scope_id kind label discipline required_depth',
    'evidence': 'evidence_id source_id source_sha256 sheet_id locator grade independence_key',
    'checks': 'check_id scope_id rule_id applicability applicability_reason target_depth achieved_depth result review_status evidence_ids issue_ids',
    'issues': 'issue_id title scope_ids rule_ids severity judgement status finding impact question_to_designer evidence_ids closure_evidence_ids recheck_ids',
}
DEPTH = {f'L{i}': i for i in range(5)}
CONFIRMED = {'confirmed_conflict', 'confirmed_missing', 'verified_noncompliance'}


def validate(data, verify_source_files=False, review_dir=None):
    errors, warnings = [], []

    def error(where, message):
        errors.append(f'{where}: {message}')

    if not isinstance(data, dict):
        return {'valid': False, 'errors': ['root must be an object'], 'warnings': [], 'metrics': {}}
    if data.get('schema_version') != '1.0':
        error('root', 'schema_version must be 1.0')
    project = data.get('project')
    if not isinstance(project, dict) or any(not project.get(k) for k in ('name', 'round', 'expected_scope_basis')):
        error('project', 'name, round and expected_scope_basis required')

    indexed = {}
    for collection, fields in FIELDS.items():
        rows = data.get(collection)
        indexed[collection] = {}
        if not isinstance(rows, list):
            error(collection, 'must be an array'); continue
        key = fields.split()[0]
        for n, row in enumerate(rows):
            where = f'{collection}[{n}]'
            if not isinstance(row, dict):
                error(where, 'must be an object'); continue
            missing = [f for f in fields.split() if f not in row]
            if missing:
                error(where, 'missing ' + ', '.join(missing))
            identity = row.get(key)
            if not isinstance(identity, str) or not identity.strip():
                error(where, f'{key} must be a nonempty string'); continue
            if identity in indexed[collection]:
                error(where, f'duplicate ID {identity}'); continue
            indexed[collection][identity] = row

    array_fields = {'limitations', 'discipline', 'evidence_ids', 'issue_ids', 'scope_ids',
                    'rule_ids', 'closure_evidence_ids', 'recheck_ids'}
    for collection, rows in indexed.items():
        for identity, row in rows.items():
            for key in FIELDS[collection].split():
                value = row.get(key)
                if key in array_fields:
                    if not isinstance(value, list) or any(not isinstance(x, str) for x in value):
                        error(identity, f'{key} must be a string array')
                elif key == 'verified':
                    if not isinstance(value, bool): error(identity, 'verified must be boolean')
                elif key == 'sheet_id' and collection == 'evidence' and value is None:
                    pass
                elif not isinstance(value, str):
                    error(identity, f'{key} must be a string')
    if errors:
        return {'valid': False, 'errors': errors, 'warnings': [], 'metrics': {}, 'metrics_usable': False}

    def choice(row, key, allowed, where):
        value = row.get(key)
        if not isinstance(value, str) or value not in allowed:
            error(where, f'invalid {key}: {value!r}')

    def nonempty(row, keys, where):
        for key in keys.split():
            if not isinstance(row.get(key), str) or not row[key].strip():
                error(where, f'{key} must be a nonempty string')

    def ref(collection, identity, where):
        if not isinstance(identity, str) or identity not in indexed[collection]:
            error(where, f'unknown {collection} ID {identity!r}'); return None
        return indexed[collection][identity]

    def refs(row, key, collection, where, required=False):
        values = row.get(key)
        if not isinstance(values, list):
            error(where, f'{key} must be an array'); return []
        if required and not values:
            error(where, f'{key} must not be empty')
        seen, result = set(), []
        for identity in values:
            item = ref(collection, identity, where)
            if isinstance(identity, str):
                if identity in seen:
                    error(where, f'duplicate reference in {key}: {identity}')
                seen.add(identity)
            if item is not None:
                result.append(item)
        return result

    for sid, src in indexed['sources'].items():
        nonempty(src, 'path sha256', sid)
        if not re.fullmatch(r'[0-9a-fA-F]{64}', str(src.get('sha256', ''))):
            error(sid, 'invalid sha256')
        choice(src, 'read_status', {'read', 'partial', 'unreadable', 'pending'}, sid)
        if not isinstance(src.get('limitations'), list):
            error(sid, 'limitations must be an array')
        elif src.get('read_status') != 'read' and not src['limitations']:
            error(sid, 'limited/unread sources require limitations')
        path = src.get('path')
        if isinstance(path, str):
            if not Path(path).is_absolute():
                error(sid, 'source path must be absolute')
            if verify_source_files:
                try:
                    with Path(path).open('rb') as stream:
                        actual = hashlib.file_digest(stream, 'sha256').hexdigest()
                    if actual.lower() != str(src.get('sha256', '')).lower():
                        error(sid, 'source file changed; evidence must be rechecked')
                except (OSError, ValueError) as exc:
                    error(sid, f'cannot verify source: {exc}')

    for sid, sheet in indexed['sheets'].items():
        ref('sources', sheet.get('source_id'), sid)
        nonempty(sheet, 'drawing_no revision locator', sid)
        choice(sheet, 'status', {'current', 'superseded', 'reference', 'unresolved'}, sid)

    for rid, rule in indexed['rules'].items():
        nonempty(rule, 'package title basis', rid)
        choice(rule, 'basis_type', {'drawing_consistency', 'project_requirement', 'normative', 'engineering_risk'}, rid)
        if not isinstance(rule.get('verified'), bool):
            error(rid, 'verified must be boolean')

    for sid, scope in indexed['scopes'].items():
        nonempty(scope, 'label', sid)
        choice(scope, 'kind', {'site', 'building', 'floor', 'zone', 'space', 'component', 'system', 'interface'}, sid)
        choice(scope, 'required_depth', {'L1', 'L2', 'L3', 'L4'}, sid)
        disciplines = scope.get('discipline')
        if not isinstance(disciplines, list) or not disciplines or any(not isinstance(x, str) or not x.strip() for x in disciplines):
            error(sid, 'discipline must be a nonempty string array')

    for eid, evidence in indexed['evidence'].items():
        src = ref('sources', evidence.get('source_id'), eid)
        nonempty(evidence, 'locator independence_key', eid)
        choice(evidence, 'grade', {'A', 'B', 'C', 'D'}, eid)
        if src and evidence.get('source_sha256') != src.get('sha256'):
            error(eid, 'evidence hash differs from source version')
        if evidence.get('sheet_id') is not None:
            sheet = ref('sheets', evidence.get('sheet_id'), eid)
            if sheet and sheet.get('source_id') != evidence.get('source_id'):
                error(eid, 'sheet and evidence source differ')

    def current_evidence(items, where):
        for evidence in items:
            sheet = indexed['sheets'].get(evidence.get('sheet_id'))
            if sheet and sheet.get('status') != 'current':
                error(where, f'evidence {evidence["evidence_id"]} uses non-current sheet')
            src = indexed['sources'].get(evidence.get('source_id'))
            if src and src.get('read_status') in {'unreadable', 'pending'}:
                error(where, 'evidence source has not been read')

    complete, applicable = 0, 0
    result_counts = {}
    planned_scopes, planned_rules = set(), set()
    usable_passes = set()
    for cid, check in indexed['checks'].items():
        scope = ref('scopes', check.get('scope_id'), cid)
        rule = ref('rules', check.get('rule_id'), cid)
        if scope: planned_scopes.add(scope['scope_id'])
        if rule: planned_rules.add(rule['rule_id'])
        choice(check, 'applicability', {'yes', 'no', 'unknown'}, cid)
        choice(check, 'target_depth', {'L1', 'L2', 'L3', 'L4'}, cid)
        choice(check, 'achieved_depth', set(DEPTH), cid)
        choice(check, 'result', {'pending', 'pass', 'issue', 'needs_confirmation', 'blocked', 'not_applicable'}, cid)
        choice(check, 'review_status', {'pending', 'reviewed'}, cid)
        result = check.get('result')
        if isinstance(result, str): result_counts[result] = result_counts.get(result, 0) + 1
        ev = refs(check, 'evidence_ids', 'evidence', cid, result in {'pass', 'issue'})
        issues = refs(check, 'issue_ids', 'issues', cid, result == 'issue')
        if (check.get('applicability') == 'no') != (result == 'not_applicable'):
            error(cid, 'not_applicable must correspond to applicability=no')
        if check.get('applicability') in {'no', 'unknown'}:
            nonempty(check, 'applicability_reason', cid)
        if check.get('applicability') != 'no': applicable += 1
        if check.get('applicability') != 'yes' and result in {'pass', 'issue'}:
            error(cid, 'completed substantive checks require applicability=yes')
        if result in {'pass', 'issue'}:
            current_evidence(ev, cid)
            if check.get('review_status') != 'reviewed':
                error(cid, 'completed checks must be reviewed')
            if not rule or rule.get('verified') is not True:
                error(cid, 'completed checks require a verified rule basis')
            if DEPTH.get(str(check.get('achieved_depth')), -1) < DEPTH.get(str(check.get('target_depth')), 99):
                error(cid, 'achieved depth is below target')
            else:
                if check.get('review_status') == 'reviewed' and check.get('applicability') == 'yes':
                    complete += 1
                    if result == 'pass': usable_passes.add(cid)
        for issue in issues:
            if check.get('scope_id') not in issue.get('scope_ids', []):
                error(cid, 'linked issue does not include check scope')

    for sid in indexed['scopes'].keys() - planned_scopes:
        error(sid, 'scope has no planned checks')
    for sid in planned_scopes:
        targets = [DEPTH.get(c['target_depth'], -1) for c in indexed['checks'].values()
                   if c['scope_id'] == sid and c['applicability'] != 'no']
        if targets and max(targets) < DEPTH.get(indexed['scopes'][sid]['required_depth'], 99):
            error(sid, 'planned checks never reach declared scope depth')
    for rid in indexed['rules'].keys() - planned_rules:
        error(rid, 'rule has no planned check or not-applicable record')

    for iid, issue in indexed['issues'].items():
        nonempty(issue, 'title finding impact question_to_designer', iid)
        scopes = refs(issue, 'scope_ids', 'scopes', iid, True)
        rules = refs(issue, 'rule_ids', 'rules', iid, True)
        ev = refs(issue, 'evidence_ids', 'evidence', iid, True)
        closing = refs(issue, 'closure_evidence_ids', 'evidence', iid)
        rechecks = refs(issue, 'recheck_ids', 'checks', iid)
        choice(issue, 'severity', {'high', 'medium', 'low'}, iid)
        choice(issue, 'judgement', CONFIRMED | {'interface_open', 'construction_risk', 'needs_confirmation', 'undetermined'}, iid)
        choice(issue, 'status', {'open', 'answered_pending_drawing', 'drawing_updated_pending_review', 'closed', 'accepted_risk', 'duplicate'}, iid)
        if issue.get('status') not in {'closed', 'duplicate'} and issue.get('judgement') in CONFIRMED:
            current_evidence(ev, iid)
        if issue.get('judgement') in CONFIRMED:
            independent = {e.get('origin_group', e.get('independence_key')) for e in ev if e.get('grade') == 'B' and isinstance(e.get('origin_group', e.get('independence_key')), str)}
            locations = {(e.get('source_id'), e.get('sheet_id'), str(e.get('locator'))) for e in ev if e.get('grade') == 'B'}
            if not any(e.get('grade') == 'A' for e in ev) and (len(independent) < 2 or len(locations) < 2):
                error(iid, 'confirmed finding requires grade A or independent grade B evidence')
        if issue.get('judgement') == 'confirmed_missing':
            nonempty(issue, 'missing_basis search_scope', iid)
        if issue.get('judgement') == 'verified_noncompliance':
            if not any(r.get('verified') is True and r.get('basis_type') in {'normative', 'project_requirement'} for r in rules):
                error(iid, 'noncompliance requires verified normative/project requirement')
        if issue.get('status') == 'closed':
            nonempty(issue, 'closure_basis', iid)
            if not closing or not rechecks:
                error(iid, 'closure evidence and rechecks required')
            current_evidence(closing, iid)
            if any(c['check_id'] not in usable_passes for c in rechecks):
                error(iid, 'closure rechecks must be reviewed passes at target depth')
            covered = {c.get('scope_id') for c in rechecks if c['check_id'] in usable_passes}
            if any(s['scope_id'] not in covered for s in scopes):
                error(iid, 'closure rechecks do not cover all affected scopes')
        if issue.get('status') == 'accepted_risk':
            nonempty(issue, 'risk_acceptance_basis', iid)
        if issue.get('status') == 'duplicate':
            target = ref('issues', issue.get('duplicate_of'), iid)
            if target and (target['issue_id'] == iid or target.get('status') == 'duplicate'):
                error(iid, 'duplicate must point directly to a retained primary issue')

    extra_metrics = validate_extensions(data, indexed, errors, warnings, review_dir)
    if not indexed['checks']:
        warnings.append('No planned checks: this is not a completed review.')
    if complete < applicable:
        warnings.append('Declared review plan is incomplete; publish only with explicit limitations.')
    warnings.append('Structural validation cannot prove scope completeness, evidence truth or engineering correctness.')
    metrics = {
        'planned_checks': len(indexed['checks']), 'applicable_or_unknown_checks': applicable,
        'completed_checks': complete, 'planned_check_completion': complete / applicable if applicable else None,
        'results': result_counts, 'issues': len(indexed['issues']),
        'high_impact_unconfirmed': sum(i.get('severity') == 'high' and i.get('judgement') in {'needs_confirmation', 'undetermined'} for i in indexed['issues'].values()),
        'source_files_verified': verify_source_files,
    }
    metrics.update(extra_metrics)
    return {'valid': not errors, 'errors': errors, 'warnings': warnings, 'metrics': metrics,
            'metrics_usable': not errors}


ARTIFACT_FIELDS = ('image', 'geometry_file', 'visibility_audit', 'audit_detail', 'image_metadata', 'geometry_detail')


def validate_extensions(data, ix, errors, warnings, review_dir=None):
    """Optional task/evidence contracts, plus stricter closure and relation checks."""
    def err(where, message): errors.append(f'{where}: {message}')
    def strings(value): return isinstance(value, list) and all(isinstance(x, str) and x.strip() for x in value)
    def refs(values, collection, where):
        if not strings(values): err(where, 'references must be nonempty strings in an array'); return []
        if len(values) != len(set(values)): err(where, 'duplicate references')
        for key in values:
            if key not in ix[collection]: err(where, f'unknown {collection} ID {key}')
        return [ix[collection][key] for key in values if key in ix[collection]]

    for eid, e in ix['evidence'].items():
        if 'origin_group' in e and (not isinstance(e['origin_group'], str) or not e['origin_group'].strip()):
            err(eid, 'origin_group must be a nonempty string')
        if 'verification' in e and (not isinstance(e['verification'], dict) or any(not isinstance(k, str) or not isinstance(v, bool) for k, v in e['verification'].items())):
            err(eid, 'verification must map names to booleans')
    for rid, rule in ix['rules'].items():
        if 'required_evidence_checks' in rule and not strings(rule['required_evidence_checks']):
            err(rid, 'required_evidence_checks must be a string array')
    for cid, c in ix['checks'].items():
        if 'parent_check_id' in c:
            err(cid, 'use tasks for aggregation; parent_check_id is not an executable hierarchy')
        if 'object_ids' in c and not strings(c['object_ids']): err(cid, 'object_ids must be a string array')
        if c['result'] not in {'pass', 'issue'}: continue
        ev = [ix['evidence'][key] for key in c['evidence_ids'] if key in ix['evidence']]
        if c['result'] == 'pass' and not any(e['grade'] in {'A', 'B'} for e in ev):
            err(cid, 'pass cannot rely exclusively on candidate/risk evidence C/D')
        required = ix['rules'].get(c['rule_id'], {}).get('required_evidence_checks', [])
        if strings(required):
            for name in required:
                if not any(isinstance(e.get('verification'), dict) and e['verification'].get(name) is True and e['grade'] in {'A', 'B'} for e in ev):
                    err(cid, f'missing verified evidence requirement: {name}')
        if not required: warnings.append(f'{cid}: evidence sufficiency not structured; manual review still required')

    for iid, issue in ix['issues'].items():
        if 'object_ids' in issue and not strings(issue['object_ids']): err(iid, 'object_ids must be a string array')
        if issue['status'] != 'closed': continue
        rechecks = [ix['checks'][key] for key in issue['recheck_ids'] if key in ix['checks']]
        matching = [c for c in rechecks if c['result'] == 'pass' and c['review_status'] == 'reviewed' and c['scope_id'] in issue['scope_ids'] and c['rule_id'] in issue['rule_ids']]
        if not set(issue['scope_ids']) <= {c['scope_id'] for c in matching} or not set(issue['rule_ids']) <= {c['rule_id'] for c in matching}:
            err(iid, 'closure must recheck original affected scopes and rules')
        if strings(issue.get('object_ids')) and issue['object_ids']:
            covered = {o for c in matching if strings(c.get('object_ids', [])) for o in c.get('object_ids', [])}
            if not set(issue['object_ids']) <= covered: err(iid, 'closure does not cover original object_ids')
        else: warnings.append(f'{iid}: closure object coverage not structured; manual verification required')

    relations = data.get('relations', [])
    if not isinstance(relations, list): err('relations', 'must be an array'); relations = []
    seen = set()
    for n, row in enumerate(relations):
        where = f'relations[{n}]'
        if not isinstance(row, dict): err(where, 'must be an object'); continue
        for key in ('relation_id', 'from_object', 'to_object', 'relation_type', 'scope_id', 'certainty'):
            if not isinstance(row.get(key), str) or not row[key].strip(): err(where, f'{key} required')
        rid = row.get('relation_id')
        if isinstance(rid, str):
            if rid in seen: err(where, 'duplicate relation_id')
            seen.add(rid)
        if isinstance(row.get('scope_id'), str) and row['scope_id'] not in ix['scopes']: err(where, 'unknown relation scope')
        refs(row.get('evidence_ids'), 'evidence', where)
        refs(row.get('version_dependencies'), 'sources', where)

    artifacts_checked = 0
    if review_dir is not None:
        base = Path(review_dir).resolve()
        for row in list(ix['evidence'].values()) + relations:
            if not isinstance(row, dict): continue
            for field in ARTIFACT_FIELDS:
                if field not in row: continue
                value = row[field]; where = row.get('evidence_id', row.get('relation_id', 'artifact'))
                if not isinstance(value, str) or not value.strip(): err(where, f'{field} must be a file path'); continue
                p = (base / value).resolve()
                if not p.is_relative_to(base): err(where, f'{field} must stay within review directory'); continue
                if not p.is_file(): err(where, f'missing {field}: {value}')
                else: artifacts_checked += 1

    tasks = data.get('tasks', [])
    if not isinstance(tasks, list): err('tasks', 'must be an array'); tasks = []
    taskmap = {}; taskowners = {}; checkowners = {}
    for n, task in enumerate(tasks):
        where = f'tasks[{n}]'
        if not isinstance(task, dict): err(where, 'must be an object'); continue
        tid = task.get('task_id')
        if not isinstance(tid, str) or not tid.strip(): err(where, 'task_id required'); continue
        if tid in taskmap: err(tid, 'duplicate task_id'); continue
        taskmap[tid] = task
        if not isinstance(task.get('label'), str) or not task['label'].strip(): err(tid, 'label required')
        if 'status' in task: err(tid, 'task status is derived, do not store it')
        for key, owners in [('check_ids', checkowners), ('task_ids', taskowners)]:
            values = task.get(key, [])
            if not strings(values): err(tid, f'{key} must be a string array'); continue
            for value in values:
                if value in owners: err(tid, f'{value} has multiple aggregation parents')
                owners[value] = tid
        if not task.get('check_ids') and not task.get('task_ids'): err(tid, 'empty task')
    for cid in checkowners:
        if cid not in ix['checks']: err(cid, 'unknown child check')
    for tid in taskowners:
        if tid not in taskmap: err(tid, 'unknown child task')
    if tasks:
        for cid in ix['checks'].keys() - checkowners.keys(): err(cid, 'check missing from task hierarchy')
    visiting = set(); cache = {}; summaries = {}
    def descendants(tid):
        if tid in visiting: err(tid, 'task hierarchy cycle'); return set()
        if tid in cache: return cache[tid]
        if tid not in taskmap: return set()
        visiting.add(tid); task = taskmap[tid]
        ids = set(task.get('check_ids', [])) & ix['checks'].keys() if strings(task.get('check_ids', [])) else set()
        if strings(task.get('task_ids', [])):
            for child in task.get('task_ids', []): ids |= descendants(child)
        visiting.remove(tid); cache[tid] = ids
        rows = [ix['checks'][cid] for cid in ids if ix['checks'][cid]['applicability'] != 'no']
        done = [c for c in rows if c['applicability'] == 'yes' and c['result'] in {'pass', 'issue'} and c['review_status'] == 'reviewed' and DEPTH.get(c['achieved_depth'], -1) >= DEPTH.get(c['target_depth'], 99)]
        status = 'not_applicable' if not rows else 'complete' if len(done) == len(rows) else 'in_progress' if any(c['achieved_depth'] != 'L0' or c['result'] not in {'pending', 'blocked'} for c in rows) else 'blocked' if all(c['result'] == 'blocked' for c in rows) else 'pending'
        summaries[tid] = dict(label=task.get('label'), status=status, applicable_checks=len(rows), completed_checks=len(done), completion=len(done)/len(rows) if rows else None)
        return ids
    for tid in taskmap: descendants(tid)
    return dict(task_summaries=summaries, artifacts_checked=artifacts_checked)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('review_json')
    parser.add_argument('--verify-source-files', action='store_true')
    parser.add_argument('--verify-artifacts', action='store_true')
    args = parser.parse_args()
    try:
        data = json.loads(Path(args.review_json).read_text(encoding='utf-8-sig'))
        result = validate(data, args.verify_source_files, Path(args.review_json).parent if args.verify_artifacts else None)
    except (OSError, ValueError) as exc:
        result = {'valid': False, 'errors': [str(exc)], 'warnings': [], 'metrics': {}}
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0 if result['valid'] else 1


if __name__ == '__main__':
    raise SystemExit(main())
