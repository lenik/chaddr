# chaddr

Change or reallocate IP addresses defined in profile files. chaddr coordinates
AWS and Aliyun Elastic IPs, Namecheap registered nameservers, `/etc/hosts` (and
similar files), and BIND zone databases from a single profile.

Homepage: https://github.com/lenik/chaddr

## Features

- **GUI** — profile picker, address CRUD, diagnose, renew, and apply with live
  logging and diagnostics tabs
- **CLI** — `--diagnose`, `--renew`, and manual `--apply` for scripting
- **Profiles** — declarative multi-resource definitions under `~/.config/chaddr/profile/`
- **Config** — JSON file for API keys, proxy, and cached client IP
- **Elevation** — GUI prompts via `pkexec`, `gksudo`, or `kdesudo` when writing
  protected files

## Requirements

- Python 3.11+
- wxPython 4.2+ (GUI)
- boto3, requests, PySocks

On Debian/Ubuntu you can install dependencies with:

```bash
sudo apt install python3 python3-wxgtk4.0 python3-boto3 python3-requests python3-socks
```

For privileged writes, install at least one of: `policykit-1` (`pkexec`),
`gksudo`, or `kde-cli-tools` (`kdesudo`).

## Quick start

```bash
mkdir -p ~/.config/chaddr
cp chaddr.conf.template ~/.config/chaddr/chaddr.conf
# edit ~/.config/chaddr/chaddr.conf with your API credentials

mkdir -p ~/.config/chaddr/profile
cp profile/example ~/.config/chaddr/profile/   # example profile

python3 chaddr.py --diagnose -v example   # CLI
python3 chaddr.py example                 # GUI
```

For a git checkout, profiles in `./profile/` are copied into `~/.config/chaddr/profile/` on first run when that directory is empty. Override the location with `CHADDR_PROFILE_DIR`.

Or after installation:

```bash
chaddr --diagnose example
chaddr
```

The Debian package installs `/usr/bin/chaddr` and example profiles under `/usr/share/chaddr/profile/`. On first run, empty `~/.config/chaddr/profile/` is seeded from those examples.

## Configuration

Copy `chaddr.conf.template` to `chaddr.conf` in the project directory or to
`~/.config/chaddr/chaddr.conf`.

| Key | Purpose |
|-----|---------|
| `proxy` | HTTP/SOCKS proxy URL |
| `aws_access_key_id`, `aws_secret_access_key` | AWS credentials |
| `namecheap_api_key`, `namecheap_username`, `namecheap_password` | Namecheap API and portal |
| `namecheap_whitelist` | `disabled` (default) or `portal` |
| `client_ip`, `client_ip_expire` | Cached public IP (auto-detected, 1h TTL) |
| `aliyun_access_key_id`, `aliyun_access_key_secret` | Aliyun credentials |

Pass `-c /path/to/chaddr.conf` or set credentials via profile `# option --key`
comments.

## Profiles

Profiles are plain-text files in `~/.config/chaddr/profile/` by default. Example profiles ship in the git `profile/` directory and, when packaged, in `/usr/share/chaddr/profile/`. Example `~/.config/chaddr/profile/example`:

```
description: Example relay
version: 1
addr-history: 198.51.100.4 2001:db8::1

from: resolve
resolve: relay.example.com

type: aws elastic ip
region: us-east-1

type: aliyun elastic ip
region: cn-hangzhou

type: registered nameserver
api: namecheap
ns: ns1.example.com, ns2.example.com

type: hosts file
path: /etc/hosts

type: zone file
path: /var/cache/bind/db.example.com
```

Supported `type` values:

| Type | Action |
|------|--------|
| `aws elastic ip` | Diagnose, renew (reallocate EIP) |
| `aliyun elastic ip` | Diagnose, renew |
| `registered nameserver` | Diagnose, apply (Namecheap NS IP) |
| `hosts file` | Diagnose, apply (replace old IP in file) |
| `zone file` | Diagnose, apply (replace A record IP) |

Use `from: resolve` with `resolve: hostname` to discover current addresses.
Optional header fields before the first `from:` / `type:` block include `description:`,
`version:`, and `addr-history:` (whitespace-separated historical IPv4/IPv6 addresses).
Lines may continue on the next line with a trailing `\`.
`addr-history` works like `--old-ip` when matching old IPs in hosts files and zone files.
The GUI and CLI accept spare “from” addresses to locate old IPs in hosts files.

## CLI usage

```
chaddr [OPTIONS] [PROFILE...]
```

| Option | Description |
|--------|-------------|
| `-c`, `--config FILE` | JSON config file |
| `--proxy URL` | Proxy for API calls |
| `--diagnose` | Run checks only |
| `--renew` | Reallocate elastic IPs |
| `--apply IP` | Manual apply (IPv4 or IPv6) |
| `--apply-ipv4`, `--apply-ipv6` | Manual apply by family |
| `--old-ip IP` | Old IP when auto-detection fails |
| `-v`, `--verbose` | More logging (repeat for debug) |
| `--no-gui` | Force CLI mode |

Examples:

```bash
chaddr --diagnose -v example
chaddr --apply 203.0.113.10 --old-ip 198.51.100.4 example
chaddr --renew example
```

## GUI usage

- **Diagnose** — check all resources; opens the right pane on the Diagnostics tab
- **Renew** — reallocate elastic IPs where supported
- **Apply** — write new addresses to manual resource types (single profile)
- **File → Browse…** — switch to another profile directory
- **View → Right Pane** (`Ctrl+H`) — toggle logging/diagnostics notebook

The profile list title shows the active directory, for example `Profile: ~/.config/chaddr/profile/`.

## Shell completion

After installation, bash completion is registered automatically. For a git
checkout:

```bash
source completion/chaddr
```

Set `CHADDR_PROFILE_DIR` to override the profile directory (default:
`~/.config/chaddr/profile/`).

## Building and installing

Meson configures install paths and writes `chaddr/buildconfig.py` at build time
(`PROFILE_DIR`, `DOC_DIR`, `SYSCONFDIR`, `VERSION`, etc.).

```bash
meson setup build
meson compile -C build
sudo meson install -C build
```

Useful options (see `meson configure build/`):

| Option | Default |
|--------|---------|
| `--prefix` | `/usr/local` |
| `-Dprofile_dir=` | `datadir/chaddr/profile` |
| `-Ddoc_dir=` | `datadir/doc/chaddr/examples` |
| `-Dbashcompletiondir=` | `datadir/bash-completion/completions` |

Version comes from `scripts/git-version` (same rules as `git describe`).

## Building a Debian package

```bash
sudo apt install debhelper meson ninja-build python3-all bash-completion
dpkg-buildpackage -us -uc -b
```

Install the resulting `.deb` with `sudo dpkg -i ../chaddr_*.deb`. This installs
the `chaddr` command to `/usr/bin/chaddr`, bash completion, the man page, and
example profiles under `/usr/share/chaddr/profile/`.

## Development

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
CHADDR_PROFILE_DIR=./profile python3 chaddr.py   # optional: use checkout profiles
python3 chaddr.py
```

Or install from a Meson build tree:

```bash
meson setup build --prefix=$HOME/.local
meson install -C build
```

## License

Copyright (C) 2026 Lenik <chaddr@bodz.net>

This program is free software: you can redistribute it and/or modify it under
the terms of the **GNU Affero General Public License v3.0 or later** (AGPL-3.0).
See [LICENSE](LICENSE) for the full license text.

The upstream `LICENSE` file also includes a **supplemental anti-AI-training
restriction** for this project. If that supplement is held unenforceable, it is
severed and the AGPL continues to apply.
