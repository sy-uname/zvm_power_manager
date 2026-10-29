#!/usr/bin/env python3
"""ST01.3: VirtualBox power policy, explicit save and restore."""
import argparse
from contextlib import contextmanager
from datetime import datetime, timezone
import fcntl
import tempfile
import json
import os
from pathlib import Path
import pwd
import re
import subprocess
import sys
import tomllib
import uuid


class LC:
    """Project constants."""
    ACTION_STORE_TRUE = 'store_true'
    AUTOMATIC = 'auto'
    SUMMARY = 'summary'
    POWER_REASON = 'power_reason'
    WARNINGS = 'warnings'
    FAILED = 'failed'
    SKIPPED = 'skipped'
    OUTCOME = 'outcome'
    SUCCESS = 'success'
    PARTIAL = 'partial_success'
    FAILURE = 'failed'
    NO_ACTION = 'no_action'
    RUNNING_VMS = 'running_vms'
    EXCLUDED_VMS = 'excluded_vms'
    PENDING_OPERATIONS = 'pending_operations'
    PROCESS_VMS = 'process_vms'
    JOURNAL_PATH = 'journal_path'
    VM_FRONTENDS = ('VirtualBoxVM', 'VBoxHeadless', 'VBoxSDL')
    STARTVM_OPTION = '--startvm'
    COMM = 'comm'
    FILTERED = 'excluded_by_save_order'
    INVISIBLE = 'process_exists_but_vboxmanage_does_not_report_running'
    JOURNAL_BLOCKED = 'unresolved_pending_operation'
    UNREGISTERED = 'running_but_not_registered'
    OPTION_CONTINUE = '--continue'
    CONTINUE = 'continue'
    RECOVER = 'recover'
    RECOVERY = 'recovery'
    PENDING = 'pending'
    STARTED_AT = 'started_at'
    LAST_ERROR = 'last_error'
    UPDATED_AT = 'updated_at'
    RESOLUTION = 'resolution'
    RESOLVED = 'resolved'
    RETRY = 'retry_required'
    NO_PENDING = 'no_pending'
    VM_STATE = 'vm_state'
    PROC_ROOT = '/proc'
    CMDLINE = 'cmdline'
    NUL = b'\0'
    VBOX_MANAGE_NAME = 'VBoxManage'
    OPTION_EXECUTE = '--execute'
    EXECUTE = 'execute'
    VM_RUNNING = 'running'
    VM_SAVED = 'saved'
    VBOX_INFO = 'showvminfo'
    VBOX_MACHINE_READABLE = '--machinereadable'
    VBOX_CONTROL = 'controlvm'
    VBOX_SAVE = 'savestate'
    VBOX_START = 'startvm'
    VBOX_TYPE = '--type'
    VM_STATE_PATTERN = r'^VMState="([^"\n]+)"$'
    PENDING_SUFFIX = '.pending'
    TEMP_PREFIX = '.zvm-'
    STOPPED_REASON = 'stopped_reason'
    COMPLETED = 'completed'
    ERROR = 'error'
    ACTION = 'action'
    ACTION_AUTO = 'auto'
    ACTION_NONE = 'none'
    ACTION_RESTORE = 'restore'
    ACTION_SAVE = 'save'
    AC_ONLINE = 'ac_online'
    AC_PATH = 'ac_path'
    ALREADY_RUNNING = 'already_running'
    BATTERY_PATH = 'battery_path'
    BATTERY_STATUS = 'battery_status'
    CAPACITY = 'capacity'
    CHECK = 'check'
    COMMAND = 'command'
    COMMAND_TIMEOUT = 'command_timeout'
    CONFIG_FILENAME = 'config.toml'
    STATE_ENCODING = 'utf-8'
    EXECUTED_ACTIONS = 'executed_actions'
    FORCE = 'force'
    LOCK_ACQUIRED = 'lock_acquired'
    LOCK_PATH = 'lock_path'
    NAME = 'name'
    NOT_REGISTERED = 'not_registered'
    ONLINE = 'online'
    OPTION_ACTION = '--action'
    OPTION_CONFIG = '--config'
    OPTION_DRY_RUN = '--dry-run'
    OPTION_FORCE = '--force'
    PLANNED_QUEUE = 'planned_queue'
    POWER = 'power'
    POWER_GATE_PASSED = 'power_gate_passed'
    READ_ONLY = 'read_only'
    REASON = 'reason'
    REGISTERED_VMS = 'registered_vms'
    REQUESTED_ACTION = 'requested_action'
    RESTORE_ORDER = 'restore_order'
    RESTORE_POWER_ELIGIBLE = 'restore_power_eligible'
    RESTORE_QUEUE = 'restore_queue'
    RESTORE_SKIPPED = 'restore_skipped'
    RESTORE_THRESHOLD = 'restore_threshold'
    RESTORE_TYPE = 'restore_type'
    RESTORE_TYPE_HEADLESS = 'headless'
    SAVED_BY_SERVICE = 'saved_by_service'
    SAVE_ORDER = 'save_order'
    INCLUDE_OTHER_RUNNING_VMS = 'include_other_running_vms'
    SAVE_POWER_ELIGIBLE = 'save_power_eligible'
    SAVE_QUEUE = 'save_queue'
    SAVE_THRESHOLD = 'save_threshold'
    SCHEMA_VERSION = 'schema_version'
    SELECTED_ACTION = 'selected_action'
    STAGE = 'stage'
    STAGE_ID = 'ST01.3'
    STATE_EXISTS = 'state_exists'
    STATE_PATH = 'state_path'
    STATUS = 'status'
    SYSFS_AC_OFFLINE = '0'
    SYSFS_AC_ONLINE = '1'
    UUID = 'uuid'
    VBOXMANAGE = 'vboxmanage'
    VBOX_LIST = 'list'
    VBOX_RUNNING_VMS = 'runningvms'
    VBOX_VMS = 'vms'
    VM_LIST_PATTERN = '"(.*)" \\{([0-9a-fA-F-]{36})\\}'
    VM_OWNER = 'vm_owner'
    CONFIG_SCHEMA_VERSION = 1
    STATE_SCHEMA_VERSION = 1
    PERCENT_MIN = 0
    PERCENT_MAX = 100
    TIMEOUT_MIN = 1
    TIMEOUT_MAX = 300
    EXIT_SUCCESS = 0
    EXIT_ERROR = 1
    EXIT_USAGE = 2
    VBOX_READ_COMMANDS = ((VBOX_LIST, VBOX_VMS), (VBOX_LIST, VBOX_RUNNING_VMS))
    STATE_KEYS = frozenset((SCHEMA_VERSION, SAVED_BY_SERVICE))
    SAVED_VM_KEYS = frozenset((UUID, NAME))
    CONFIG_KEYS = frozenset({SCHEMA_VERSION, VM_OWNER, VBOXMANAGE, BATTERY_PATH, AC_PATH, STATE_PATH, LOCK_PATH, SAVE_THRESHOLD, RESTORE_THRESHOLD, SAVE_ORDER, RESTORE_ORDER, RESTORE_TYPE, COMMAND_TIMEOUT, INCLUDE_OTHER_RUNNING_VMS})

class TEXT:
    """External interface literals."""
    HELP_AUTO = 'Select save/restore from current power; never force or retry pending operations'
    HELP_AUTO_EXECUTE = 'Execute the power policy once (for the systemd timer)'
    SUMMARY_IDLE = 'No action: neither save nor restore power conditions are satisfied.'
    SUMMARY_PLAN = 'Candidates for {0}: {1}. Planned now: {2}. Power gate: {3}. Warnings: {4}; pending operations: {5}.'
    SUMMARY_EXECUTION = 'Completed: {0}; failed: {1}; skipped: {2}. Outcome: {3}.'
    POWER_FORCED = 'Power eligibility bypassed by --force.'
    POWER_ALLOWED = 'Power conditions allow the selected action.'
    POWER_SAVE_BLOCKED = 'Save requires AC offline and battery <= {0}%; current AC online={1}, battery={2}%.'
    POWER_RESTORE_BLOCKED = 'Restore requires AC online and battery >= {0}%; current AC online={1}, battery={2}%.'
    POWER_NO_ACTION = 'Neither save nor restore power conditions are satisfied.'
    WARNING_PROCESS = 'Cannot inspect VM processes completely: {0}'
    ERROR_DUPLICATE_PENDING = 'Multiple pending journals for VM {0}; reconcile the files before execution.'
    ERROR_PROCESS_CONFLICT = 'VM {0} has an active frontend process but is not reported running; recovery is unsafe.'
    ERROR_JOURNAL = 'Invalid pending journal {0}: {1}. Nothing was changed.'
    ERROR_JOURNAL_FIELDS = 'Expected action (save/restore), uuid and name; optional started_at, updated_at and last_error'
    ERROR_ACTION_MISMATCH = 'Pending action is {0}, requested action is {1}; journal was preserved.'
    ERROR_RECOVERY_STATE = 'Cannot reconcile VM {0}: state is {1}, transitional, inconsistent, or unsupported; journal was preserved.'
    ERROR_ACTIVE_OPERATION = 'A VBoxManage operation for VM {0} is still active; retry later.'
    ERROR_RETRY_FILTER = 'Pending VM {0} is excluded by current save settings; journal was preserved.'
    ERROR_CONTINUE_MODE = '--continue requires --execute; use recover --dry-run to preview journal recovery.'
    HELP_CONTINUE = 'Reconcile the pending operation, retry it if needed, then continue the queue'
    HELP_RECOVER = 'Reconcile service files only; never start or save VMs'
    HELP_RECOVER_EXECUTE = 'Commit a confirmed result to state.json and remove the resolved journal'
    HELP_RECOVER_DETAIL = 'Incomplete operations remain pending. Use save/restore --execute --continue to retry. Unexpected or inconsistent VM states block recovery.'
    ERROR_LOCK = 'Another operation holds lock {0}'
    ERROR_PENDING = 'Unresolved operation journal exists: {0}. Use recover --dry-run to inspect it, then recover --execute or the matching save/restore --execute --continue.'
    ERROR_VM_STATE = 'VM {0}: expected state {1}, found {2}; operation stopped.'
    ERROR_VM_STATE_OUTPUT = 'Cannot read exactly one VMState from VBoxManage for {0}'
    ERROR_TRACKED_RUNNING = 'VM {0} is running but already recorded as saved. Reconcile state before saving again.'
    ERROR_POWER = 'Power condition is not satisfied; remaining operations were not executed.'
    ERROR_EXECUTION = 'Operation failed: {0}. Completed entries are committed; inspect any pending journal before retrying.'
    HELP_SAVE = 'Save eligible running VMs; select --dry-run or --execute explicitly'
    HELP_RESTORE = 'Restore service-recorded saved VMs headlessly; select --dry-run or --execute'
    HELP_EXECUTE = 'Perform real VM operations and update service state'
    HELP_PREVIEW = 'Preview the queue without modifying VMs or service files'
    HELP_EXECUTION_FORCE = 'Bypass only the power condition; all state and lock checks remain active'
    HELP_EXECUTION = 'Use --dry-run to preview. --execute modifies real VMs. Per-VM errors are reported while other VMs continue; shared persistence errors stop execution.'
    ERROR_ABSOLUTE_PATH = '{0} must be an absolute path'
    ERROR_AC_ONLINE = 'Invalid AC online value'
    ERROR_BATTERY_CAPACITY = 'Invalid battery capacity'
    ERROR_BATTERY_STATUS = 'Empty battery status'
    ERROR_CONFIG_KEYS = 'Configuration keys: missing={0}, unknown={1}'
    ERROR_CONFIG_SCHEMA = 'Unsupported config schema'
    ERROR_DUPLICATE_NAMES = 'Duplicate names in {0}'
    ERROR_DUPLICATE_UUID = 'Duplicate VM UUID: {0}'
    ERROR_FORCE_ACTION = '--force requires --action save or --action restore'
    ERROR_INVALID = 'Invalid {0}'
    ERROR_BOOLEAN = '{0} must be a boolean (true or false)'
    ERROR_INVENTORY_CHANGED = 'VM inventory changed or is inconsistent; retry'
    ERROR_MESSAGE = 'ERROR: {0}'
    ERROR_PATH_COLLISION = 'State and lock paths must differ'
    ERROR_RESTORE_TYPE = 'Only future {0} restore is supported'
    ERROR_SAVED_ENTRY = 'Invalid saved VM entry'
    ERROR_SAVED_LIST = 'saved_by_service must be a list'
    ERROR_SAVED_NAME = 'Invalid saved VM name'
    ERROR_SAVED_UUID_DUPLICATE = 'Duplicate saved VM UUID'
    ERROR_STATE_FIELDS = 'Invalid state fields'
    ERROR_STATE_SCHEMA = 'Unsupported state schema'
    ERROR_STATE_JSON = 'Invalid JSON in state file {0}: {1} (line {2}, column {3}). Expected an object with schema_version and saved_by_service. The file was not modified.'
    ERROR_STATE_ENCODING = 'Cannot decode state file {0}: {1}. Save the file as UTF-8. The file was not modified.'
    ERROR_STATE_CONTENT = 'Invalid state file {0}: {1}. The file was not modified.'
    ERROR_THRESHOLD_ORDER = 'Restore threshold must exceed save threshold'
    ERROR_TIMEOUT = 'Invalid timeout'
    ERROR_UUID_TYPE = 'VM UUID must be a string'
    ERROR_VBOX_FAILED = 'VBoxManage failed ({0}): {1}'
    ERROR_VBOX_OPERATION = 'Forbidden VBoxManage operation'
    ERROR_VBOX_OUTPUT = 'Unexpected VBoxManage output: {0!r}'
    ERROR_VM_OWNER = 'Run as VM owner {0}; do not run as root'
    HELP_CHECK = 'Show the action plan (JSON); requires --dry-run'
    HELP_FORCE = 'Bypass only the power check; requires --action save or restore'
    HELP_STATUS = 'Show power, VM inventory and queues (JSON)'
    HELP_DESCRIPTION = 'zvm-power-manager ST01.3: inspect, preview, save and restore VMs.'
    HELP_COMMANDS = 'Commands (each has its own arguments and help)'
    HELP_CONFIG = 'Path to config.toml; specify before the command'
    HELP_DRY_RUN = 'Required: show the plan without executing any actions'
    HELP_ACTION = 'auto: select based on power; save: preview saving; restore: preview restoration'
    HELP_MAIN_EXAMPLES = '''Usage examples:\n  %(prog)s status\n  %(prog)s check --dry-run\n   %(prog)s --config /opt/zvm-power-manager/config.toml check --dry-run\n\nCommand arguments and detailed help:\n  %(prog)s status --help\n  %(prog)s check --help\n\nPlace global options before the command and command arguments after it.\nstatus and check are read-only. save and restore require --dry-run or --execute.\nUse save --help or restore --help for execution options.\nUse recover --help for pending-operation reconciliation.'''
    HELP_CHECK_EXAMPLES = '''Examples:\n  %(prog)s --dry-run\n  %(prog)s --dry-run --action save\n  %(prog)s --dry-run --action restore --force\n\nThe command requires --dry-run. No actions are executed, even with --force.\n--force bypasses only the power condition; all other checks remain active.'''
    HELP_STATUS_DETAILS = 'status has no additional options except -h/--help. No data is modified.'
    CLI_ERROR = '{0}: error: {1}\n'


class ValidationError(Exception):
    pass


class HelpArgumentParser(argparse.ArgumentParser):
    """Show the relevant command's full help when arguments are invalid."""

    def error(self, message):
        self.print_help(sys.stderr)
        self.exit(LC.EXIT_USAGE, TEXT.CLI_ERROR.format(self.prog, message))


def require(condition, message):
    if not condition:
        raise ValidationError(message)


def load_config(path):
    with Path(path).open('rb') as stream:
        c = tomllib.load(stream)
    require(set(c) == LC.CONFIG_KEYS, TEXT.ERROR_CONFIG_KEYS.format(sorted(LC.CONFIG_KEYS - set(c)), sorted(set(c) - LC.CONFIG_KEYS)))
    require(type(c[LC.SCHEMA_VERSION]) is int and c[LC.SCHEMA_VERSION] == LC.CONFIG_SCHEMA_VERSION, TEXT.ERROR_CONFIG_SCHEMA)
    for key in (LC.VBOXMANAGE, LC.BATTERY_PATH, LC.AC_PATH, LC.STATE_PATH, LC.LOCK_PATH):
        require(isinstance(c[key], str) and Path(c[key]).is_absolute(), TEXT.ERROR_ABSOLUTE_PATH.format(key))
    require(c[LC.STATE_PATH] != c[LC.LOCK_PATH], TEXT.ERROR_PATH_COLLISION)
    require(type(c[LC.INCLUDE_OTHER_RUNNING_VMS]) is bool,
            TEXT.ERROR_BOOLEAN.format(LC.INCLUDE_OTHER_RUNNING_VMS))
    require(isinstance(c[LC.VM_OWNER], str) and bool(c[LC.VM_OWNER]), TEXT.ERROR_INVALID.format(LC.VM_OWNER))
    for key in (LC.SAVE_THRESHOLD, LC.RESTORE_THRESHOLD):
        require(type(c[key]) is int and LC.PERCENT_MIN <= c[key] <= LC.PERCENT_MAX, TEXT.ERROR_INVALID.format(key))
    require(c[LC.SAVE_THRESHOLD] < c[LC.RESTORE_THRESHOLD], TEXT.ERROR_THRESHOLD_ORDER)
    require(type(c[LC.COMMAND_TIMEOUT]) is int and LC.TIMEOUT_MIN <= c[LC.COMMAND_TIMEOUT] <= LC.TIMEOUT_MAX, TEXT.ERROR_TIMEOUT)
    for key in (LC.SAVE_ORDER, LC.RESTORE_ORDER):
        value = c[key]
        require(isinstance(value, list) and all((isinstance(x, str) and x for x in value)), TEXT.ERROR_INVALID.format(key))
        require(len(value) == len(set(value)), TEXT.ERROR_DUPLICATE_NAMES.format(key))
    require(c[LC.RESTORE_TYPE] == LC.RESTORE_TYPE_HEADLESS, TEXT.ERROR_RESTORE_TYPE.format(LC.RESTORE_TYPE_HEADLESS))
    return c


def read_power(c):
    capacity = int((Path(c[LC.BATTERY_PATH]) / LC.CAPACITY).read_text().strip())
    online = (Path(c[LC.AC_PATH]) / LC.ONLINE).read_text().strip()
    status = (Path(c[LC.BATTERY_PATH]) / LC.STATUS).read_text().strip()
    require(LC.PERCENT_MIN <= capacity <= LC.PERCENT_MAX, TEXT.ERROR_BATTERY_CAPACITY)
    require(online in (LC.SYSFS_AC_OFFLINE, LC.SYSFS_AC_ONLINE), TEXT.ERROR_AC_ONLINE)
    require(bool(status), TEXT.ERROR_BATTERY_STATUS)
    return {LC.CAPACITY: capacity, LC.AC_ONLINE: online == LC.SYSFS_AC_ONLINE, LC.BATTERY_STATUS: status}


def canonical_uuid(value):
    require(isinstance(value, str), TEXT.ERROR_UUID_TYPE)
    return str(uuid.UUID(value))


def parse_vms(output):
    result = {}
    for line in output.splitlines():
        match = re.fullmatch(LC.VM_LIST_PATTERN, line)
        require(match is not None, TEXT.ERROR_VBOX_OUTPUT.format(line))
        name, raw_id = match.groups()
        ident = canonical_uuid(raw_id)
        require(ident not in result, TEXT.ERROR_DUPLICATE_UUID.format(ident))
        result[ident] = name
    return result


def run_vbox(c, *args):
    require(pwd.getpwuid(os.geteuid()).pw_name == c[LC.VM_OWNER], TEXT.ERROR_VM_OWNER.format(c[LC.VM_OWNER]))
    proc = subprocess.run([c[LC.VBOXMANAGE], *args], text=True, capture_output=True, timeout=c[LC.COMMAND_TIMEOUT], check=False)
    require(proc.returncode == LC.EXIT_SUCCESS, TEXT.ERROR_VBOX_FAILED.format(proc.returncode, proc.stderr.strip()))
    return proc.stdout


def vbox(c, *args):
    require(args in LC.VBOX_READ_COMMANDS, TEXT.ERROR_VBOX_OPERATION)
    return parse_vms(run_vbox(c, *args))


def vm_state(c, ident):
    ident = canonical_uuid(ident)
    output = run_vbox(c, LC.VBOX_INFO, ident, LC.VBOX_MACHINE_READABLE)
    states = re.findall(LC.VM_STATE_PATTERN, output, re.MULTILINE)
    require(len(states) == 1, TEXT.ERROR_VM_STATE_OUTPUT.format(ident))
    return states[0]


def mutate_vm(c, action, ident):
    ident = canonical_uuid(ident)
    require(action in (LC.ACTION_SAVE, LC.ACTION_RESTORE), TEXT.ERROR_VBOX_OPERATION)
    if action == LC.ACTION_SAVE:
        run_vbox(c, LC.VBOX_CONTROL, ident, LC.VBOX_SAVE)
    else:
        run_vbox(c, LC.VBOX_START, ident, LC.VBOX_TYPE, LC.RESTORE_TYPE_HEADLESS)


def read_state(path):
    try:
        content = Path(path).read_text(encoding=LC.STATE_ENCODING)
    except FileNotFoundError:
        return ([], False)
    except UnicodeError as exc:
        raise ValidationError(TEXT.ERROR_STATE_ENCODING.format(path, exc)) from exc
    if not content.strip():
        return ([], False)
    try:
        state = json.loads(content)
    except json.JSONDecodeError as exc:
        raise ValidationError(TEXT.ERROR_STATE_JSON.format(path, exc.msg, exc.lineno, exc.colno)) from exc
    try:
        return validate_state(state), True
    except (ValidationError, ValueError) as exc:
        raise ValidationError(TEXT.ERROR_STATE_CONTENT.format(path, exc)) from exc


def validate_state(state):
    require(isinstance(state, dict) and set(state) == LC.STATE_KEYS, TEXT.ERROR_STATE_FIELDS)
    require(type(state[LC.SCHEMA_VERSION]) is int and state[LC.SCHEMA_VERSION] == LC.STATE_SCHEMA_VERSION, TEXT.ERROR_STATE_SCHEMA)
    entries = state[LC.SAVED_BY_SERVICE]
    require(isinstance(entries, list), TEXT.ERROR_SAVED_LIST)
    result = []
    for entry in entries:
        require(isinstance(entry, dict) and set(entry) == LC.SAVED_VM_KEYS, TEXT.ERROR_SAVED_ENTRY)
        require(isinstance(entry[LC.NAME], str) and bool(entry[LC.NAME]), TEXT.ERROR_SAVED_NAME)
        ident = canonical_uuid(entry[LC.UUID])
        require(ident not in [x[LC.UUID] for x in result], TEXT.ERROR_SAVED_UUID_DUPLICATE)
        result.append({LC.UUID: ident, LC.NAME: entry[LC.NAME]})
    return result


def ordered(entries, priority):
    # Stable order preserves the service-recorded order of other VMs.
    return sorted(entries, key=lambda x: priority.index(x[LC.NAME]) if x[LC.NAME] in priority else len(priority))



def process_inventory():
    found, warnings = set(), []
    for process in Path(LC.PROC_ROOT).iterdir():
        if not process.name.isdigit():
            continue
        try:
            if process.stat().st_uid != os.geteuid():
                continue
            name = (process / LC.COMM).read_text().strip()
            if name not in LC.VM_FRONTENDS:
                continue
            args = [os.fsdecode(arg) for arg in (process / LC.CMDLINE).read_bytes().split(LC.NUL)]
            if LC.STARTVM_OPTION in args:
                ident = args[args.index(LC.STARTVM_OPTION) + 1]
                found.add(canonical_uuid(ident))
        except (FileNotFoundError, ProcessLookupError):
            continue
        except (OSError, ValueError, IndexError) as exc:
            warnings.append(TEXT.WARNING_PROCESS.format(exc))
    return found, warnings


def pending_journals(c):
    base = Path(c[LC.STATE_PATH] + LC.PENDING_SUFFIX)
    paths = ([base] if base.exists() else []) + sorted(base.parent.glob(base.name + '.*'))
    result = {}
    for path in paths:
        # Backups are not active journals. New sidecars end in a canonical UUID.
        if path != base:
            try:
                if canonical_uuid(path.name[len(base.name) + 1:]) != path.name[len(base.name) + 1:]:
                    continue
            except (ValueError, ValidationError):
                continue
        item = read_pending(path)
        if item is None:
            continue
        ident = item[LC.UUID]
        require(ident not in result, TEXT.ERROR_DUPLICATE_PENDING.format(ident))
        result[ident] = (path, item)
    return result


def inspect(c, action=LC.ACTION_AUTO, force=False):
    power = read_power(c)
    registered = vbox(c, LC.VBOX_LIST, LC.VBOX_VMS)
    running = vbox(c, LC.VBOX_LIST, LC.VBOX_RUNNING_VMS)
    processes, warnings = process_inventory()
    warnings = list(warnings)
    journals = pending_journals(c)
    saved, state_exists = read_state(c[LC.STATE_PATH])
    save_queue = ordered(
        [{LC.UUID: k, LC.NAME: v} for k, v in running.items()
         if k in registered and (c[LC.INCLUDE_OTHER_RUNNING_VMS] or v in c[LC.SAVE_ORDER])],
        c[LC.SAVE_ORDER])
    excluded = [{LC.UUID: k, LC.NAME: v, LC.REASON: LC.FILTERED if k in registered else LC.UNREGISTERED}
                for k, v in running.items() if k not in [entry[LC.UUID] for entry in save_queue]]
    for ident in sorted(processes - set(running)):
        warning = {LC.UUID: ident, LC.NAME: registered.get(ident), LC.REASON: LC.INVISIBLE}
        warnings.append(warning)
        excluded.append(warning)
    warnings.extend(entry for entry in excluded if entry[LC.REASON] == LC.UNREGISTERED)
    restore_candidates, skipped = ([], [])
    for entry in saved:
        ident = entry[LC.UUID]
        if ident not in registered or ident in running or ident in processes:
            skipped.append({**entry, LC.REASON: LC.INVISIBLE if ident in processes and ident not in running else LC.ALREADY_RUNNING if ident in running else LC.NOT_REGISTERED})
        else:
            restore_candidates.append({LC.UUID: ident, LC.NAME: registered[ident]})
    restore_queue = ordered(restore_candidates, c[LC.RESTORE_ORDER])
    save_ok = not power[LC.AC_ONLINE] and power[LC.CAPACITY] <= c[LC.SAVE_THRESHOLD]
    restore_ok = power[LC.AC_ONLINE] and power[LC.CAPACITY] >= c[LC.RESTORE_THRESHOLD]
    selected = action if action != LC.ACTION_AUTO else LC.ACTION_SAVE if save_ok else LC.ACTION_RESTORE if restore_ok else LC.ACTION_NONE
    allowed = selected != LC.ACTION_NONE and (force or (save_ok if selected == LC.ACTION_SAVE else restore_ok))
    candidates = save_queue if selected == LC.ACTION_SAVE else restore_queue
    queue = [entry for entry in candidates if entry[LC.UUID] not in journals] if allowed else []
    summary = TEXT.SUMMARY_PLAN.format(selected, [e[LC.NAME] for e in candidates] if selected != LC.ACTION_NONE else [],
                                      [e[LC.NAME] for e in queue], allowed, len(warnings), len(journals))
    if force:
        power_reason = TEXT.POWER_FORCED
    elif allowed:
        power_reason = TEXT.POWER_ALLOWED
    elif selected == LC.ACTION_SAVE:
        power_reason = TEXT.POWER_SAVE_BLOCKED.format(c[LC.SAVE_THRESHOLD], power[LC.AC_ONLINE], power[LC.CAPACITY])
    elif selected == LC.ACTION_RESTORE:
        power_reason = TEXT.POWER_RESTORE_BLOCKED.format(c[LC.RESTORE_THRESHOLD], power[LC.AC_ONLINE], power[LC.CAPACITY])
    else:
        power_reason = TEXT.POWER_NO_ACTION
    return {
        LC.SUMMARY: summary,
        LC.POWER_REASON: power_reason,
        LC.RUNNING_VMS: [{LC.UUID: k, LC.NAME: v, LC.VM_STATE: LC.VM_RUNNING} for k, v in running.items()],
        LC.EXCLUDED_VMS: excluded,
        LC.WARNINGS: warnings,
        LC.PENDING_OPERATIONS: [item for path, item in journals.values()],
        LC.STAGE: LC.STAGE_ID,
        LC.READ_ONLY: True,
        LC.POWER: power,
        LC.STATE_PATH: c[LC.STATE_PATH],
        LC.STATE_EXISTS: state_exists,
        LC.LOCK_PATH: c[LC.LOCK_PATH],
        LC.LOCK_ACQUIRED: False,
        LC.REGISTERED_VMS: [{LC.UUID: k, LC.NAME: v} for k, v in registered.items()],
        LC.SAVE_QUEUE: save_queue,
        LC.RESTORE_QUEUE: restore_queue,
        LC.RESTORE_SKIPPED: skipped,
        LC.SAVE_POWER_ELIGIBLE: save_ok,
        LC.RESTORE_POWER_ELIGIBLE: restore_ok,
        LC.REQUESTED_ACTION: action,
        LC.SELECTED_ACTION: selected,
        LC.FORCE: force,
        LC.POWER_GATE_PASSED: allowed,
        LC.PLANNED_QUEUE: queue,
        LC.RESTORE_TYPE: c[LC.RESTORE_TYPE],
        LC.EXECUTED_ACTIONS: 0,
    }


@contextmanager
def operation_lock(path):
    # Never unlink a lock file: concurrent processes must lock the same inode.
    fd = os.open(path, os.O_CREAT | os.O_RDWR | os.O_NOFOLLOW, 0o600)
    try:
        try:
            fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError as exc:
            raise ValidationError(TEXT.ERROR_LOCK.format(path)) from exc
        yield
    finally:
        os.close(fd)


def sync_directory(path):
    fd = os.open(path, os.O_RDONLY | os.O_DIRECTORY)
    try:
        os.fsync(fd)
    finally:
        os.close(fd)


def atomic_json(path, value):
    path = Path(path)
    fd, temporary = tempfile.mkstemp(prefix=LC.TEMP_PREFIX, dir=path.parent)
    try:
        with os.fdopen(fd, 'w', encoding=LC.STATE_ENCODING) as stream:
            json.dump(value, stream, ensure_ascii=False, indent=2)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, path)
        sync_directory(path.parent)
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)


def write_state(path, entries):
    payload = {LC.SCHEMA_VERSION: LC.STATE_SCHEMA_VERSION, LC.SAVED_BY_SERVICE: entries}
    validate_state(payload)
    atomic_json(path, payload)


def power_allows(c, action, force):
    power = read_power(c)  # --force never suppresses unreadable/invalid power data.
    return force or (not power[LC.AC_ONLINE] and power[LC.CAPACITY] <= c[LC.SAVE_THRESHOLD]
                     if action == LC.ACTION_SAVE else
                     power[LC.AC_ONLINE] and power[LC.CAPACITY] >= c[LC.RESTORE_THRESHOLD])



def utc_now():
    return datetime.now(timezone.utc).isoformat()


def read_pending(path):
    try:
        raw = Path(path).read_text(encoding=LC.STATE_ENCODING)
    except FileNotFoundError:
        return None
    except UnicodeError as exc:
        raise ValidationError(TEXT.ERROR_JOURNAL.format(path, exc)) from exc
    try:
        item = json.loads(raw)
        required = {LC.ACTION, LC.UUID, LC.NAME}
        optional = {LC.STARTED_AT, LC.UPDATED_AT, LC.LAST_ERROR}
        require(isinstance(item, dict) and required <= set(item) <= required | optional,
                TEXT.ERROR_JOURNAL_FIELDS)
        require(item[LC.ACTION] in (LC.ACTION_SAVE, LC.ACTION_RESTORE), TEXT.ERROR_JOURNAL_FIELDS)
        require(isinstance(item[LC.NAME], str) and bool(item[LC.NAME]), TEXT.ERROR_JOURNAL_FIELDS)
        item[LC.UUID] = canonical_uuid(item[LC.UUID])
        for key in optional & set(item):
            require(item[key] is None or isinstance(item[key], str), TEXT.ERROR_JOURNAL_FIELDS)
        return item
    except (ValueError, ValidationError) as exc:
        raise ValidationError(TEXT.ERROR_JOURNAL.format(path, exc)) from exc


def assert_no_active_operation(ident):
    # A timed-out client can leave a server-side operation; VM state is checked too.
    for process in Path(LC.PROC_ROOT).iterdir():
        if not process.name.isdigit():
            continue
        try:
            if process.stat().st_uid != os.geteuid():
                continue
            args = (process / LC.CMDLINE).read_bytes().split(LC.NUL)
            if args and Path(os.fsdecode(args[0])).name == LC.VBOX_MANAGE_NAME:
                decoded = [os.fsdecode(arg) for arg in args]
                require(not (ident in decoded and any(cmd in decoded for cmd in
                            (LC.VBOX_CONTROL, LC.VBOX_START))), TEXT.ERROR_ACTIVE_OPERATION.format(ident))
        except (FileNotFoundError, ProcessLookupError):
            continue


def recovery_plan(c, requested=None, path=None):
    path = Path(path) if path else Path(c[LC.STATE_PATH] + LC.PENDING_SUFFIX)
    pending = read_pending(path)
    result = {LC.PENDING: pending, LC.RESOLUTION: LC.NO_PENDING, LC.VM_STATE: None}
    saved, _ = read_state(c[LC.STATE_PATH])
    if pending is None:
        return result, saved
    action, ident = pending[LC.ACTION], pending[LC.UUID]
    require(requested is None or requested == action,
            TEXT.ERROR_ACTION_MISMATCH.format(action, requested))
    assert_no_active_operation(ident)
    first = vm_state(c, ident)
    registered = vbox(c, LC.VBOX_LIST, LC.VBOX_VMS)
    running = vbox(c, LC.VBOX_LIST, LC.VBOX_RUNNING_VMS)
    second = vm_state(c, ident)
    require(ident in registered and first == second and second in (LC.VM_RUNNING, LC.VM_SAVED)
            and ((ident in running) == (second == LC.VM_RUNNING)),
            TEXT.ERROR_RECOVERY_STATE.format(ident, second))
    assert_no_active_operation(ident)
    processes, process_warnings = process_inventory()
    require(not process_warnings, TEXT.WARNING_PROCESS.format(process_warnings))
    require(not (ident in processes and second != LC.VM_RUNNING), TEXT.ERROR_PROCESS_CONFLICT.format(ident))
    result[LC.JOURNAL_PATH] = str(path)
    done = second == (LC.VM_SAVED if action == LC.ACTION_SAVE else LC.VM_RUNNING)
    result.update({LC.VM_STATE: second, LC.RESOLUTION: LC.RESOLVED if done else LC.RETRY})
    if done:
        if action == LC.ACTION_SAVE:
            if ident not in [entry[LC.UUID] for entry in saved]:
                saved.append({LC.UUID: ident, LC.NAME: registered[ident]})
        else:
            saved = [entry for entry in saved if entry[LC.UUID] != ident]
    return result, saved


def commit_recovery(c, result, saved):
    if result[LC.RESOLUTION] == LC.RESOLVED:
        write_state(c[LC.STATE_PATH], saved)
        path = Path(result.get(LC.JOURNAL_PATH, c[LC.STATE_PATH] + LC.PENDING_SUFFIX))
        path.unlink()
        sync_directory(path.parent)


def recover(c, execute_mode=False):
    require(pwd.getpwuid(os.geteuid()).pw_name == c[LC.VM_OWNER], TEXT.ERROR_VM_OWNER.format(c[LC.VM_OWNER]))
    def reconcile():
        results, failures = [], []
        saved, _ = read_state(c[LC.STATE_PATH])
        for ident, (path, item) in pending_journals(c).items():
            try:
                result, updated = recovery_plan(c, path=path)
            except (ValidationError, ValueError, subprocess.SubprocessError) as exc:
                failures.append({LC.UUID: ident, LC.ERROR: str(exc)})
                continue
            if execute_mode:
                commit_recovery(c, result, updated)  # Persistence errors stop all recovery.
                saved = updated
            elif result[LC.RESOLUTION] == LC.RESOLVED:
                if item[LC.ACTION] == LC.ACTION_SAVE and ident not in [e[LC.UUID] for e in saved]:
                    saved.append({LC.UUID: ident, LC.NAME: item[LC.NAME]})
                elif item[LC.ACTION] == LC.ACTION_RESTORE:
                    saved = [e for e in saved if e[LC.UUID] != ident]
            results.append(result)
        return {LC.READ_ONLY: not execute_mode, LC.RECOVERY: results,
                LC.FAILED: failures, LC.ERROR: bool(failures),
                LC.SAVED_BY_SERVICE: saved, LC.EXECUTED_ACTIONS: 0}
    if execute_mode:
        with operation_lock(c[LC.LOCK_PATH]):
            return reconcile()
    return reconcile()


def journal_failure(path, exc):
    try:
        pending = read_pending(path)
        if pending is not None:
            pending[LC.LAST_ERROR] = str(exc)
            pending[LC.UPDATED_AT] = utc_now()
            atomic_json(path, pending)
    except (OSError, ValueError, ValidationError):
        # Preserve the existing durable intent if even diagnostic persistence fails.
        pass


def execute(c, action, force=False, continue_mode=False):
    require(action in (LC.ACTION_SAVE, LC.ACTION_RESTORE), TEXT.ERROR_VBOX_OPERATION)
    require(pwd.getpwuid(os.geteuid()).pw_name == c[LC.VM_OWNER], TEXT.ERROR_VM_OWNER.format(c[LC.VM_OWNER]))
    report = {LC.STAGE: LC.STAGE_ID, LC.ACTION: action, LC.READ_ONLY: False,
              LC.EXECUTED_ACTIONS: 0, LC.COMPLETED: [], LC.FAILED: [], LC.SKIPPED: [],
              LC.WARNINGS: [], LC.ERROR: None, LC.STOPPED_REASON: None, LC.LOCK_ACQUIRED: False}
    with operation_lock(c[LC.LOCK_PATH]):
        report[LC.LOCK_ACQUIRED] = True
        journals = pending_journals(c)  # Corrupt shared journal metadata stops execution.
        retry = {}
        if continue_mode:
            report[LC.RECOVERY] = []
            for ident, (path, item) in journals.items():
                try:
                    result, saved = recovery_plan(c, action, path)
                except (ValidationError, ValueError, subprocess.SubprocessError) as exc:
                    report[LC.FAILED].append({LC.UUID: ident, LC.NAME: item[LC.NAME], LC.ERROR: str(exc)})
                    continue
                commit_recovery(c, result, saved)  # Never continue after a state write failure.
                report[LC.RECOVERY].append(result)
                if result[LC.RESOLUTION] == LC.RETRY:
                    retry[ident] = path
        plan = inspect(c, action, force)
        report[LC.WARNINGS] = plan[LC.WARNINGS]
        report[LC.SKIPPED].extend(plan[LC.EXCLUDED_VMS] if action == LC.ACTION_SAVE else plan[LC.RESTORE_SKIPPED])
        saved, _ = read_state(c[LC.STATE_PATH])
        queue = plan[LC.SAVE_QUEUE if action == LC.ACTION_SAVE else LC.RESTORE_QUEUE]
        active = pending_journals(c)
        for ident, (path, item) in active.items():
            if ident not in retry:
                report[LC.SKIPPED].append({LC.UUID: ident, LC.NAME: item[LC.NAME], LC.REASON: LC.JOURNAL_BLOCKED})
        for ident in retry:
            if ident not in [e[LC.UUID] for e in queue]:
                report[LC.SKIPPED].append({LC.UUID: ident, LC.REASON: TEXT.ERROR_RETRY_FILTER.format(ident)})
        queue = [e for e in queue if e[LC.UUID] not in active or e[LC.UUID] in retry]
        queue.sort(key=lambda e: e[LC.UUID] not in retry)
        if queue:
            write_state(c[LC.STATE_PATH], saved)  # Preflight persistence, before any VM operation.
        for entry in queue:
            ident = entry[LC.UUID]
            # Power-data failures are global; do not bury them in per-VM errors.
            if not power_allows(c, action, force):
                report[LC.STOPPED_REASON] = TEXT.ERROR_POWER
                break
            try:
                expected = LC.VM_RUNNING if action == LC.ACTION_SAVE else LC.VM_SAVED
                actual = vm_state(c, ident)
                require(actual == expected, TEXT.ERROR_VM_STATE.format(ident, expected, actual))
                processes, warnings = process_inventory()
                report[LC.WARNINGS].extend(warnings)
                if action == LC.ACTION_RESTORE:
                    require(ident not in processes, TEXT.ERROR_PROCESS_CONFLICT.format(ident))
                if action == LC.ACTION_SAVE:
                    require(ident not in [x[LC.UUID] for x in saved], TEXT.ERROR_TRACKED_RUNNING.format(ident))
                else:
                    require(ident in [x[LC.UUID] for x in saved], TEXT.ERROR_SAVED_ENTRY)
                assert_no_active_operation(ident)
            except (OSError, ValueError, ValidationError, subprocess.SubprocessError) as exc:
                report[LC.FAILED].append({**entry, LC.ERROR: str(exc)})
                continue
            if not power_allows(c, action, force):
                report[LC.STOPPED_REASON] = TEXT.ERROR_POWER
                break
            base = Path(c[LC.STATE_PATH] + LC.PENDING_SUFFIX)
            pending = retry.get(ident, base if not base.exists() else Path(str(base) + '.' + ident))
            atomic_json(pending, {LC.ACTION: action, LC.UUID: ident, LC.NAME: entry[LC.NAME],
                                  LC.STARTED_AT: utc_now(), LC.LAST_ERROR: None})
            try:
                mutate_vm(c, action, ident)
                expected_after = LC.VM_SAVED if action == LC.ACTION_SAVE else LC.VM_RUNNING
                actual = vm_state(c, ident)
                require(actual == expected_after, TEXT.ERROR_VM_STATE.format(ident, expected_after, actual))
            except (OSError, ValueError, ValidationError, subprocess.SubprocessError) as exc:
                item = read_pending(pending)
                item.update({LC.LAST_ERROR: str(exc), LC.UPDATED_AT: utc_now()})
                atomic_json(pending, item)  # Failure to persist is global, not a per-VM skip.
                report[LC.FAILED].append({**entry, LC.ERROR: str(exc)})
                continue
            updated = saved + [entry] if action == LC.ACTION_SAVE else [x for x in saved if x[LC.UUID] != ident]
            write_state(c[LC.STATE_PATH], updated)
            saved = updated
            report[LC.COMPLETED].append(entry)
            report[LC.EXECUTED_ACTIONS] += 1
            pending.unlink()
            sync_directory(pending.parent)
        report[LC.PENDING_OPERATIONS] = [item for path, item in pending_journals(c).values()]
    problems = bool(report[LC.FAILED] or report[LC.PENDING_OPERATIONS] or report[LC.WARNINGS])
    report[LC.ERROR] = problems
    report[LC.OUTCOME] = (LC.PARTIAL if report[LC.COMPLETED] else LC.FAILURE) if problems else (
        LC.PARTIAL if report[LC.COMPLETED] and report[LC.STOPPED_REASON] else
        LC.SUCCESS if report[LC.COMPLETED] else LC.NO_ACTION)
    report[LC.SUMMARY] = TEXT.SUMMARY_EXECUTION.format(len(report[LC.COMPLETED]), len(report[LC.FAILED]),
                                                      len(report[LC.SKIPPED]), report[LC.OUTCOME])
    return report



def automatic(c, execute_mode=False):
    if not execute_mode:
        return inspect(c)
    power = read_power(c)
    if not power[LC.AC_ONLINE] and power[LC.CAPACITY] <= c[LC.SAVE_THRESHOLD]:
        return execute(c, LC.ACTION_SAVE)
    if power[LC.AC_ONLINE] and power[LC.CAPACITY] >= c[LC.RESTORE_THRESHOLD]:
        return execute(c, LC.ACTION_RESTORE)
    return {LC.SUMMARY: TEXT.SUMMARY_IDLE, LC.POWER: power,
            LC.SELECTED_ACTION: LC.ACTION_NONE, LC.EXECUTED_ACTIONS: 0,
            LC.OUTCOME: LC.NO_ACTION, LC.ERROR: None}


def main(argv=None):
    parser = HelpArgumentParser(
        description=TEXT.HELP_DESCRIPTION, epilog=TEXT.HELP_MAIN_EXAMPLES,
        formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument(LC.OPTION_CONFIG, type=Path,
                        default=Path(__file__).resolve().with_name(LC.CONFIG_FILENAME),
                        help=TEXT.HELP_CONFIG)
    commands = parser.add_subparsers(dest=LC.COMMAND, required=True, title=TEXT.HELP_COMMANDS)
    commands.add_parser(LC.STATUS, help=TEXT.HELP_STATUS,
                        description=TEXT.HELP_STATUS, epilog=TEXT.HELP_STATUS_DETAILS)
    check = commands.add_parser(
        LC.CHECK, help=TEXT.HELP_CHECK, description=TEXT.HELP_CHECK,
        epilog=TEXT.HELP_CHECK_EXAMPLES,
        formatter_class=argparse.RawDescriptionHelpFormatter)
    check.add_argument(LC.OPTION_DRY_RUN, action=LC.ACTION_STORE_TRUE, required=True, help=TEXT.HELP_DRY_RUN)
    check.add_argument(LC.OPTION_ACTION, choices=(LC.ACTION_AUTO, LC.ACTION_SAVE, LC.ACTION_RESTORE),
                       default=LC.ACTION_AUTO, help=TEXT.HELP_ACTION)
    check.add_argument(LC.OPTION_FORCE, action=LC.ACTION_STORE_TRUE, help=TEXT.HELP_FORCE)
    for action, help_text in ((LC.ACTION_SAVE, TEXT.HELP_SAVE), (LC.ACTION_RESTORE, TEXT.HELP_RESTORE)):
        command = commands.add_parser(action, help=help_text, description=help_text,
                                      epilog=TEXT.HELP_EXECUTION)
        mode = command.add_mutually_exclusive_group(required=True)
        mode.add_argument(LC.OPTION_DRY_RUN, action=LC.ACTION_STORE_TRUE, help=TEXT.HELP_PREVIEW)
        mode.add_argument(LC.OPTION_EXECUTE, action=LC.ACTION_STORE_TRUE, help=TEXT.HELP_EXECUTE)
        command.add_argument(LC.OPTION_CONTINUE, action=LC.ACTION_STORE_TRUE, help=TEXT.HELP_CONTINUE)
        command.add_argument(LC.OPTION_FORCE, action=LC.ACTION_STORE_TRUE, help=TEXT.HELP_EXECUTION_FORCE)
    recovery_command = commands.add_parser(LC.RECOVER, help=TEXT.HELP_RECOVER,
                                            description=TEXT.HELP_RECOVER, epilog=TEXT.HELP_RECOVER_DETAIL)
    recovery_mode = recovery_command.add_mutually_exclusive_group(required=True)
    recovery_mode.add_argument(LC.OPTION_DRY_RUN, action=LC.ACTION_STORE_TRUE, help=TEXT.HELP_PREVIEW)
    recovery_mode.add_argument(LC.OPTION_EXECUTE, action=LC.ACTION_STORE_TRUE, help=TEXT.HELP_RECOVER_EXECUTE)
    auto_command = commands.add_parser(LC.AUTOMATIC, help=TEXT.HELP_AUTO, description=TEXT.HELP_AUTO)
    auto_mode = auto_command.add_mutually_exclusive_group(required=True)
    auto_mode.add_argument(LC.OPTION_DRY_RUN, action=LC.ACTION_STORE_TRUE, help=TEXT.HELP_PREVIEW)
    auto_mode.add_argument(LC.OPTION_EXECUTE, action=LC.ACTION_STORE_TRUE, help=TEXT.HELP_AUTO_EXECUTE)
    args = parser.parse_args(argv)
    if getattr(args, LC.CONTINUE, False) and not getattr(args, LC.EXECUTE, False):
        parser.error(TEXT.ERROR_CONTINUE_MODE)
    if args.command == LC.CHECK and getattr(args, LC.FORCE, False) and args.action == LC.ACTION_AUTO:
        check.error(TEXT.ERROR_FORCE_ACTION)
    try:
        c = load_config(args.config)
        if args.command in (LC.ACTION_SAVE, LC.ACTION_RESTORE):
            operation = execute if getattr(args, LC.EXECUTE, False) else inspect
            if getattr(args, LC.CONTINUE, False):
                result = execute(c, args.command, args.force, continue_mode=True)
            else:
                result = operation(c, args.command, args.force)
        elif args.command == LC.AUTOMATIC:
            result = automatic(c, args.execute)
        elif args.command == LC.RECOVER:
            result = recover(c, args.execute)
        else:
            result = inspect(c, getattr(args, LC.ACTION, LC.ACTION_AUTO), getattr(args, LC.FORCE, False))
        print(json.dumps(result, ensure_ascii=False, indent=2))
        return LC.EXIT_ERROR if result.get(LC.ERROR) else LC.EXIT_SUCCESS
    except (OSError, ValueError, ValidationError, subprocess.SubprocessError, KeyError) as exc:
        print(TEXT.ERROR_MESSAGE.format(exc), file=sys.stderr)
        return LC.EXIT_ERROR

if __name__ == '__main__':
    sys.exit(main())
