from pathlib import Path
import json
import unittest
from unittest.mock import patch
import zvm_power_manager as m
from test_execution import ExecutionTests, A, B, C

class RecoveryTests(ExecutionTests):
    def setUp(self):
        super().setUp()
        patch.object(m, 'assert_no_active_operation').start()
        self.pending=Path(self.c['state_path']+'.pending')

    def journal(self, action='save', ident=A):
        self.pending.write_text(json.dumps({'action':action,'uuid':ident,'name':self.names[ident]}))

    def test_recover_saved_preserves_other_entries_and_is_idempotent(self):
        m.write_state(self.c['state_path'],[{'uuid':C,'name':'Other'}])
        self.journal()
        self.states[A]='saved'
        before=self.pending.read_bytes()
        r=m.recover(self.c)
        self.assertEqual(r['recovery'][0]['resolution'],'resolved')
        self.assertEqual(self.pending.read_bytes(),before)
        self.assertEqual([e['uuid'] for e in self.entries()],[C])
        m.recover(self.c,True)
        self.assertEqual([e['uuid'] for e in self.entries()],[C,A])
        self.assertFalse(self.pending.exists())
        self.assertEqual(m.recover(self.c,True)['recovery'],[])
        self.assertEqual(self.mutations,[])

    def test_recover_running_restore_removes_only_target(self):
        m.write_state(self.c['state_path'],[{'uuid':i,'name':self.names[i]} for i in (A,B)])
        self.journal('restore')
        m.recover(self.c,True)
        self.assertEqual([e['uuid'] for e in self.entries()],[B])
        self.assertEqual(self.mutations,[])

    def test_recover_incomplete_keeps_journal(self):
        self.journal()
        before=self.pending.read_bytes()
        self.assertEqual(m.recover(self.c,True)['recovery'][0]['resolution'],'retry_required')
        self.assertEqual(self.pending.read_bytes(),before)
        self.assertEqual(self.mutations,[])

    def test_continue_retries_problem_vm_first(self):
        self.journal(ident=B)
        r=m.execute(self.c,'save',True,continue_mode=True)
        self.assertEqual([e['uuid'] for e in r['completed']],[B,A,C])
        self.assertFalse(self.pending.exists())

    def test_continue_commits_previous_success_then_remaining(self):
        self.journal()
        self.states[A]='saved'
        r=m.execute(self.c,'save',True,continue_mode=True)
        self.assertEqual([e['uuid'] for e in r['completed']],[B,C])
        self.assertEqual([e['uuid'] for e in self.entries()],[A,B,C])

    def test_continue_restore_retry_and_preserve(self):
        m.write_state(self.c['state_path'],[{'uuid':i,'name':self.names[i]} for i in (A,B)])
        self.states[A]=self.states[B]='saved'
        self.journal('restore')
        r=m.execute(self.c,'restore',True,continue_mode=True)
        self.assertEqual([e['uuid'] for e in r['completed']],[A,B])
        self.assertEqual(self.entries(),[])

    def test_mismatch_transitional_and_malformed_block(self):
        self.journal()
        before=self.pending.read_bytes()
        self.assertTrue(m.execute(self.c,'restore',True,continue_mode=True)['failed'])
        self.assertEqual(self.pending.read_bytes(),before)
        for state in ('saving','restoring','poweroff'):
            self.states[A]=state
            self.assertTrue(m.recover(self.c,True)['failed'])
            self.assertEqual(self.pending.read_bytes(),before)
        self.pending.write_text('{')
        with self.assertRaises(m.ValidationError):m.recover(self.c,True)
        self.assertEqual(self.pending.read_text(),'{')
        self.assertEqual(self.mutations,[])

    def test_recovery_commit_failure_keeps_journal(self):
        self.journal(); self.states[A]='saved'
        with patch.object(m,'write_state',side_effect=OSError('disk failure')):
            with self.assertRaises(OSError):m.recover(self.c,True)
        self.assertTrue(self.pending.exists())
        self.assertEqual(self.mutations,[])

    def test_retry_respects_filter_and_power(self):
        self.journal(ident=C)
        self.states[A]=self.states[B]='poweroff'
        self.c['include_other_running_vms']=False
        self.assertTrue(m.execute(self.c,'save',True,continue_mode=True)['pending_operations'])
        self.assertTrue(self.pending.exists())
        self.c['include_other_running_vms']=True
        self.power={'capacity':100,'ac_online':True}
        r=m.execute(self.c,'save',continue_mode=True)
        self.assertIsNotNone(r['stopped_reason'])
        self.assertTrue(self.pending.exists())
        self.assertEqual(self.mutations,[])

    def test_error_metadata(self):
        self.fail_vm=A
        m.execute(self.c,'save')
        data=json.loads(self.pending.read_text())
        self.assertIn('started_at',data)
        self.assertIn('timed out',data['last_error'])
        self.assertIn('updated_at',data)

    def test_inconsistent_observation_blocks(self):
        self.journal()
        with patch.object(m,'vm_state',side_effect=['running','saved']):
            self.assertTrue(m.recover(self.c,True)['failed'])
        self.assertTrue(self.pending.exists())

if __name__=='__main__':unittest.main()

# Reuse the isolated fake-host fixture without rerunning inherited tests.
def load_tests(loader, tests, pattern):
    return unittest.TestSuite(RecoveryTests(name) for name in RecoveryTests.__dict__ if name.startswith('test_'))
