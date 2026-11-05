import argparse
import contextlib
import importlib.util
import io
import json
import os
from pathlib import Path
import pwd
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch
import install as m

APP_DIR = '' 

class InstallerTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory();self.addCleanup(self.tmp.cleanup)
        self.root=Path(self.tmp.name)
        self.source=self.root/APP_DIR;self.source.mkdir(exist_ok=True)
        self.prefix=self.root/'opt';self.config=self.root/'user'/'config.toml'
        self.units=self.root/'units';self.units.mkdir()
        self.tmpfiles=self.root/'tmpfiles';self.tmpfiles.mkdir()
        self.locks=self.root/'locks';self.locks.mkdir()
        self.account=pwd.getpwuid(os.getuid())
        self.sample=f'vm_owner = "your-user"\nstate_path = "{self.root}/state/state.json"\nlock_path = "{self.locks}/manager.lock"\n'
        (self.source/'config.toml.sample').write_text(self.sample)
        (self.source/'zvm_power_manager.py').write_text('def load_config(path):\n    return {}\n')
        (self.source/m.TIMER).write_text('[Timer]\nOnUnitInactiveSec=30s\n')
        self.active=False;self.enabled=False;self.commands=[];self.fail_verify=False
        self.real_run=m.run
        for name,value in [('SOURCE_DIR',self.source),('UNIT_DIR',self.units),('TMPFILES_DIR',self.tmpfiles),('RUN_LOCK_DIR',self.locks)]:
            patch.object(m,name,value).start()
        patch.object(m.os,'geteuid',return_value=0).start()
        patch.object(m.os,'chown').start();patch.object(m.os,'fchown').start()
        patch.object(m,'run',side_effect=self.fake_run).start()
        self.addCleanup(patch.stopall)

    def args(self,**kw):
        values=dict(with_tools=False,service_name=m.DEFAULT_SERVICE_NAME,user=self.account.pw_name,config=str(self.config),prefix=str(self.prefix),python=sys.executable,update=False,rebuild_venv=False,enable=False,dry_run=False)
        values.update(kw);return argparse.Namespace(**values)

    def fake_run(self,argv,check=True):
        argv=[str(x) for x in argv];self.commands.append(argv)
        if argv[0]=='runuser':
            return subprocess.CompletedProcess(argv,0,'','')
        if argv[0]=='systemctl':
            out=''
            if argv[1]=='show':out='active' if argv[2]==m.TIMER and self.active else 'inactive'
            if argv[1]=='is-enabled':out='enabled' if self.enabled else 'disabled'
            if argv[1]=='stop':self.active=False
            if argv[1]=='start':self.active=True
            if argv[1]=='enable':self.enabled=True
            if argv[1]=='disable':self.enabled=False
            return subprocess.CompletedProcess(argv,0,out,'')
        if argv[0]=='systemd-analyze':
            if self.fail_verify:raise m.InstallError('simulated verification failure')
            return subprocess.CompletedProcess(argv,0,'','')
        if '-m' in argv and 'venv' in argv:
            dest=Path(argv[-1]);(dest/'bin').mkdir(parents=True)
            (dest/'bin/python').symlink_to(sys.executable)
            return subprocess.CompletedProcess(argv,0,'','')
        return self.real_run(argv,check)

    def execute(self,**kw):
        with contextlib.redirect_stdout(io.StringIO()):m.install(self.args(**kw))

    def test_custom_name_install_and_update(self):
        (self.source/m.TIMER).write_text('[Timer]\nOnBootSec=1min\nUnit=' + m.SERVICE + '\n')
        self.execute(service_name='zvmpm-alice')
        self.assertTrue((self.units/'zvmpm-alice.service').exists())
        self.assertIn('Unit=zvmpm-alice.service', (self.units/'zvmpm-alice.timer').read_text())
        self.assertTrue((self.tmpfiles/'zvmpm-alice.conf').exists())
        self.assertFalse((self.units/m.SERVICE).exists())
        self.commands.clear()
        self.execute(service_name='zvmpm-alice', update=True, enable=True)
        self.assertIn(['systemctl', 'enable', 'zvmpm-alice.timer'], self.commands)
        self.assertIn(['systemctl', 'start', 'zvmpm-alice.timer'], self.commands)
        self.assertFalse(any(m.TIMER in cmd or m.SERVICE in cmd for cmd in self.commands if cmd[0]=='systemctl'))

    def test_invalid_service_names(self):
        for name in ('../bad', 'bad.service', 'a b', 'bad@user', ''):
            with self.subTest(name=name), self.assertRaises(m.InstallError):
                self.execute(service_name=name)
        self.assertFalse(self.prefix.exists())

    def test_new_name_cannot_update_another_installation(self):
        self.execute()
        with self.assertRaises(m.InstallError):
            self.execute(service_name='other', update=True)

    def prepare_tools(self):
        for name in ('zvmpm', 'zvmpm_save'):
            (self.source/name).write_text((Path(__file__).parent/name).read_text())

    def test_tools_install_update_and_preserve_without_flag(self):
        self.prepare_tools()
        self.execute(with_tools=True, service_name='custom')
        wrapper = self.prefix/'zvmpm'
        helper = self.prefix/'zvmpm_save'
        self.assertIn(str(self.config), wrapper.read_text())
        self.assertIn('ZVM_POWER_MANAGER_TIMER=custom.timer', helper.read_text())
        self.assertEqual(wrapper.stat().st_mode & 0o777, 0o755)
        wrapper.write_text('local changes')
        self.execute(update=True, service_name='custom')
        self.assertEqual(wrapper.read_text(), 'local changes')
        self.execute(update=True, with_tools=True, service_name='custom')
        self.assertIn(str(self.config), wrapper.read_text())

    def test_tools_rollback(self):
        self.prepare_tools()
        self.execute(with_tools=True)
        wrapper = self.prefix/'zvmpm'
        wrapper.write_text('previous wrapper')
        self.fail_verify=True
        with self.assertRaises(m.InstallError):
            self.execute(update=True, with_tools=True)
        self.assertEqual(wrapper.read_text(), 'previous wrapper')

    def test_tools_dry_run_and_missing_source(self):
        with self.assertRaises(FileNotFoundError):
            self.execute(with_tools=True, dry_run=True)
        self.prepare_tools()
        self.execute(with_tools=True, dry_run=True)
        self.assertFalse(self.prefix.exists())

    def test_fresh_install_no_enable_no_state_file(self):
        self.execute()
        self.assertTrue((self.prefix/'venv/bin/python').exists())
        self.assertIn(self.account.pw_name,self.config.read_text())
        self.assertFalse((self.root/'state/state.json').exists())
        self.assertFalse(self.active);self.assertFalse(self.enabled)
        self.assertIn(str(self.config),(self.units/m.SERVICE).read_text())

    def test_update_preserves_config_state_pending_and_timer(self):
        self.execute();self.active=True;self.enabled=True
        state=self.root/'state/state.json';state.write_text('state')
        pending=state.with_name('state.json.pending');pending.write_text('pending')
        config=self.config.read_bytes()
        (self.source/'zvm_power_manager.py').write_text('def load_config(path):\n    return {}\n# updated\n')
        self.execute(update=True)
        self.assertEqual(self.config.read_bytes(),config)
        self.assertEqual(state.read_text(),'state');self.assertEqual(pending.read_text(),'pending')
        self.assertTrue(self.active);self.assertTrue(self.enabled)
        self.assertIn('# updated',(self.prefix/'zvm_power_manager.py').read_text())

    def test_dry_run_has_no_installation_side_effects(self):
        self.execute(dry_run=True)
        self.assertFalse(self.prefix.exists());self.assertFalse(self.config.exists())
        self.assertEqual(list(self.units.iterdir()),[])
        self.assertFalse(any(c[0]=='systemctl' for c in self.commands))

    def test_failure_rolls_back_and_restores_timer(self):
        self.execute();self.active=True
        old=(self.prefix/'zvm_power_manager.py').read_bytes()
        (self.source/'zvm_power_manager.py').write_text('def load_config(path):\n    return {}\n# new\n')
        self.fail_verify=True
        with self.assertRaises(m.InstallError):self.execute(update=True)
        self.assertEqual((self.prefix/'zvm_power_manager.py').read_bytes(),old)
        self.assertTrue(self.active)

    def test_rebuild_failure_restores_old_venv(self):
        self.execute();marker=self.prefix/'venv/marker';marker.write_text('old')
        self.fail_verify=True
        with self.assertRaises(m.InstallError):self.execute(update=True,rebuild_venv=True)
        self.assertEqual(marker.read_text(),'old')

    def test_enable_is_explicit_and_requires_existing_config(self):
        with self.assertRaises(m.InstallError):self.execute(enable=True)
        self.execute();self.execute(update=True,enable=True)
        self.assertTrue(self.active);self.assertTrue(self.enabled)

    def test_config_path_migration_rejected(self):
        self.execute()
        with self.assertRaises(m.InstallError):self.execute(update=True,config=str(self.root/'other.toml'))

    def test_conflicting_install_rejected(self):
        self.execute()
        with self.assertRaises(m.InstallError):self.execute()

    def test_version_shorthand(self):
        executable,info=m.interpreter('3.12')
        self.assertEqual(info['version'][:2],[3,12])

    def test_active_service_is_not_stopped(self):
        with patch.object(m,'show_property',side_effect=lambda unit,name: 'active' if unit==m.SERVICE else 'inactive'):
            with self.assertRaises(m.InstallError):self.execute()
        self.assertFalse(any(c[:3]==['systemctl','stop',m.SERVICE] for c in self.commands))
        self.assertFalse(self.prefix.exists())

    def test_lock_contention_does_not_stop_timer(self):
        self.execute();self.active=True
        with patch.object(m.fcntl,'flock',side_effect=BlockingIOError('locked')):
            with self.assertRaises(BlockingIOError):self.execute(update=True)
        self.assertTrue(self.active)

    def test_older_python_rejected(self):
        with patch.object(m,'run',return_value=subprocess.CompletedProcess([],0,json.dumps({'version':[3,10,1],'base':sys.executable}),'')):
            with self.assertRaises(m.InstallError):m.interpreter(sys.executable)

    def test_systemd_escaping(self):
        self.assertEqual(m.quote('a%"$b',True),'"a%%\\"$$b"')
        with self.assertRaises(m.InstallError):m.quote('a\nb')




class LockOpeningTests(unittest.TestCase):
    def test_existing_lock_no_create(self):
        with patch.object(m.os, 'open', return_value=42) as opened:
            self.assertEqual(m.open_lock('/run/lock/example.lock'), 42)
        self.assertFalse(opened.call_args.args[1] & os.O_CREAT)
        self.assertTrue(opened.call_args.args[1] & os.O_NOFOLLOW)

    def test_creation_race_reopens_existing(self):
        with patch.object(m.os, 'open', side_effect=[FileNotFoundError(), FileExistsError(), 42]) as opened:
            self.assertEqual(m.open_lock('/run/lock/example.lock'), 42)
        self.assertTrue(opened.call_args_list[1].args[1] & os.O_EXCL)
        self.assertFalse(opened.call_args_list[2].args[1] & os.O_CREAT)

    def test_real_lock_preserves_inode(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / 'lock'
            fd = m.open_lock(path)
            os.close(fd)
            before = path.stat().st_ino
            fd = m.open_lock(path)
            os.close(fd)
            self.assertEqual(path.stat().st_ino, before)


class UnitVerificationTests(unittest.TestCase):
    @unittest.skipUnless(__import__('shutil').which('systemd-analyze'), 'systemd-analyze unavailable')
    def test_generated_units_with_systemd(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            account = pwd.getpwuid(os.getuid())
            for name in ('application', 'application with spaces', 'application%name'):
                prefix = root / name
                (prefix / 'venv/bin').mkdir(parents=True)
                (prefix / 'venv/bin/python').symlink_to(sys.executable)
                service = root / m.SERVICE
                service.write_text(m.service_text(prefix, root / 'config.toml', account, root))
                timer = root / m.TIMER
                timer.write_text('[Unit]\nDescription=Test timer\n[Timer]\nOnBootSec=1min\n')
                result = subprocess.run(['systemd-analyze', 'verify', str(service), str(timer)], text=True, capture_output=True)
                self.assertEqual(result.returncode, 0, result.stderr)


class ToolExecutionTests(unittest.TestCase):
    def test_wrapper_quoting_and_exit_status(self):
        with tempfile.TemporaryDirectory() as directory:
            root=Path(directory)
            config=root / "config ' $HOME $(false).toml"
            scripts=m.tool_scripts(Path(__file__).parent, config, 'custom.timer')
            (root/'zvmpm').write_text(scripts['zvmpm'])
            (root/'venv/bin').mkdir(parents=True)
            python=root/'venv/bin/python'
            python.write_text('#!/bin/bash\nprintf "%s\\n" "$ZVM_POWER_MANAGER_CONFIG" "$@"\nexit 7\n')
            python.chmod(0o755)
            result=subprocess.run(['bash', str(root/'zvmpm'), 'argument with spaces'], text=True, capture_output=True)
            self.assertEqual(result.returncode,7)
            self.assertEqual(result.stdout.splitlines(), [str(config),str(root/'zvm_power_manager.py'),'argument with spaces'])

    def test_save_stops_selected_timer_before_save_and_aborts_on_failure(self):
        with tempfile.TemporaryDirectory() as directory:
            root=Path(directory)
            scripts=m.tool_scripts(Path(__file__).parent, root/'config', 'custom.timer')
            (root/'zvmpm_save').write_text(scripts['zvmpm_save'])
            sudo=root/'sudo'
            sudo.write_text('#!/bin/bash\nprintf "%s\\n" "$*"\nexit "${STOP_RC:-0}"\n')
            sudo.chmod(0o755)
            wrapper=root/'zvmpm'
            wrapper.write_text('#!/bin/bash\nprintf "%s\\n" "$*"\nexit 8\n')
            wrapper.chmod(0o755)
            env=dict(os.environ, PATH=str(root)+':'+os.environ['PATH'])
            result=subprocess.run(['bash',str(root/'zvmpm_save')],env=env,text=True,capture_output=True)
            self.assertEqual(result.returncode,8)
            self.assertEqual(result.stdout.splitlines(),['systemctl stop custom.timer','save --force --execute'])
            env['STOP_RC']='1'
            result=subprocess.run(['bash',str(root/'zvmpm_save')],env=env,text=True,capture_output=True)
            self.assertEqual(result.returncode,1)
            self.assertEqual(result.stdout.splitlines(),['systemctl stop custom.timer'])

if __name__ == '__main__':
    unittest.main()
