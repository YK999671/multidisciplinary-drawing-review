#!/usr/bin/env python3
"""Synthetic contract regression tests; no CAD/engineering accuracy claims."""
import copy
import hashlib
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest

from validate_review import validate


def fixture(path):
    digest = hashlib.sha256(path.read_bytes()).hexdigest()
    return {
        'schema_version': '1.0',
        'project': {'name': 'Synthetic review', 'round': 'R1', 'expected_scope_basis': 'Test design index'},
        'sources': [{'source_id': 'SRC1', 'path': str(path), 'sha256': digest, 'read_status': 'read', 'limitations': []}],
        'sheets': [{'sheet_id': 'S1', 'source_id': 'SRC1', 'drawing_no': 'M01', 'revision': 'R1', 'status': 'current', 'locator': 'page 1'}],
        'rules': [{'rule_id': 'R1', 'package': 'PLB', 'title': 'Cross-sheet ID consistency', 'basis_type': 'drawing_consistency', 'basis': 'Design index and system labels', 'verified': True}],
        'scopes': [{'scope_id': 'SYS1', 'kind': 'system', 'label': 'Water system 1', 'discipline': ['plumbing'], 'required_depth': 'L3'}],
        'evidence': [{'evidence_id': 'E1', 'source_id': 'SRC1', 'source_sha256': digest, 'sheet_id': 'S1', 'locator': 'page 1 system label', 'grade': 'B', 'independence_key': 'original-label-1'}],
        'checks': [{'check_id': 'C1', 'scope_id': 'SYS1', 'rule_id': 'R1', 'applicability': 'yes', 'applicability_reason': '', 'target_depth': 'L3', 'achieved_depth': 'L3', 'result': 'pass', 'review_status': 'reviewed', 'evidence_ids': ['E1'], 'issue_ids': []}],
        'issues': [],
    }


def add_issue(data, judgement='needs_confirmation'):
    issue = {'issue_id': 'I1', 'title': 'Unresolved connection', 'scope_ids': ['SYS1'], 'rule_ids': ['R1'],
             'severity': 'high', 'judgement': judgement, 'status': 'open', 'finding': 'Connection not established',
             'impact': 'Potential system interruption', 'question_to_designer': 'Confirm the upstream connection',
             'evidence_ids': ['E1'], 'closure_evidence_ids': [], 'recheck_ids': []}
    data['issues'].append(issue)
    data['checks'][0].update(result='needs_confirmation', issue_ids=['I1'])
    return issue


class ContractTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.source = Path(self.temp.name) / 'source.txt'
        self.source.write_text('synthetic source, not an engineering drawing', encoding='utf-8')
        self.data = fixture(self.source)

    def test_valid_review_and_source_verification(self):
        result = validate(self.data, True)
        self.assertTrue(result['valid'], result['errors'])
        self.assertEqual(result['metrics']['planned_check_completion'], 1)

    def test_cli_reports_validation_and_failure_exit_code(self):
        review = Path(self.temp.name) / 'review.json'
        review.write_text(json.dumps(self.data), encoding='utf-8')
        command = [sys.executable, '-X', 'utf8', str(Path(__file__).with_name('validate_review.py')), str(review), '--verify-source-files']
        good = subprocess.run(command, capture_output=True, text=True, encoding='utf-8')
        self.assertEqual(good.returncode, 0, good.stderr)
        self.assertTrue(json.loads(good.stdout)['valid'])
        self.source.write_text('source changed after review', encoding='utf-8')
        bad = subprocess.run(command, capture_output=True, text=True, encoding='utf-8')
        self.assertEqual(bad.returncode, 1)
        self.assertFalse(json.loads(bad.stdout)['valid'])

    def test_stale_evidence(self):
        self.data['evidence'][0]['source_sha256'] = '0' * 64
        self.assertFalse(validate(self.data)['valid'])

    def test_source_changed_on_disk(self):
        self.source.write_text('new drawing version', encoding='utf-8')
        self.assertFalse(validate(self.data, True)['valid'])

    def test_superseded_sheet_cannot_support_pass(self):
        self.data['sheets'][0]['status'] = 'superseded'
        self.assertFalse(validate(self.data)['valid'])

    def test_duplicate_evidence_is_not_independent(self):
        issue = add_issue(self.data, 'confirmed_conflict')
        duplicate = dict(self.data['evidence'][0], evidence_id='E2')
        self.data['evidence'].append(duplicate)
        issue['evidence_ids'].append('E2')
        self.assertFalse(validate(self.data)['valid'])
        duplicate['independence_key'] = 'invented-other-key'
        self.assertFalse(validate(self.data)['valid'])

    def test_independent_evidence_supports_confirmed_issue(self):
        issue = add_issue(self.data, 'confirmed_conflict')
        self.data['evidence'].append(dict(self.data['evidence'][0], evidence_id='E2', locator='page 1 independent table', independence_key='original-table-2'))
        issue['evidence_ids'].append('E2')
        self.data['checks'][0]['result'] = 'issue'
        self.assertTrue(validate(self.data)['valid'])

    def test_high_impact_uncertainty_is_retained(self):
        add_issue(self.data)
        self.data['evidence'][0]['grade'] = 'C'
        result = validate(self.data)
        self.assertTrue(result['valid'], result['errors'])
        self.assertEqual(result['metrics']['high_impact_unconfirmed'], 1)
        self.assertEqual(result['metrics']['completed_checks'], 0)

    def test_unresolved_version_can_be_reported_as_uncertainty(self):
        add_issue(self.data, 'undetermined')
        self.data['sheets'][0]['status'] = 'unresolved'
        self.data['evidence'][0]['grade'] = 'D'
        self.data['sources'][0].update(read_status='unreadable', limitations=['File inventoried but drawing cannot be parsed'])
        result = validate(self.data)
        self.assertTrue(result['valid'], result['errors'])
        self.assertEqual(result['metrics']['completed_checks'], 0)

    def test_unknown_applicability_stays_in_denominator(self):
        self.data['checks'][0].update(applicability='unknown', applicability_reason='Missing project conditions', result='blocked')
        result = validate(self.data)
        self.assertTrue(result['valid'])
        self.assertEqual(result['metrics']['applicable_or_unknown_checks'], 1)
        self.assertEqual(result['metrics']['planned_check_completion'], 0)

    def test_not_applicable_requires_reason_and_zero_is_not_complete(self):
        self.data['checks'][0].update(applicability='no', result='not_applicable')
        self.assertFalse(validate(self.data)['valid'])
        self.data['checks'][0]['applicability_reason'] = 'System expressly excluded by project scope'
        result = validate(self.data)
        self.assertTrue(result['valid'])
        self.assertIsNone(result['metrics']['planned_check_completion'])

    def test_missing_scope_plan_and_insufficient_depth(self):
        self.data['scopes'].append(dict(self.data['scopes'][0], scope_id='SYS2'))
        self.assertFalse(validate(self.data)['valid'])
        self.data['scopes'].pop()
        self.data['checks'][0]['achieved_depth'] = 'L1'
        self.assertFalse(validate(self.data)['valid'])

    def test_rule_noncompliance_needs_verified_requirement(self):
        add_issue(self.data, 'verified_noncompliance')
        self.data['evidence'][0]['grade'] = 'A'
        self.assertFalse(validate(self.data)['valid'])
        self.data['rules'][0]['basis_type'] = 'project_requirement'
        self.assertTrue(validate(self.data)['valid'])
        self.data['rules'][0]['verified'] = False
        self.assertFalse(validate(self.data)['valid'])

    def test_confirmed_missing_needs_basis_and_search_scope(self):
        issue = add_issue(self.data, 'confirmed_missing')
        self.data['evidence'][0]['grade'] = 'A'
        self.assertFalse(validate(self.data)['valid'])
        issue.update(missing_basis='Listed in design index', search_scope='All current sheets and references read')
        self.assertTrue(validate(self.data)['valid'])

    def test_closure_requires_successful_recheck_for_every_scope(self):
        issue = add_issue(self.data)
        issue.update(status='closed', closure_basis='Approved response resolves interface')
        self.assertFalse(validate(self.data)['valid'])
        issue.update(closure_evidence_ids=['E1'], recheck_ids=['C1'])
        self.assertFalse(validate(self.data)['valid'])
        self.data['checks'][0].update(result='pass', issue_ids=[])
        self.assertTrue(validate(self.data)['valid'])
        self.data['scopes'].append(dict(self.data['scopes'][0], scope_id='SYS2'))
        self.data['checks'].append(dict(self.data['checks'][0], check_id='C2', scope_id='SYS2'))
        issue['scope_ids'].append('SYS2')
        self.assertFalse(validate(self.data)['valid'])

    def test_malformed_arrays_and_references_report_errors(self):
        for field in ('scope_ids', 'evidence_ids', 'rule_ids'):
            data = copy.deepcopy(self.data)
            issue = add_issue(data)
            issue[field] = {'invalid': 'object'}
            self.assertFalse(validate(data)['valid'])
        self.data['checks'][0]['evidence_ids'] = ['missing-evidence']
        self.assertFalse(validate(self.data)['valid'])


if __name__ == '__main__':
    unittest.main(verbosity=2)
