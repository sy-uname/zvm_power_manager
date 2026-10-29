# Installation and updates

Run `install.py` from the repository checkout. The installer itself requires
Python 3.11 or newer; the application interpreter must be Python 3.12 or newer.
It uses only the standard library and never downloads Python or runs apt/pip.
Install the desired Python interpreter and its venv support beforehand.

The installer manages one system-wide `zvm-power-manager.service` and timer,
executed as the selected VM owner. It does not create multiple per-user service
instances. Code and venv default to `/opt/zvm-power-manager`.

## First installation

Preview as the normal user:

```sh
python3 install.py --user "$USER" \
  --config "$HOME/.config/zvm-power-manager/config.toml" \
  --python 3.12 --dry-run
```

Apply the same plan with sudo, without `--dry-run`:

```sh
sudo python3 install.py --user "$USER" \
  --config "$HOME/.config/zvm-power-manager/config.toml" --python 3.12
```

Shell expansion happens before sudo, so these arguments refer to the invoking
user. A missing config is generated from `app/config.toml.sample` with `vm_owner`
set to that user. Review VM names, power paths, thresholds, state and lock paths
before enabling the timer. Existing configurations are preserved byte-for-byte.
The sample's VM1/VM2 names must be replaced with real VM names.

The installer creates required directories, a persistent lock file, the venv,
code, systemd units and a tmpfiles rule. It does not create/reset `state.json`
or remove pending journals. It does not change GNOME or UPower settings.
This installer requires `lock_path` directly under `/run/lock`.

Enable automation only after reviewing and testing the configuration:

```sh
sudo python3 install.py --user "$USER" \
  --config "$HOME/.config/zvm-power-manager/config.toml" --update --enable
```

`--enable` enables and starts the timer, which can trigger real VM operations.
A first installation with a newly generated config cannot use `--enable`.

## Existing installation

Always pass the existing configuration path. For the original deployment:

```sh
python3 install.py --user "$USER" --config /opt/zvm-power-manager/config.toml \
  --update --dry-run
sudo python3 install.py --user "$USER" --config /opt/zvm-power-manager/config.toml \
  --update
```

Updates preserve config, state, pending journals and the existing venv by default.
An active timer is temporarily stopped and restarted after the update; restarting
can trigger the normal VM power policy. An inactive timer is not started unless
`--enable` is supplied. A running service or busy application lock causes refusal;
the installer never kills an ongoing save/restore. Retry once it finishes.

Use `--prefix /absolute/path` for a nondefault code location. Updating a different
service owner or switching its config path is refused; migrations are separate.
Do not change state/lock paths as part of a routine update.

## Selecting Python

`--python` accepts a version (`3.12`), executable name (`python3.12`) or absolute
executable path (`/usr/bin/python3.12`). Without this option, updates use the
existing venv, and new installations use `python3.12`.

Changing an existing venv's interpreter requires explicit reconstruction:

```sh
sudo python3 install.py --user "$USER" --config /opt/zvm-power-manager/config.toml \
  --update --python /usr/bin/python3.13 --rebuild-venv
```

The selected interpreter must already exist and be able to create a venv. The
venv is created without pip because the application has no external dependencies.
The installer checks interpreter version, config, imports, help and systemd units.
It does not invoke VBoxManage or run the application's VM execution commands.

## Failure recovery and verification

Replaced files and any rebuilt venv are retained under
`<prefix>/install-backup-*/`; `manifest.json` maps file backups to their targets.
On a caught installation error, the installer attempts to restore replaced files,
the prior venv and timer activity. New directories and the persistent lock can
remain. This is not a transactional guarantee against power loss or SIGKILL.
A timer restart failure is reported with a nonzero exit status even if files were
successfully installed. Inspect the reported backup directory before manual recovery.

`--dry-run` validates source/config using temporary files and prints a plan; it
never changes the installed files, VM state or systemd. It does not establish
that root permissions, systemd or actual venv creation will succeed.

Installer tests use temporary directories and simulated system commands:

```sh
python3.12 -B -m unittest -v test_installer
```
