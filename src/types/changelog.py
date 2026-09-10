"""Debian-style changelog handler — bump version and record IP changes."""

from __future__ import annotations

import os
import pwd
import re
from datetime import datetime
from email.utils import format_datetime
from pathlib import Path

from chaddr.privilege import write_text as write_text_privileged
from chaddr.types.base import (
    AddressTypeHandler,
    DiagnoseItem,
    DiagnoseResult,
    is_ipv4,
    is_ipv6,
)

_HEADER_RE = re.compile(
    r"^(?P<package>\S+)\s+\((?P<version>[^)]+)\)\s+(?P<dist>\S+);(?:\s*urgency=(?P<urgency>\S+))?",
)
_AUTHOR_RE = re.compile(r"^ --\s+(?P<author>.+?)\s{2,}(?P<date>.+)\s*$")
_VERSION_TAIL_RE = re.compile(r"^(?P<head>.*?)(?P<num>\d+)$")


def _default_author() -> str:
    name = os.environ.get("DEBFULLNAME", "").strip()
    email = os.environ.get("DEBEMAIL", "").strip()
    if name and email:
        return f"{name} <{email}>"
    if email:
        return email
    try:
        pw = pwd.getpwuid(os.getuid())
        gecos = (pw.pw_gecos or "").split(",", 1)[0].strip() or pw.pw_name
        return f"{gecos} <{pw.pw_name}@localhost>"
    except KeyError:
        return "chaddr <chaddr@localhost>"


def _bump_version(version: str) -> str:
    match = _VERSION_TAIL_RE.match(version.strip())
    if not match:
        return f"{version}.1"
    head = match.group("head")
    num = int(match.group("num")) + 1
    width = len(match.group("num"))
    return f"{head}{num:0{width}d}" if width > 1 and match.group("num").startswith("0") else f"{head}{num}"


def parse_changelog_top(text: str) -> dict[str, str] | None:
    """Return package/version/dist/urgency/author from the first changelog entry."""
    lines = text.splitlines()
    header = None
    author = None
    for line in lines:
        if header is None:
            match = _HEADER_RE.match(line)
            if match:
                header = match.groupdict()
            continue
        author_match = _AUTHOR_RE.match(line)
        if author_match:
            author = author_match.group("author").strip()
            break
        if line.startswith(" ") or line.startswith("\t") or not line.strip():
            continue
        # Next entry started without an author trailer — stop.
        if _HEADER_RE.match(line):
            break
    if header is None:
        return None
    dist = (header.get("dist") or "unstable").strip()
    # Prefer a released suite when the tip is still UNRELEASED.
    if dist.upper() == "UNRELEASED":
        for line in lines:
            match = _HEADER_RE.match(line)
            if not match:
                continue
            candidate = match.group("dist").strip()
            if candidate.upper() != "UNRELEASED":
                dist = candidate
                break
    return {
        "package": header["package"],
        "version": header["version"],
        "dist": dist,
        "urgency": (header.get("urgency") or "medium").rstrip(","),
        "author": author or _default_author(),
    }


def build_changelog_entry(
    *,
    package: str,
    version: str,
    dist: str,
    urgency: str,
    author: str,
    body_lines: list[str],
    when: datetime | None = None,
) -> str:
    stamp = format_datetime(when or datetime.now().astimezone())
    body = "\n".join(f"  * {line}" for line in body_lines) or "  * Update"
    return (
        f"{package} ({version}) {dist}; urgency={urgency}\n"
        f"\n"
        f"{body}\n"
        f"\n"
        f" -- {author}  {stamp}\n"
    )


def prepend_changelog_entry(text: str, entry: str) -> str:
    body = text if text.endswith("\n") or not text else text + "\n"
    entry_text = entry if entry.endswith("\n") else entry + "\n"
    if not body.strip():
        return entry_text
    return entry_text + "\n" + body.lstrip("\n")


class ChangelogHandler(AddressTypeHandler):
    type_name = "changelog"
    supports_manual_edit = True
    supports_reallocate = False

    def _path(self) -> Path:
        path = (self.config.get("path") or self.config.get("changelog") or "").strip()
        if not path:
            raise RuntimeError("missing path in changelog profile entry")
        return Path(path)

    def _read_text(self) -> str:
        path = self._path()
        if not path.is_file():
            raise FileNotFoundError(f"changelog not found: {path}")
        return path.read_text(encoding="utf-8")

    def diagnose(self) -> DiagnoseResult:
        items: list[DiagnoseItem] = []
        addresses: list[str] = []
        try:
            path = self._path()
            items.append(DiagnoseItem("path", True, str(path)))
        except Exception as exc:
            items.append(
                DiagnoseItem(
                    "path",
                    False,
                    str(exc),
                    'Add a line like "changelog: /path/to/debian/changelog" or '
                    '"type: changelog" / "path: ...".',
                )
            )
            return DiagnoseResult(self.type_name, "path missing", False, items, addresses)

        try:
            text = self._read_text()
            top = parse_changelog_top(text)
            if top is None:
                items.append(
                    DiagnoseItem(
                        "changelog",
                        False,
                        "could not parse top changelog entry",
                        "Ensure the file is a Debian-format changelog.",
                    )
                )
            else:
                items.append(
                    DiagnoseItem(
                        "package",
                        True,
                        f"{top['package']} ({top['version']}) {top['dist']}",
                    )
                )
                items.append(DiagnoseItem("next", True, _bump_version(top["version"])))
        except FileNotFoundError:
            items.append(
                DiagnoseItem(
                    "changelog",
                    False,
                    f"file not found: {path}",
                    "Create the changelog or fix the path.",
                )
            )
        except PermissionError:
            items.append(
                DiagnoseItem(
                    "changelog",
                    False,
                    f"permission denied: {path}",
                    "Run with sufficient privileges or approve the GUI elevation prompt.",
                )
            )
        except Exception as exc:
            items.append(DiagnoseItem("changelog", False, str(exc)))

        ok = all(item.ok for item in items)
        return DiagnoseResult(self.type_name, "ready" if ok else "issues found", ok, items, addresses)

    def apply_manual(self, old_ip: str, new_ip: str) -> bool:
        if not is_ipv4(new_ip) and not is_ipv6(new_ip):
            raise ValueError(f"invalid IP address: {new_ip}")
        if old_ip == new_ip:
            self.logger.info("changelog: old and new IP identical (%s); skipping", new_ip)
            return False
        family = "IPv4" if is_ipv4(new_ip) else "IPv6"
        return self._append_entry([f"chaddr {family} from {old_ip} to {new_ip}"])

    def apply_address_map(self, old, new) -> bool:
        body: list[str] = []
        if old.ipv4 and new.ipv4 and old.ipv4 != new.ipv4:
            body.append(f"chaddr IPv4 from {old.ipv4} to {new.ipv4}")
        if old.ipv6 and new.ipv6 and old.ipv6 != new.ipv6:
            body.append(f"chaddr IPv6 from {old.ipv6} to {new.ipv6}")
        if not body:
            self.logger.info("changelog: no IP changes to record")
            return False
        return self._append_entry(body)

    def _append_entry(self, body_lines: list[str]) -> bool:
        path = self._path()
        text = self._read_text()
        top = parse_changelog_top(text)
        if top is None:
            raise RuntimeError(f"could not parse changelog: {path}")

        new_version = _bump_version(top["version"])
        entry = build_changelog_entry(
            package=top["package"],
            version=new_version,
            dist=top["dist"],
            urgency=top["urgency"],
            author=top["author"],
            body_lines=body_lines,
        )
        new_text = prepend_changelog_entry(text, entry)
        write_text_privileged(path, new_text, encoding="utf-8")
        self.logger.info(
            "Updated %s: %s (%s) -> (%s); %s",
            path,
            top["package"],
            top["version"],
            new_version,
            "; ".join(body_lines),
        )
        return True
