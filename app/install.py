#!/usr/bin/env python3
"""Install/update zvm-power-manager without modifying VM state."""
import argparse
import fcntl
import grp
import json
import os
from pathlib import Path
import pwd
import re
import shlex
import shutil
import subprocess
import sys
import tempfile
import tomllib

MIN_PYTHON = (3, 12)
# 'app'
APP_DIR = '' 
SOURCE_DIR = Path(__file__).resolve().parent / APP_DIR
UNIT_DIR = Path('/etc/systemd/system')
TMPFILES_DIR = Path('/etc/tmpfiles.d')
RUN_LOCK_DIR = Path('/run/lock')
DEFAULT_SERVICE_NAME = 'zvm-power-manager'
SERVICE = DEFAULT_SERVICE_NAME + '.service'
TIMER = 'zvm-power-manager.timer'
PROBE = ('import json,sys; print(json.dumps({"version":list(sys.version_info[:3]),'
         '"base":getattr(sys,"_base_executable",sys.executable)}))')


class InstallError(Exception):
    pass


def run(argv, check=True):
    result = subprocess.run([str(x) for x in argv], text=True, capture_output=True)
    if check and result.returncode:
        raise InstallError(f'Command failed: {shlex.join([str(x) for x in argv])}\n{result.stderr.strip() or result.stdout.strip()}')
    return result


def absolute(value):
    path = Path(value)
    if not path.is_absolute() or any(ch in str(path) for ch in '\n\r\0'):
        raise InstallError(f'An absolute path without control characters is required: {value!r}')
    return path


def interpreter(value):
    value = str(value)
    if re.fullmatch(r'3\.\d+(?:\.\d+)?', value):
        value = 'python' + value
    executable = shutil.which(value)
    if not executable:
        raise InstallError(f'Python executable not found: {value}')
    info = json.loads(run([executable, '-I', '-c', PROBE]).stdout)
    if tuple(info['version'][:2]) < MIN_PYTHON:
        raise InstallError(f'Python >= 3.12 is required, found {info["version"]}')
    return Path(executable), info


def quote(value, exec_arg=False):
    value = str(value)
    if any(ch in value for ch in '\n\r\0'):
        raise InstallError('Control characters are not allowed in systemd values')
    value = value.replace('\\', '\\\\').replace('"', '\\"').replace('%', '%%')
    if exec_arg:
        value = value.replace('$', '$$')
    return '"' + value + '"'


def service_text(prefix, config, account, state_parent, service_name=DEFAULT_SERVICE_NAME):
    # Do not use /home/<username>: use the passwd database's actual home directory.
    return f'''[Unit]
Description=VirtualBox power-aware save and restore
After=local-fs.target systemd-tmpfiles-setup.service
RequiresMountsFor={quote(prefix)} {quote(account.pw_dir)} {quote(config.parent)} {quote(state_parent)}

[Service]
Type=oneshot
User={account.pw_name}
Group={grp.getgrgid(account.pw_gid).gr_name}
WorkingDirectory={str(prefix).replace('%', '%%')}
Environment={quote('HOME=' + account.pw_dir)}
Environment={quote('USER=' + account.pw_name)}
Environment={quote('LOGNAME=' + account.pw_name)}
Environment=PYTHONDONTWRITEBYTECODE=1
ExecStart={quote(prefix / 'venv/bin/python', True)} {quote(prefix / 'zvm_power_manager.py', True)} --config {quote(config, True)} auto --execute
UMask=0077
TimeoutStartSec=infinity
TimeoutStopSec=15s
# Allow VirtualBox server/VM descendants to outlive each policy invocation.
KillMode=process
PrivateTmp=no
StandardOutput=journal
StandardError=journal
SyslogIdentifier={service_name}
'''


def read_configuration(path, source, account, service_name=DEFAULT_SERVICE_NAME):
    existing = path.exists()
    text = path.read_text() if existing else (source / 'config.toml.sample').read_text().replace(
        'vm_owner = "your-user"', 'vm_owner = ' + json.dumps(account.pw_name))
    if not existing and service_name != DEFAULT_SERVICE_NAME:
        text = text.replace('/var/lib/zvm-power-manager/state.json',
                            '/var/lib/' + service_name + '/state.json')
        text = text.replace('/run/lock/zvm-power-manager.lock',
                            '/run/lock/' + service_name + '.lock')
    data = tomllib.loads(text)
    if data.get('vm_owner') != account.pw_name:
        raise InstallError('Configuration vm_owner must match --user')
    state = absolute(data['state_path'])
    lock = absolute(data['lock_path'])
    # A flat /run/lock file needs no per-user runtime directory or login session.
    if lock.parent != RUN_LOCK_DIR:
        raise InstallError('This system-service installer requires lock_path directly under /run/lock')
    if state == lock or state == path or lock == path:
        raise InstallError('Configuration, state and lock paths must be distinct')
    return existing, text, data


def check_existing_unit(text, account, config):
    user = re.search(r'^User=(.*)$', text, re.MULTILINE)
    if not user or user.group(1).strip() != account.pw_name:
        raise InstallError('Existing service belongs to a different user or is not recognized; migration is required')
    command = re.search(r'^ExecStart=(.*)$', text, re.MULTILINE)
    args = shlex.split(command.group(1)) if command else []
    if '--config' not in args:
        raise InstallError('Existing service has no explicit --config; migration is required')
    if args.index('--config') + 1 >= len(args):
        raise InstallError('Existing service has an incomplete --config argument')
    old = args[args.index('--config') + 1].replace('%%', '%').replace('$$', '$')
    if Path(old) != config:
        raise InstallError(f'Refusing to change the service configuration path from {old} to {config}')


class FileChanges:
    """Retain backups and restore replaced files if installation fails."""
    def __init__(self, backup):
        self.backup = backup
        self.entries = []

    def write(self, path, content, mode=0o644, uid=0, gid=0):
        path = Path(path)
        if path.is_symlink():
            raise InstallError(f'Refusing to replace a symlink: {path}')
        previous = None
        if path.exists():
            previous = self.backup / str(len(self.entries))
            shutil.copy2(path, previous)
            stat = path.stat()
            os.chown(previous, stat.st_uid, stat.st_gid)
        self.entries.append((path, previous))
        (self.backup / 'manifest.json').write_text(json.dumps([
            {'target': str(target), 'backup': str(saved) if saved else None}
            for target, saved in self.entries], indent=2))
        fd, name = tempfile.mkstemp(prefix='.zvm-install-', dir=path.parent)
        try:
            with os.fdopen(fd, 'w') as stream:
                stream.write(content)
                stream.flush()
                os.fsync(stream.fileno())
            os.chmod(name, mode)
            os.chown(name, uid, gid)
            os.replace(name, path)
        finally:
            if os.path.exists(name):
                os.unlink(name)

    def rollback(self):
        for path, previous in reversed(self.entries):
            if previous:
                shutil.copy2(previous, path)
                stat = previous.stat()
                os.chown(path, stat.st_uid, stat.st_gid)
            else:
                path.unlink(missing_ok=True)


def user_directory(path, account, mode):
    missing = []
    current = path
    while not current.exists():
        missing.append(current)
        current = current.parent
    for directory in reversed(missing):
        directory.mkdir(mode=mode)
        os.chown(directory, account.pw_uid, account.pw_gid)


def open_lock(path):
    # O_CREAT on another user's existing file in a sticky directory may be
    # denied by fs.protected_regular, even for root. Preserve the existing inode.
    flags = os.O_RDWR | os.O_NOFOLLOW
    for attempt in range(3):
        try:
            return os.open(path, flags)
        except FileNotFoundError:
            try:
                return os.open(path, flags | os.O_CREAT | os.O_EXCL, 0o600)
            except FileExistsError:
                continue  # Another process created it; open without O_CREAT.
    raise InstallError('Lock file changed repeatedly during opening: ' + str(path))


def show_property(unit, name):
    return run(['systemctl', 'show', unit, '--property=' + name, '--value']).stdout.strip()


def install(args):
    source = SOURCE_DIR
    service_name = args.service_name
    if (not re.fullmatch(r'[A-Za-z0-9][A-Za-z0-9_-]{0,199}', service_name)):
        raise InstallError('--service-name must be a base name (letters, digits, _ or -), without .service/.timer')
    service_id, timer_id = service_name + '.service', service_name + '.timer'
    prefix, config = absolute(args.prefix), absolute(args.config)
    account = pwd.getpwnam(args.user)
    if account.pw_uid == 0 or not re.fullmatch(r'[a-zA-Z0-9_.-]+', account.pw_name):
        raise InstallError('--user must name a non-root local account with a simple username')
    if not Path(account.pw_dir).is_absolute():
        raise InstallError('User home directory must be absolute')
    app = prefix / 'zvm_power_manager.py'
    venv = prefix / 'venv'
    unit = UNIT_DIR / service_id
    timer = unit.with_name(timer_id)
    tmpfiles = TMPFILES_DIR / (service_name + '.conf')
    if not args.update and (app.exists() or venv.exists() or unit.exists() or timer.exists()):
        raise InstallError('An installation already exists; use --update')
    if args.update and not unit.exists():
        raise InstallError('--update requires the selected service to exist; use a separate --prefix for a new service')
    if args.update and not app.exists():
        raise InstallError('--update requires an existing installation')
    for other in UNIT_DIR.glob('*.service'):
        if other == unit:
            continue
        content = other.read_text()
        if str(prefix / 'zvm_power_manager.py') in content or quote(prefix / 'zvm_power_manager.py', True) in content:
            raise InstallError(f'Installation directory is used by {other.name}; use a separate --prefix')
    if unit.exists():
        check_existing_unit(unit.read_text(), account, config)
    exists, config_text, cfg = read_configuration(config, source, account, service_name)
    if args.update and not exists:
        raise InstallError('Update requires the existing configuration; it will not create a replacement')
    if args.enable and not exists:
        raise InstallError('First create/review the generated configuration, then rerun --update --enable')
    selected = args.python or (str(venv / 'bin/python') if venv.exists() else 'python3.12')
    python, info = interpreter(selected)
    if venv.exists():
        _, current = interpreter(venv / 'bin/python')
        different = (info['version'][:2] != current['version'][:2] or
                     Path(info['base']).resolve() != Path(current['base']).resolve())
        if different and not args.rebuild_venv:
            raise InstallError('Selected Python differs from the existing venv; add --rebuild-venv explicitly')
    if args.rebuild_venv and not args.update:
        raise InstallError('--rebuild-venv is only valid with --update')
    # Rebuilding must use a base interpreter, not a venv which is about to move.
    if args.rebuild_venv:
        python, info = interpreter(info['base'])
    unit_text = service_text(prefix, config, account, Path(cfg['state_path']).parent, service_name)
    timer_text = (source / TIMER).read_text().replace('Unit=' + SERVICE, 'Unit=' + service_id)
    group = grp.getgrgid(account.pw_gid).gr_name
    tmp_text = '# Keep this lock inode; do not delete during use.\nf ' + quote(cfg['lock_path']) + f' 0600 {account.pw_name} {group} - -\n'
    # Validate source/config in a separate interpreter without calling VBoxManage.
    with tempfile.TemporaryDirectory(prefix='zvm-validate-') as directory:
        check_config = Path(directory) / 'config.toml'
        check_config.write_text(config_text)
        validate = ('import importlib.util,sys; s=importlib.util.spec_from_file_location("zvm",sys.argv[1]); '
                    'm=importlib.util.module_from_spec(s); s.loader.exec_module(m); m.load_config(sys.argv[2])')
        run([python, '-B', '-I', '-c', validate, source / 'zvm_power_manager.py', check_config])
    print(json.dumps({'service_name': service_name, 'mode': 'update' if args.update else 'install', 'user': account.pw_name,
                      'config': str(config), 'config_preserved': exists, 'prefix': str(prefix),
                      'python': str(python), 'python_version': info['version'],
                      'rebuild_venv': args.rebuild_venv, 'enable_requested': args.enable,
                      'dry_run': args.dry_run}, indent=2))
    if args.dry_run:
        return
    if os.geteuid() != 0:
        raise InstallError('Installation requires root; run with sudo, or use --dry-run')
    for target in (prefix, config, Path(cfg['state_path']), Path(cfg['lock_path']), unit, timer, tmpfiles):
        if target.is_symlink():
            raise InstallError(f'Symlink target is not supported: {target}')
    if show_property(service_id, 'ActiveState') not in ('inactive', 'failed', ''):
        raise InstallError('Service is active; wait for it to finish before updating (it will not be killed)')
    was_active = show_property(timer_id, 'ActiveState') == 'active'
    was_enabled = run(['systemctl', 'is-enabled', timer_id], check=False).stdout.strip() == 'enabled'
    # Acquire the application's own lock before stopping scheduling or replacing files.
    lock_path = Path(cfg['lock_path'])
    fd = open_lock(lock_path)
    try:
        fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        os.fchown(fd, account.pw_uid, account.pw_gid)
        os.fchmod(fd, 0o600)
    except BaseException:
        os.close(fd)
        raise
    backup = None
    old_venv = None
    new_venv = False
    changes = None
    success = False
    timer_error = None
    try:
        if was_active:
            run(['systemctl', 'stop', timer_id])
        if show_property(service_id, 'ActiveState') not in ('inactive', 'failed', ''):
            raise InstallError('A scheduled invocation is still active; retry after it finishes')
        prefix.mkdir(parents=True, exist_ok=True)
        backup = Path(tempfile.mkdtemp(prefix='install-backup-', dir=prefix))
        os.chmod(backup, 0o700)
        changes = FileChanges(backup)
        if args.rebuild_venv and venv.exists():
            old_venv = backup / 'venv'
            venv.rename(old_venv)
        if not venv.exists():
            new_venv = True
            run([python, '-m', 'venv', '--without-pip', venv])
        run([venv / 'bin/python', '-B', '-I', '-c', 'import tomllib,fcntl'])
        # Test the new application before switching the installed file.
        run([venv / 'bin/python', '-B', source / 'zvm_power_manager.py', '--help'])
        if not exists:
            if not config.parent.exists():
                user_directory(config.parent, account, 0o700)
            changes.write(config, config_text, 0o600, account.pw_uid, account.pw_gid)
        state_parent = Path(cfg['state_path']).parent
        if not state_parent.exists():
            user_directory(state_parent, account, 0o750)
        run(['runuser', '-u', account.pw_name, '--', 'test', '-r', config])
        run(['runuser', '-u', account.pw_name, '--', 'test', '-w', state_parent])
        run(['runuser', '-u', account.pw_name, '--', str(venv / 'bin/python'), '-B', '-I', '-c', 'import tomllib,fcntl'])
        changes.write(app, (source / 'zvm_power_manager.py').read_text())
        changes.write(unit, unit_text)
        changes.write(timer, timer_text)
        changes.write(tmpfiles, tmp_text)
        run(['systemd-analyze', 'verify', unit, timer])
        run(['systemctl', 'daemon-reload'])
        if args.enable:
            run(['systemctl', 'enable', timer_id])
        (backup / 'manifest.json').write_text(json.dumps([{'target': str(path), 'backup': str(previous) if previous else None} for path, previous in changes.entries], indent=2))
        success = True
    except BaseException:
        if changes:
            changes.rollback()
        if new_venv and venv.exists():
            shutil.rmtree(venv)  # Only the venv created by this attempt.
        if old_venv and old_venv.exists():
            old_venv.rename(venv)
        run(['systemctl', 'daemon-reload'], check=False)
        if args.enable and not was_enabled:
            run(['systemctl', 'disable', timer_id], check=False)
        raise
    finally:
        os.close(fd)
        # Start only after releasing the application lock; this can run real policy.
        if was_active or (success and args.enable):
            result = run(['systemctl', 'start', timer_id], check=False)
            if result.returncode:
                timer_error = 'Could not restart the timer: ' + result.stderr.strip()
                print('WARNING: ' + timer_error, file=sys.stderr)
    print(f'Installation complete. Backups: {backup}')
    print('Configuration/state/pending journals were preserved.' if exists else
          f'Configuration created: {config}. Review VM names before enabling the timer.')
    if timer_error:
        raise InstallError('Files installed, but ' + timer_error)
    print('Timer active.' if was_active or args.enable else 'Timer was not started.')


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--user', required=True, help='VM owner account (not root)')
    parser.add_argument('--config', required=True, help='Explicit absolute configuration path; existing files are preserved')
    parser.add_argument('--service-name', default=DEFAULT_SERVICE_NAME, help='Service/timer base name without suffix (default: %(default)s)')
    parser.add_argument('--prefix', default='/opt/zvm-power-manager', help='Application and venv installation directory (default: %(default)s)')
    parser.add_argument('--python', help='Python version or executable/path, e.g. 3.13 or /usr/bin/python3.13; default: existing venv or python3.12')
    parser.add_argument('--update', action='store_true', help='Update an existing installation, preserving configs, data and timer state')
    parser.add_argument('--rebuild-venv', action='store_true', help='With --update, explicitly rebuild venv with selected Python; retain backup')
    parser.add_argument('--enable', action='store_true', help='Explicitly enable/start the timer; may trigger real VM operations')
    parser.add_argument('--dry-run', action='store_true', help='Validate and show plan without changing installation/systemd')
    args = parser.parse_args(argv)
    try:
        install(args)
        return 0
    except (InstallError, OSError, ValueError, KeyError) as exc:
        print(f'ERROR: {exc}', file=sys.stderr)
        return 1


if __name__ == '__main__':
    sys.exit(main())
