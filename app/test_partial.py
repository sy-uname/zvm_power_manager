import json
from pathlib import Path
import subprocess
import unittest
from unittest.mock import patch
import zvm_power_manager as m
from test_execution import ExecutionTests, A, B, C

scan_processes = m.process_inventory

class PartialTests(ExecutionTests):
    def test_two_timeouts_do_not_block_third_and_keep_two_journals(self):
        original=m.mutate_vm
        def fail_two(c,action,ident):
            if ident in (A,B): raise subprocess.TimeoutExpired('VBoxManage',60)
            original(c,action,ident)
        with patch.object(m,'mutate_vm',side_effect=fail_two):
            result=m.execute(self.c,'save')
        self.assertEqual(result['outcome'],'partial_success')
        self.assertEqual([e['uuid'] for e in result['completed']],[C])
        self.assertEqual({e['uuid'] for e in result['failed']},{A,B})
        self.assertEqual(set(m.pending_journals(self.c)),{A,B})
        self.assertEqual([e['uuid'] for e in self.entries()],[C])
        # Both timed-out server-side operations have subsequently finished.
        self.states[A]=self.states[B]='saved'
        preview=m.recover(self.c)
        self.assertEqual({e['uuid'] for e in preview['saved_by_service']},{A,B,C})
        self.assertEqual([e['uuid'] for e in self.entries()],[C])
        result=m.recover(self.c,True)
        self.assertFalse(result['failed'])
        self.assertEqual({e['uuid'] for e in self.entries()},{A,B,C})
        self.assertEqual(m.pending_journals(self.c),{})

    def test_existing_pending_blocks_only_affected_vm(self):
        path=Path(self.c['state_path']+'.pending')
        path.write_text(json.dumps({'action':'save','uuid':A,'name':'WebServer'}))
        before=path.read_bytes()
        result=m.execute(self.c,'save',True)
        self.assertEqual([e['uuid'] for e in result['completed']],[B,C])
        self.assertEqual(path.read_bytes(),before)
        self.assertTrue(any(e['uuid']==A for e in result['skipped']))

    def test_invisible_process_warns_and_other_vms_save(self):
        self.states[A]='poweroff'
        with patch.object(m,'process_inventory',return_value=({A},[])):
            plan=m.inspect(self.c,'save')
            result=m.execute(self.c,'save')
        self.assertEqual(plan['warnings'][0]['uuid'],A)
        self.assertEqual([e['uuid'] for e in result['completed']],[B,C])
        self.assertEqual(result['outcome'],'partial_success')
        self.assertNotIn(A,[args[1] for args in self.mutations])

    def test_invisible_restore_never_cold_starts(self):
        self.states[A]='saved'
        self.states[B]='saved'
        m.write_state(self.c['state_path'],[{'uuid':i,'name':self.names[i]} for i in (A,B)])
        with patch.object(m,'process_inventory',return_value=({A},[])):
            result=m.execute(self.c,'restore',True)
        self.assertEqual([e['uuid'] for e in result['completed']],[B])
        self.assertEqual([e['uuid'] for e in self.entries()],[A])

    def test_state_check_error_continues_without_journal_for_unattempted(self):
        original=m.vm_state
        def state(c,ident):
            if ident==A: raise m.ValidationError('VM inaccessible')
            return original(c,ident)
        with patch.object(m,'vm_state',side_effect=state):
            result=m.execute(self.c,'save')
        self.assertEqual([e['uuid'] for e in result['completed']],[B,C])
        self.assertEqual(result['failed'][0]['uuid'],A)
        self.assertEqual(m.pending_journals(self.c),{})

    def test_continue_one_unresolved_does_not_block_other_retry(self):
        base=Path(self.c['state_path']+'.pending')
        for ident,path in ((A,base),(B,Path(str(base)+'.'+B))):
            path.write_text(json.dumps({'action':'save','uuid':ident,'name':self.names[ident]}))
        self.states[A]='saving'
        result=m.execute(self.c,'save',True,True)
        self.assertEqual([e['uuid'] for e in result['completed']],[B,C])
        self.assertEqual(set(m.pending_journals(self.c)),{A})

    def test_recovery_one_unresolved_does_not_block_other_commit(self):
        base=Path(self.c['state_path']+'.pending')
        for ident,path in ((A,base),(B,Path(str(base)+'.'+B))):
            path.write_text(json.dumps({'action':'save','uuid':ident,'name':self.names[ident]}))
        self.states[A]='saving';self.states[B]='saved'
        result=m.recover(self.c,True)
        self.assertEqual([e['uuid'] for e in self.entries()],[B])
        self.assertEqual(result['failed'][0]['uuid'],A)
        self.assertEqual(set(m.pending_journals(self.c)),{A})

    def test_shared_journal_write_error_stops_before_mutation(self):
        original=m.atomic_json
        def write(path,value):
            if '.pending' in str(path): raise OSError('disk full')
            original(path,value)
        with patch.object(m,'atomic_json',side_effect=write):
            with self.assertRaises(OSError):m.execute(self.c,'save')
        self.assertEqual(self.mutations,[])

    def test_process_scanner_uses_only_vm_frontends(self):
        proc=self.root/'proc'; proc.mkdir()
        for pid,comm,args in [('123','VBoxHeadless',['/usr/lib/virtualbox/VBoxHeadless','--startvm',A]),
                              ('124','VirtualBoxVM',['VirtualBoxVM','--startvm',B]),
                              ('125','other',['other','--startvm',C])]:
            d=proc/pid;d.mkdir();(d/'comm').write_text(comm+'\n')
            (d/'cmdline').write_bytes(b'\0'.join(x.encode() for x in args))
        with patch.object(m.LC,'PROC_ROOT',str(proc)):
            found,warnings=scan_processes()
        self.assertEqual(found,{A,B});self.assertEqual(warnings,[])

    def test_plan_explains_power_and_filter(self):
        self.c['include_other_running_vms']=False
        self.power={'capacity':100,'ac_online':True}
        result=m.inspect(self.c,'save')
        self.assertEqual(len(result['running_vms']),3)
        self.assertEqual([e['uuid'] for e in result['save_queue']],[A,B])
        self.assertEqual(result['planned_queue'],[])
        self.assertEqual(result['excluded_vms'][0]['reason'],'excluded_by_save_order')
        self.assertIn('Power gate: False',result['summary'])


def load_tests(loader, tests, pattern):
    return unittest.TestSuite(PartialTests(name) for name in PartialTests.__dict__ if name.startswith('test_'))
