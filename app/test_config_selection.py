import contextlib
import io
import os
from pathlib import Path
import unittest
from unittest.mock import patch
import zvm_power_manager as m

class ConfigSelectionTests(unittest.TestCase):
    def test_cli_overrides_environment(self):
        with patch.dict(os.environ,{m.LC.CONFIG_ENV:'/env.toml'}):
            self.assertEqual(m.configuration_path('/cli.toml'),Path('/cli.toml'))
            self.assertEqual(m.configuration_path(None),Path('/env.toml'))

    def test_missing_or_empty_configuration(self):
        for value in (None,'','  '):
            with patch.dict(os.environ,{},clear=True):
                if value is not None:os.environ[m.LC.CONFIG_ENV]=value
                with self.assertRaises(m.ValidationError):m.configuration_path(None)
        with patch.dict(os.environ,{m.LC.CONFIG_ENV:'/env.toml'}):
            with self.assertRaises(m.ValidationError):m.configuration_path('')

    def test_help_needs_no_configuration(self):
        for args in (['--help'],['status','--help'],['check','--help'],['save','--help'],['restore','--help'],['recover','--help'],['auto','--help']):
            with patch.dict(os.environ,{},clear=True),patch.object(m,'load_config') as load,contextlib.redirect_stdout(io.StringIO()):
                with self.assertRaises(SystemExit) as e:m.main(args)
                self.assertEqual(e.exception.code,0)
                load.assert_not_called()

    def test_missing_configuration_prevents_execution(self):
        with patch.dict(os.environ,{},clear=True),patch.object(m,'execute') as execute,contextlib.redirect_stderr(io.StringIO()) as err:
            self.assertEqual(m.main(['save','--execute']),1)
            self.assertIn('ZVM_POWER_MANAGER_CONFIG',err.getvalue())
            execute.assert_not_called()

    def test_invalid_explicit_config_never_falls_back(self):
        for failure in (FileNotFoundError('missing'),PermissionError('denied'),ValueError('invalid TOML'),m.ValidationError('bad schema')):
            with patch.dict(os.environ,{m.LC.CONFIG_ENV:'/env.toml'}),patch.object(m,'load_config',side_effect=failure) as load,patch.object(m,'inspect') as inspect,contextlib.redirect_stderr(io.StringIO()) as err:
                self.assertEqual(m.main(['--config','/explicit.toml','status']),1)
                load.assert_called_once_with(Path('/explicit.toml'))
                self.assertIn('/explicit.toml',err.getvalue())
                self.assertIn('No fallback',err.getvalue())
                inspect.assert_not_called()

    def test_environment_dispatch(self):
        with patch.dict(os.environ,{m.LC.CONFIG_ENV:'/env.toml'}),patch.object(m,'load_config',return_value={}) as load,patch.object(m,'inspect',return_value={}),contextlib.redirect_stdout(io.StringIO()):
            self.assertEqual(m.main(['status']),0)
            load.assert_called_once_with(Path('/env.toml'))
