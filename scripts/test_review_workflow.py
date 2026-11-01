"""Behavioral regression tests for task aggregation and transactional publication."""
import copy
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from test_validate_review import fixture, add_issue
from validate_review import validate
import review_workflow as w


class WorkflowTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory();self.addCleanup(self.tmp.cleanup)
        self.root=Path(self.tmp.name);self.project=self.root/'project';self.round=self.project/'R1';self.round.mkdir(parents=True)
        self.src=self.root/'source.txt';self.src.write_text('synthetic',encoding='utf-8')
        self.data=fixture(self.src);self.review=self.round/'review.json';self.qa=self.round/'qa.json'
        self.image=self.round/'evidence'/'note.txt';self.image.parent.mkdir();self.image.write_text('evidence',encoding='utf-8')
        self.data['evidence'][0]['image']='evidence/note.txt';self.save()

    def save(self):w.write(self.review,self.data)
    def ready(self):
        self.save();q=w.prepare_qa(self.review,self.qa);q.update(status='reviewed',reviewed_by='Synthetic test reviewer',evidence_ids_checked=['E1'],limitations=['Synthetic fixtures only']);w.write(self.qa,q)
    def tasks(self):
        self.data['tasks']=[dict(task_id='T0',label='Whole scope',task_ids=['T1']),dict(task_id='T1',label='System',check_ids=['C1'])]

    def test_task_status_derived_without_double_count(self):
        self.tasks();v=validate(self.data);self.assertTrue(v['valid'],v['errors']);self.assertEqual(v['metrics']['completed_checks'],1)
        self.assertEqual(v['metrics']['task_summaries']['T0']['status'],'complete')
        self.data['checks'][0].update(result='pending',review_status='pending',achieved_depth='L1')
        v=validate(self.data);self.assertEqual(v['metrics']['completed_checks'],0);self.assertEqual(v['metrics']['task_summaries']['T0']['status'],'in_progress')

    def test_task_cycle_and_multiple_parents_rejected(self):
        self.tasks();self.data['tasks'][1]['task_ids']=['T0'];self.assertFalse(validate(self.data)['valid'])
        self.tasks();self.data['tasks'][0]['check_ids']=['C1'];self.assertFalse(validate(self.data)['valid'])

    def test_task_missing_check_unknown_child_and_manual_status(self):
        self.tasks();self.data['tasks'][1]['check_ids']=['BAD'];self.assertFalse(validate(self.data)['valid'])
        self.tasks();self.data['tasks'][0]['status']='complete';self.assertFalse(validate(self.data)['valid'])
        self.tasks();self.data['tasks'][1]['task_ids']=[{}];self.assertFalse(validate(self.data)['valid'])

    def test_not_applicable_task_not_counted_complete(self):
        self.tasks();self.data['checks'][0].update(result='not_applicable',applicability='no',applicability_reason='Excluded')
        v=validate(self.data);self.assertTrue(v['valid']);self.assertEqual(v['metrics']['task_summaries']['T0']['status'],'not_applicable');self.assertIsNone(v['metrics']['task_summaries']['T0']['completion'])

    def test_candidate_evidence_cannot_support_pass(self):
        self.data['evidence'][0]['grade']='C';self.assertFalse(validate(self.data)['valid'])

    def test_required_evidence_capability(self):
        self.data['rules'][0]['required_evidence_checks']=['identity','visible']
        self.assertFalse(validate(self.data)['valid'])
        self.data['evidence'][0]['verification']={'identity':True,'visible':False};self.assertFalse(validate(self.data)['valid'])
        self.data['evidence'][0]['verification']['visible']=True;self.assertTrue(validate(self.data)['valid'])

    def test_shared_origin_not_independent(self):
        i=add_issue(self.data,'confirmed_conflict');self.data['evidence'][0]['origin_group']='same-definition'
        self.data['evidence'].append(dict(self.data['evidence'][0],evidence_id='E2',locator='another drawing',independence_key='different-id'));i['evidence_ids'].append('E2')
        self.assertFalse(validate(self.data)['valid'])

    def test_closure_requires_original_rule_and_objects(self):
        i=add_issue(self.data);i.update(status='closed',closure_basis='Rechecked',closure_evidence_ids=['E1'],recheck_ids=['C1'],object_ids=['pipe1'])
        self.data['checks'][0].update(result='pass',issue_ids=[],object_ids=['pipe2']);self.assertFalse(validate(self.data)['valid'])
        self.data['checks'][0]['object_ids']=['pipe1'];self.assertTrue(validate(self.data)['valid'])
        self.data['rules'].append(dict(self.data['rules'][0],rule_id='R2'));self.data['checks'][0]['rule_id']='R2';self.assertFalse(validate(self.data)['valid'])

    def test_relation_references_and_artifacts_checked(self):
        self.data['relations']=[dict(relation_id='X',from_object='a',to_object='b',relation_type='index_reference',scope_id='SYS1',certainty='candidate',evidence_ids=['bad'],version_dependencies=['SRC1'])]
        self.assertFalse(validate(self.data)['valid']);self.data['relations'][0]['evidence_ids']=['E1'];self.assertTrue(validate(self.data,review_dir=self.round)['valid'])
        self.image.unlink();self.assertFalse(validate(self.data,review_dir=self.round)['valid'])

    def test_artifact_escape_rejected(self):
        self.data['evidence'][0]['image']='../../source.txt';self.assertFalse(validate(self.data,review_dir=self.round)['valid'])

    def test_checkpoint_roundtrip_stale_revision_and_bad_artifact(self):
        ptr=w.checkpoint(self.project,dict(work_round='R1',current_object='pipe',work_artifacts=['R1/review.json']),None)
        state=w.load_state(self.project);self.assertEqual(state['current_object'],'pipe')
        with self.assertRaises(ValueError):w.checkpoint(self.project,dict(current_object='stale'),None)
        with self.assertRaises(ValueError):w.checkpoint(self.project,dict(work_artifacts=['missing']),ptr['revision'])
        self.assertEqual(w.load_state(self.project)['revision'],ptr['revision'])

    def test_prepare_qa_not_auto_approved(self):
        q=w.prepare_qa(self.review,self.qa);self.assertEqual(q['status'],'pending')
        with self.assertRaises(ValueError):w.publish(self.project,self.review,self.qa,None)
        self.assertFalse((self.project/'workflow.json').exists())

    def test_publish_immutable_snapshot_and_tamper_detection(self):
        self.ready();self.tasks();self.save()
        # Prior QA cannot approve changed review bytes.
        with self.assertRaises(ValueError):w.publish(self.project,self.review,self.qa,None)
        q=w.read(self.qa);q['review_sha256']=w.digest(self.review);w.write(self.qa,q)
        ptr=w.publish(self.project,self.review,self.qa,None);state=w.load_state(self.project);snap=Path(state['latest_validated']['review'])
        self.assertNotEqual(snap,self.review);self.assertTrue(snap.is_file());self.assertFalse(w.status(self.project)['integrity_warnings'])
        self.image.write_text('working changes',encoding='utf-8');self.assertEqual((snap.parent/'evidence/note.txt').read_text(),'evidence')
        (snap.parent/'checks.md').write_text('tampered',encoding='utf-8');self.assertTrue(w.status(self.project)['integrity_warnings'])

    def test_evidence_change_after_qa_rejects_publish(self):
        self.ready();self.image.write_text('changed',encoding='utf-8')
        with self.assertRaises(ValueError):w.publish(self.project,self.review,self.qa,None)
        self.assertIsNone(w.load_state(self.project))

    def test_source_change_rejects_publish(self):
        self.ready();self.src.write_text('new version',encoding='utf-8')
        with self.assertRaises(ValueError):w.publish(self.project,self.review,self.qa,None)

    def test_report_failure_preserves_previous_pointer(self):
        self.ready();ptr=w.publish(self.project,self.review,self.qa,None);before=(self.project/'workflow.json').read_bytes()
        with patch.object(w,'reports',side_effect=OSError('disk failure')):
            with self.assertRaises(OSError):w.publish(self.project,self.review,self.qa,ptr['revision'])
        self.assertEqual((self.project/'workflow.json').read_bytes(),before)

    def test_pointer_commit_failure_preserves_previous_pointer(self):
        ptr=w.checkpoint(self.project,dict(current_object='old'),None);before=(self.project/'workflow.json').read_bytes();original=w.os.replace
        def fail(src,dst):
            if Path(dst).name=='workflow.json':raise OSError('commit failure')
            return original(src,dst)
        with patch.object(w.os,'replace',side_effect=fail):
            with self.assertRaises(OSError):w.checkpoint(self.project,dict(current_object='new'),ptr['revision'])
        self.assertEqual((self.project/'workflow.json').read_bytes(),before)

    def test_high_issue_requires_all_evidence_reviewed(self):
        i=add_issue(self.data,'confirmed_conflict');self.data['evidence'][0]['grade']='A'
        self.ready();q=w.read(self.qa);q['evidence_ids_checked']=[];w.write(self.qa,q)
        with self.assertRaises(ValueError):w.publish(self.project,self.review,self.qa,None)

    def test_lock_prevents_concurrent_writer(self):
        with w.lock(self.project):
            with self.assertRaises(ValueError):w.checkpoint(self.project,dict(current_object='concurrent'),None)

    def test_legacy_registration_needs_report_and_preserves_work_state(self):
        self.ready();w.write(self.qa,dict(valid=True))
        with self.assertRaises(ValueError):w.register_baseline(self.project,self.review,self.qa,None)
        w.write(self.round/'report.html','legacy report');ptr=w.checkpoint(self.project,dict(work_round='R1',next_actions=['Continue unfinished work']),None)
        ptr=w.register_baseline(self.project,self.review,self.qa,ptr['revision']);state=w.load_state(self.project)
        self.assertEqual(state['latest_validated']['kind'],'legacy_baseline');self.assertEqual(state['next_actions'],['Continue unfinished work'])
        with self.assertRaises(ValueError):w.register_baseline(self.project,self.review,self.qa,ptr['revision'])

if __name__=='__main__':unittest.main(verbosity=2)
