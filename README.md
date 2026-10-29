# zvm-power-manager — ST01.3

Python 3.12, standard library only. Run as the configured VM owner .
No systemd services or timers are installed. GNOME/UPower are not modified.

## Commands

From `/opt/zvm-power-manager`:

```sh
./venv/bin/python ./zvm_power_manager.py status
./venv/bin/python ./zvm_power_manager.py check --dry-run
./venv/bin/python ./zvm_power_manager.py save --dry-run
./venv/bin/python ./zvm_power_manager.py restore --dry-run
./venv/bin/python ./zvm_power_manager.py save --help
```

`status`, `check` and `--dry-run` never write state/locks or change VMs.
Preview queues are candidates; execution checks actual VM state again.
Global `--config /absolute/path/config.toml` must precede the command.
All user-facing help and diagnostics are English; literals are in LC/TEXT.

Real operations require an explicit choice:

```sh
./venv/bin/python ./zvm_power_manager.py save --execute
./venv/bin/python ./zvm_power_manager.py restore --execute
```

These commands change real VMs. Real-host execution has NOT been acceptance-tested;
only simulated VM execution has been tested for this stage.

`--force` bypasses only power eligibility. It never bypasses validation, ownership,
locks, pending recovery, VM states, save filtering or restore membership.
It still requires readable, valid power data.

Save requires AC offline and charge <=30%; restore requires AC online and >=40%.
These thresholds are configurable. Power is checked before each VM operation.
Save order: VM1, ..., VMN, then remaining running VMs when
`include_other_running_vms = true`. With false, only running names in save_order
are selected. Restore order: VMN, ..., VM1, then the remaining
service-recorded entries. Restore only starts VMs currently in `saved` state,
using `--type headless`; powered-off VMs are not cold-started.

## Persistence and failures

Only explicit execution acquires the nonblocking exclusive lock at lock_path.
The lock file remains on disk after release; it must not be deleted during use.
Parent directories for state_path and lock_path must exist and be writable by
the VM owner. Execution verifies state persistence before changing a VM.

State schema remains version 1:

```json
{"schema_version": 1, "saved_by_service": []}
```

Missing, empty and whitespace-only state files mean no recorded VMs. Malformed
nonempty files fail with a path and diagnostic. They are never silently reset.
After each successful operation and VM-state verification, state.json is written
via a temporary file, fsync and atomic rename. Saved entries are appended after
saving and removed after restoration. Execution stops on the first error;
previous successful operations remain committed.

Before each VM operation, a durable `<state_path>.pending` journal records its
action, UUID and name. A timeout, interruption or persistence error may leave this
journal. Subsequent execution refuses to proceed, including with --force.
Do not simply delete it: inspect the recorded VM's actual state and state.json,
reconcile whether the operation completed, then remove the journal under the
same exclusive lock. ST01.3 intentionally has no automatic recovery command.
This prevents an uncertain operation from being blindly retried or forgotten.

`command_timeout` applies to VBoxManage reads AND operations (currently 20 seconds).
Before real acceptance testing, choose a suitable timeout for the VM sizes
(up to 300 seconds). A timed-out client does not prove the VM operation stopped;
the pending journal is retained for reconciliation.

All manager instances must use the same lock_path. External VBoxManage/GUI changes
are not covered by that lock; avoid concurrent manual VM operations during execution.

Output is JSON. Exit 0 means inspection/execution finished, including an empty
queue or a power-policy stop (see stopped_reason); exit 1 means an error;
exit 2 means invalid CLI arguments. Partial execution reports completed entries
and an error. Unregistered or already running restore entries remain recorded
and are reported by status/check for manual reconciliation.

## Validation

```sh
./venv/bin/python -B -m unittest discover -s /opt/zvm-power-manager -v
```

Execution tests mock subprocess calls and use temporary state/lock files.
They never call the real VBoxManage executable.

VirtualBox command reference:
https://docs.oracle.com/en/virtualization/virtualbox/7.2/user/vboxmanage.html

## Pending-operation recovery (supersedes manual-only recovery above)

```sh
./venv/bin/python ./zvm_power_manager.py recover --dry-run
./venv/bin/python ./zvm_power_manager.py recover --execute
./venv/bin/python ./zvm_power_manager.py save --execute --continue
./venv/bin/python ./zvm_power_manager.py restore --execute --continue
```

`recover --dry-run` is a read-only snapshot. `recover --execute` takes the common
lock, rereads state and reconciles a confirmed completed operation; it never
starts or saves a VM. A completed save adds the UUID only if missing; a completed
restore removes only that UUID. Other saved entries remain intact. The journal
is removed only after the updated state is durably written. An incomplete
operation is reported as `retry_required` and its journal remains untouched.

`--continue` requires `--execute`, and the command must match the journal action.
A confirmed completion is reconciled before processing the remaining queue.
An incomplete operation is retried first, then the normal queue continues.
Without a journal, --continue behaves like normal execution. Power policy and
save filtering still apply; --force bypasses only power policy. Blocked retries
retain the journal. --continue cannot be combined with --dry-run; preview recovery
with `recover --dry-run` instead.

Recovery compares two VM-state reads with registered/running inventories and
refuses transitional, unexpected, inconsistent or missing states. It also refuses
recovery while a same-user VBoxManage control/start process for the UUID is active.
These checks cannot lock out external GUI/API actions: do not concurrently manage
the affected VM, and do not recover while the VirtualBox control connection is
known to be inconsistent. Manager instances coordinate through lock_path.

New journals include started_at (UTC), last_error, and updated_at on failure.
Old journals containing only action/uuid/name remain supported. If a storage
failure prevents recording an error, the original durable journal is preserved.
`state.json` stays at schema version 1. No automatic journal deletion or clean
command is provided.

## Partial execution and inventory diagnostics (current behavior)

This section supersedes earlier instructions to stop at the first VM error or
block all execution while any journal exists.

JSON remains the output format. Preview output now starts with `summary` and
includes `running_vms`, `excluded_vms`, `warnings` and `pending_operations`.
`save_queue`/`restore_queue` show candidates. `planned_queue` excludes pending
VMs and applies the power gate; `--continue` execution separately reconciles them.
A visible VM frontend process missing from VBoxManage running inventory is
reported explicitly and is never used as an alternative command target.
A process discrepancy for one VM does not prevent work on other available VMs.
Process inspection is best-effort; unreadable process metadata is reported.
Do not invoke the system VBoxManage shell launcher inside a process-isolated
sandbox: its automatic IPC cleanup may disrupt the host control connection.

Execution output includes `completed`, `failed`, `skipped`, `warnings`,
`pending_operations`, `summary` and `outcome`:
- `success`: completed without reported operational problems;
- `partial_success`: some VMs completed but problems or a power stop remain;
- `failed`: problems were reported with no confirmed completed operations;
- `no_action`: no operations completed and no operational errors were reported.
Exit status is 1 when VM failures, unresolved journals or inventory warnings remain;
otherwise 0 (including a power-policy stop, recorded in stopped_reason).
Filtered or already-running VMs are explained as skips rather than VM failures.

A per-VM state/read/save/restore failure is recorded and the queue continues.
Timeout or uncertain post-operation verification retains a durable pending record.
Shared failures (lock, malformed state/journal metadata, state/journal persistence,
or invalid/unreadable power data) abort further work. Previous durable results
remain in state.json even if a global error prevents a final JSON report.

The original `state.json.pending` format remains valid. When another operation
must be journaled while that file is occupied, the service uses
`state.json.pending.<VM-UUID>`. Each file contains one operation. A successful
operation removes only its own journal. Backups with non-UUID suffixes are ignored.
Do not manually rename active journals to backups to bypass reconciliation.
Duplicate journals for one UUID are rejected. State schema remains unchanged.

`recover` returns a list in `recovery` and per-VM reconciliation errors in `failed`.
It continues reconciling independent journals when one VM is unresolved. A shared
write error stops reconciliation. `recover --dry-run` writes nothing and shows
all proposed additions/removals while preserving unrelated saved entries.
`--continue` retries matching unresolved operations first, then other candidates.
Mismatching, filtered or unresolvable pending VMs remain recorded and are skipped;
they do not block other eligible VMs. Without --continue, only the pending VMs
are blocked. Neither --force nor --continue bypasses per-VM consistency checks.
