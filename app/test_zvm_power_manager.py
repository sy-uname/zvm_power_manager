import contextlib
import io
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch
import zvm_power_manager as m

A = '00000000-0000-0000-0000-000000000001'
B = '00000000-0000-0000-0000-000000000002'
C = '00000000-0000-0000-0000-000000000003'
D = '00000000-0000-0000-0000-000000000004'


class Tests(unittest.TestCase):
    def setUp(self):
        self.c = m.load_config(Path(__file__).with_name('config.toml'))

    def plan(self, capacity, ac, running=None, saved=None, action='auto', force=False):
        inventory = {C: 'Other', B: 'lUbuntu-MariaDB', A: 'WebServer'}
        with patch.object(m, 'read_power', return_value=dict(capacity=capacity, ac_online=ac)), \
             patch.object(m, 'vbox', side_effect=[inventory, inventory if running is None else running]), \
             patch.object(m, 'read_state', return_value=(saved or [], True)):
            return m.inspect(self.c, action, force)

    def test_thresholds(self):
        for charge, ac, expected in [(30, False, 'save'), (31, False, 'none'),
                                     (39, True, 'none'), (40, True, 'restore'),
                                     (0, True, 'none'), (100, False, 'none')]:
            with self.subTest(charge=charge, ac=ac):
                self.assertEqual(self.plan(charge, ac)['selected_action'], expected)

    def test_save_order(self):
        self.assertEqual([x['uuid'] for x in self.plan(30, False)['planned_queue']], [A, B, C])

    def test_exclude_other_running_vms(self):
        self.c['include_other_running_vms'] = False
        self.assertEqual([x['uuid'] for x in self.plan(30, False)['planned_queue']], [A, B])
        forced = self.plan(97, True, action='save', force=True)
        self.assertEqual([x['uuid'] for x in forced['planned_queue']], [A, B])
        self.c['save_order'] = []
        self.assertEqual(self.plan(30, False)['save_queue'], [])
        self.c['include_other_running_vms'] = True
        self.assertEqual(len(self.plan(30, False)['save_queue']), 3)

    def test_save_filter_does_not_filter_service_restore_list(self):
        self.c['include_other_running_vms'] = False
        result = self.plan(40, True, running={}, saved=[dict(uuid=C, name='Other')])
        self.assertEqual([x['uuid'] for x in result['planned_queue']], [C])

    def test_include_other_running_vms_requires_boolean(self):
        source = Path(__file__).with_name('config.toml').read_text()
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / 'config.toml'
            for value in ('1', '"true"', '[]'):
                path.write_text(source.replace('include_other_running_vms = true',
                                               f'include_other_running_vms = {value}'))
                with self.assertRaises(m.ValidationError) as exc:
                    m.load_config(path)
                self.assertIn('must be a boolean', str(exc.exception))

    def test_restore_and_force_scope(self):
        saved = [dict(uuid=x, name=n) for x, n in [(C, 'Other'), (A, 'WebServer'), (B, 'lUbuntu-MariaDB'), (D, 'Missing')]]
        r = self.plan(1, False, running={C: 'Other'}, saved=saved, action='restore', force=True)
        self.assertEqual([x['uuid'] for x in r['planned_queue']], [B, A])
        self.assertEqual(len(r['restore_skipped']), 2)
        self.assertEqual(r['executed_actions'], 0)
        self.assertEqual(self.plan(100, True, running={}, action='restore', force=True)['planned_queue'], [])

    def test_power_gate(self):
        self.assertEqual(self.plan(97, True, action='save')['planned_queue'], [])
        self.assertEqual(len(self.plan(97, True, action='save', force=True)['planned_queue']), 3)

    def test_sysfs_and_state_read_only(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            for name, content in [('capacity', '30'), ('online', '0'), ('status', 'Discharging')]:
                (root / name).write_text(content)
            c = dict(self.c, battery_path=tmp, ac_path=tmp)
            before = {p.name: p.read_bytes() for p in root.iterdir()}
            self.assertEqual(m.read_power(c)['capacity'], 30)
            self.assertEqual(m.read_state(root / 'absent'), ([], False))
            self.assertEqual(before, {p.name: p.read_bytes() for p in root.iterdir()})
            (root / 'online').write_text('2')
            with self.assertRaises(m.ValidationError):
                m.read_power(c)
            state = root / 'state.json'
            state.write_text(json.dumps(dict(schema_version=1, saved_by_service=[dict(uuid=A, name='WebServer')])))
            original = state.read_bytes()
            self.assertEqual(m.read_state(state)[0][0]['uuid'], A)
            self.assertEqual(original, state.read_bytes())
            state.write_text('{')
            with self.assertRaises(m.ValidationError):
                m.read_state(state)

    def test_empty_state_is_absent_and_unchanged(self):
        with tempfile.TemporaryDirectory() as tmp:
            state = Path(tmp) / 'state.json'
            for content in (b'', b' \n\t\r\n'):
                state.write_bytes(content)
                self.assertEqual(m.read_state(state), ([], False))
                self.assertEqual(state.read_bytes(), content)

    def test_invalid_state_has_context_and_is_unchanged(self):
        with tempfile.TemporaryDirectory() as tmp:
            state = Path(tmp) / 'state.json'
            cases = [(b'{', 'line 1, column 2'),
                     (b'garbage', 'Invalid JSON'),
                     (b'{}', 'Invalid state fields'),
                     (b'[]', 'Invalid state fields'),
                     (b'{"schema_version": 2, "saved_by_service": []}', 'Unsupported state schema'),
                     (b'\xff', 'UTF-8')]
            for content, message in cases:
                with self.subTest(content=content):
                    state.write_bytes(content)
                    with self.assertRaises(m.ValidationError) as exc:
                        m.read_state(state)
                    self.assertIn(str(state), str(exc.exception))
                    self.assertIn(message, str(exc.exception))
                    self.assertEqual(state.read_bytes(), content)

    def test_cli_rejects_mutations(self):
        for args in [['save'], ['restore'], ['check'], ['status', '--force'], ['check', '--dry-run', '--force']]:
            with self.subTest(args=args), contextlib.redirect_stderr(io.StringIO()):
                with self.assertRaises(SystemExit) as exc:
                    m.main(args)
                self.assertEqual(exc.exception.code, 2)

    def test_config_validation(self):
        source = Path(__file__).with_name('config.toml').read_text()
        for bad in [source.replace('save_threshold = 30', 'save_threshold = true'),
                    source.replace('restore_threshold = 40', 'restore_threshold = 20'),
                    source + '\nunknown = 1\n', source.replace('"headless"', '"gui"'),
                    source.replace('/run/lock/zvm-power-manager.lock', 'relative.lock')]:
            with tempfile.TemporaryDirectory() as tmp:
                p = Path(tmp) / 'config.toml'
                p.write_text(bad)
                with self.assertRaises(m.ValidationError):
                    m.load_config(p)

    def test_vbox_allowlist(self):
        with patch.object(m.subprocess, 'run') as run:
            with self.assertRaises(m.ValidationError):
                m.vbox(self.c, 'controlvm', A, 'savestate')
            run.assert_not_called()
        self.assertEqual(m.parse_vms(f'"VM with spaces" {{{A}}}'), {A: 'VM with spaces'})
        with self.assertRaises(m.ValidationError):
            m.parse_vms('invalid')


if __name__ == '__main__':
    unittest.main()
