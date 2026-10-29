import contextlib
import io
import unittest
from unittest.mock import patch
import zvm_power_manager as m

class AutomaticTests(unittest.TestCase):
    def setUp(self):
        self.c = {m.LC.SAVE_THRESHOLD:30, m.LC.RESTORE_THRESHOLD:40}

    def test_boundaries_and_no_force_or_continue(self):
        for ac, capacity, expected in [(False,30,'save'),(False,31,None),(True,39,None),(True,40,'restore'),(True,0,None),(False,100,None)]:
            with self.subTest(ac=ac,capacity=capacity), patch.object(m,'read_power',return_value={'capacity':capacity,'ac_online':ac}), patch.object(m,'execute',return_value={}) as execute:
                r=m.automatic(self.c,True)
                if expected: execute.assert_called_once_with(self.c,expected)
                else:
                    execute.assert_not_called()
                    self.assertEqual(r['executed_actions'],0)

    def test_preview_only(self):
        with patch.object(m,'inspect',return_value={}) as inspect, patch.object(m,'execute') as execute:
            m.automatic(self.c)
            inspect.assert_called_once_with(self.c)
            execute.assert_not_called()

    def test_cli_requires_mode_and_rejects_force_continue(self):
        for args in [['auto'],['auto','--execute','--force'],['auto','--execute','--continue'],['auto','--execute','--dry-run']]:
            with self.subTest(args=args),contextlib.redirect_stderr(io.StringIO()), self.assertRaises(SystemExit) as e:
                m.main(args)
            self.assertEqual(e.exception.code,2)

    def test_cli_dispatch(self):
        with patch.object(m,'load_config',return_value=self.c), patch.object(m,'automatic',return_value={}) as auto, contextlib.redirect_stdout(io.StringIO()):
            self.assertEqual(m.main(['auto','--execute']),0)
            auto.assert_called_once_with(self.c,True)
