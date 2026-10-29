# zvm-power-manager systemd package

Prepared for Ubuntu 24.04, systemd 255, Python 3.12 and VM owner $USER.
Nothing has been installed or enabled by preparing this package.

## Contents and destinations

| File | Destination |
|---|---|
| zvm_power_manager.py | /opt/zvm-power-manager/zvm_power_manager.py |
| zvm-power-manager.service | /etc/systemd/system/zvm-power-manager.service |
| zvm-power-manager.timer | /etc/systemd/system/zvm-power-manager.timer |
| zvm-power-manager.conf | /etc/tmpfiles.d/zvm-power-manager.conf |
| test_*.py | Optional: /opt/zvm-power-manager/ |
| config.toml | Reference snapshot only; retain your installed configuration |

The updated Python file is REQUIRED: the previous version does not have the
`auto` subcommand used by this service. The existing Python venv is reused.
There are no new dependencies or configuration keys. The reference config retains
command_timeout=120; do not overwrite later local configuration changes.

## Place files without enabling execution

Run from this package directory, when no manager operation is in progress:

```sh
sudo cp -a /opt/zvm-power-manager/zvm_power_manager.py /opt/zvm-power-manager/zvm_power_manager.py.before-systemd
sudo install -m 0644 zvm_power_manager.py /opt/zvm-power-manager/zvm_power_manager.py
sudo install -m 0644 zvm-power-manager.service zvm-power-manager.timer /etc/systemd/system/
sudo install -m 0644 zvm-power-manager.conf /etc/tmpfiles.d/zvm-power-manager.conf
sudo systemd-tmpfiles --create /etc/tmpfiles.d/zvm-power-manager.conf
sudo systemctl daemon-reload
sudo systemd-analyze verify /etc/systemd/system/zvm-power-manager.service /etc/systemd/system/zvm-power-manager.timer
```

The tmpfiles rule prepares the state directory and persistent lock inode at boot.
It does not create/reset state.json or remove pending journals. It uses the
currently configured default state/lock paths; if you customize those paths,
update the tmpfiles rule and service mount requirements accordingly.
Do not delete or replace the lock file while any manager instance is running.

As $USER, check the updated CLI and perform a read-only preview:

```sh
/opt/zvm-power-manager/venv/bin/python /opt/zvm-power-manager/zvm_power_manager.py auto --help
/opt/zvm-power-manager/venv/bin/python /opt/zvm-power-manager/zvm_power_manager.py auto --dry-run
```

These commands do not enable the timer. Do not start the service merely to check
its syntax: starting it performs real save/restore operations when power permits.

## Enable only as a separate, deliberate step

After reviewing the preview and deciding to enable automatic VM operations:

```sh
sudo systemctl enable --now zvm-power-manager.timer
```

The first activation is one minute after boot (or promptly if that time has
already passed when the timer is enabled). Subsequent activations occur about
30 seconds after the previous service invocation finishes. Long VM operations
therefore do not overlap or build a backlog. The common application lock also
coordinates with manual executions. The service is not enabled separately.

Policy: on battery <=30%, save; on AC >=40%, restore. Actual thresholds are read
from config.toml on each run. Power is rechecked before each VM operation.
Neither --force nor --continue is used. Pending VMs remain blocked, but other
eligible VMs continue. Partial failures exit nonzero and remain visible in the
journal; the timer schedules another check. There is no immediate Restart loop.

## Inspect and stop scheduling

```sh
systemctl status zvm-power-manager.timer zvm-power-manager.service
systemctl list-timers zvm-power-manager.timer
journalctl -u zvm-power-manager.service -n 100 --no-pager
journalctl -u zvm-power-manager.service -f
sudo systemctl disable --now zvm-power-manager.timer
```

Disabling the timer stops future scheduling, not an operation already in progress.
Prefer waiting for an active operation to finish. If you deliberately stop the
service, an external VBoxManage operation may continue and a pending journal may
remain; inspect/recover it before retrying that VM.

## Runtime choices and limitations

- User/Group and HOME/USER/LOGNAME are fixed to $USER to reach the same VM inventory.
- PrivateTmp=no preserves the host VirtualBox IPC namespace. Do not add an isolated
  /tmp or process sandbox around the VirtualBox launcher.
- KillMode=process deliberately avoids killing VBoxSVC/headless VM descendants
  when the oneshot policy process exits. Those descendants can outlive the unit;
  stopping this service is not a method for stopping VMs.
- TimeoutStartSec=infinity avoids truncating a long multi-VM queue. Each VBoxManage
  call still uses the configured command_timeout. There is no whole-queue timeout.
- Output/errors go to journald. No VM execution was performed during package tests.
- GNOME/UPower settings are unchanged. This service does not inhibit host sleep or
  implement shutdown ordering; these remain separate integration considerations.

Validation: 50 simulated tests passed; systemd-analyze verify accepted both units.
The automatic mode and timer have not yet been tested through real systemd
activation or reboot. Existing manual save/restore was tested previously by you.
