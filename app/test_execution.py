import json
import contextlib
import io
from pathlib import Path
import subprocess
import tempfile
import unittest
from unittest.mock import patch
import zvm_power_manager as m

A = '00000000-0000-0000-0000-000000000001'
B = '00000000-0000-0000-0000-000000000002'
C = '00000000-0000-0000-0000-000000000003'


class ExecutionTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.c = m.load_config(Path(__file__).with_name('config.toml'))
        self.c.update(state_path=str(self.root/'state.json'), lock_path=str(self.root/'lock'))
        self.names = {C:'Other', B:'lUbuntu-MariaDB', A:'WebServer'}
        self.states = {k:'running' for k in self.names}
        self.mutations = []
        self.fail_vm = None
        self.power = {'capacity':30, 'ac_online':False}
        self.addCleanup(patch.stopall)
        patch.object(m, 'process_inventory', return_value=(set(), [])).start()
        patch.object(m, 'assert_no_active_operation').start()
        patch.object(m.subprocess, 'run', side_effect=self.fake_vbox).start()
        patch.object(m, 'read_power', side_effect=lambda c: self.power.copy()).start()

    def fake_vbox(self, argv, **kwargs):
        args = argv[1:]
        if args == ['list','vms']:
            out = '\n'.join(f'"{v}" {{{k}}}' for k,v in self.names.items())
        elif args == ['list','runningvms']:
            out = '\n'.join(f'"{v}" {{{k}}}' for k,v in self.names.items() if self.states[k]=='running')
        elif args[0] == 'showvminfo':
            self.assertEqual(args[2:], ['--machinereadable'])
            out = f'VMState="{self.states[args[1]]}"\n'
        else:
            self.assertIn(args[0], ['controlvm','startvm'])
            self.mutations.append(args)
            if self.fail_vm == args[1]:
                raise subprocess.TimeoutExpired(argv, 20)
            if args[0] == 'controlvm':
                self.assertEqual(args[2:], ['savestate'])
                self.states[args[1]] = 'saved'
            else:
                self.assertEqual(args[2:], ['--type','headless'])
                self.states[args[1]] = 'running'
            out = ''
        return subprocess.CompletedProcess(argv,0,out,'')

    def entries(self):
        return m.read_state(self.c['state_path'])[0]

    def test_save_then_restore_order_and_state(self):
        r = m.execute(self.c,'save')
        self.assertEqual(r['executed_actions'],3)
        self.assertEqual([e['uuid'] for e in self.entries()],[A,B,C])
        self.power = {'capacity':40,'ac_online':True}
        r = m.execute(self.c,'restore')
        self.assertEqual([e['uuid'] for e in r['completed']],[B,A,C])
        self.assertEqual(self.entries(),[])
        self.assertFalse(Path(self.c['state_path']+'.pending').exists())

    def test_failure_preserves_completed_and_pending(self):
        self.fail_vm = B
        r = m.execute(self.c,'save')
        self.assertEqual(r['executed_actions'],2)
        self.assertTrue(r['error'])
        self.assertEqual([e['uuid'] for e in self.entries()],[A,C])
        pending=Path(self.c['state_path']+'.pending')
        self.assertEqual(json.loads(pending.read_text())['uuid'], B)
        before=len(self.mutations)
        self.assertTrue(m.execute(self.c,'save',True)['error'])
        self.assertEqual(len(self.mutations),before)

    def test_power_change_stops_remaining(self):
        original=m.mutate_vm
        def change(c,action,ident):
            original(c,action,ident)
            self.power={'capacity':97,'ac_online':True}
        with patch.object(m,'mutate_vm',side_effect=change):
            r=m.execute(self.c,'save')
        self.assertEqual(r['executed_actions'],1)
        self.assertIsNotNone(r['stopped_reason'])
        self.assertEqual(len(self.entries()),1)

    def test_force_keeps_filter(self):
        self.power={'capacity':97,'ac_online':True}
        self.c['include_other_running_vms']=False
        r=m.execute(self.c,'save',True)
        self.assertEqual([e['uuid'] for e in r['completed']],[A,B])

    def test_restore_requires_saved_state_even_with_force(self):
        self.states[A]='poweroff'
        m.write_state(self.c['state_path'],[{'uuid':A,'name':'WebServer'}])
        r=m.execute(self.c,'restore',True)
        self.assertIsNotNone(r['error'])
        self.assertEqual(self.mutations,[])
        self.assertEqual(len(self.entries()),1)

    def test_restore_failure_preserves_unfinished_entries(self):
        m.execute(self.c,'save')
        self.mutations=[]
        self.fail_vm=A
        r=m.execute(self.c,'restore',True)
        self.assertEqual([e['uuid'] for e in r['completed']],[B,C])
        self.assertEqual([e['uuid'] for e in self.entries()],[A])

    def test_lock_contention(self):
        with m.operation_lock(self.c['lock_path']):
            with self.assertRaises(m.ValidationError): m.execute(self.c,'save',True)
        self.assertEqual(self.mutations,[])

    def test_atomic_replace_failure_keeps_old_state(self):
        m.write_state(self.c['state_path'],[])
        before=Path(self.c['state_path']).read_bytes()
        with patch.object(m.os,'replace',side_effect=OSError('disk failure')):
            with self.assertRaises(OSError): m.write_state(self.c['state_path'],[{'uuid':A,'name':'WebServer'}])
        self.assertEqual(Path(self.c['state_path']).read_bytes(),before)
        self.assertEqual(list(self.root.glob('.zvm-*')),[])

    def test_commit_failure_leaves_recovery_journal(self):
        original=m.write_state
        count=0
        def fail_commit(path,entries):
            nonlocal count
            count+=1
            if count==2: raise OSError('disk failure')
            original(path,entries)
        with patch.object(m,'write_state',side_effect=fail_commit):
            with self.assertRaises(OSError):
                m.execute(self.c,'save')
        self.assertEqual(self.states[A],'saved')
        self.assertTrue(Path(self.c['state_path']+'.pending').exists())
        self.assertEqual(len(self.mutations),1)

    def test_dry_run_never_creates_lock_or_state(self):
        m.inspect(self.c,'save',True)
        self.assertEqual(list(self.root.iterdir()),[])
        self.assertEqual(self.mutations,[])

    def test_cli_execution_is_explicit(self):
        for action in ('save', 'restore'):
            with patch.object(m, 'inspect', return_value={}) as preview, patch.object(m, 'execute', return_value={}) as execute:
                with contextlib.redirect_stdout(io.StringIO()):
                    self.assertEqual(m.main([action, '--dry-run', '--force']), 0)
                preview.assert_called_once()
                execute.assert_not_called()
                with contextlib.redirect_stdout(io.StringIO()):
                    self.assertEqual(m.main([action, '--execute', '--force']), 0)
                execute.assert_called_once()
            with contextlib.redirect_stderr(io.StringIO()), self.assertRaises(SystemExit) as exc:
                m.main([action, '--dry-run', '--execute'])
            self.assertEqual(exc.exception.code, 2)

    def test_bad_state_and_wrong_owner_block_force(self):
        Path(self.c['state_path']).write_text('{')
        with self.assertRaises(m.ValidationError): m.execute(self.c,'save',True)
        self.c['vm_owner']='not-the-owner'
        with self.assertRaises(m.ValidationError): m.execute(self.c,'save',True)
        self.assertEqual(self.mutations,[])

if __name__ == '__main__':
    unittest.main()
