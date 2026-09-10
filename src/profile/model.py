"""Profile data model types."""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path

from chaddr.address import AddressSet
from chaddr.profile.history import (
    AddrHistoryRecord,
    addr_history_records_to_sets,
    parse_addr_history_arg,
)
from chaddr.types import get_handler_class


@dataclass
class ProfileHeader:
    options: dict[str, str] = field(default_factory=dict)
    addr_history_records: list[AddrHistoryRecord] = field(default_factory=list)

    @property
    def description(self) -> str:
        return self.options.get("description", "")

    @property
    def version(self) -> str:
        return self.options.get("version", "")

    @property
    def addr_history(self) -> str:
        if self.addr_history_records:
            return " ".join(record.address for record in self.addr_history_records)
        return self.options.get("addr-history", "")


@dataclass
class ProfileEntry:
    type: str
    options: dict[str, str] = field(default_factory=dict)
    cli_options: list[str] = field(default_factory=list)

    @property
    def config(self) -> dict[str, str]:
        return self.options


@dataclass
class ProfileFromBlock:
    from_type: str
    options: dict[str, str] = field(default_factory=dict)


@dataclass
class ProfileInstruction:
    """One selectable profile AST node (from: or type: block) for the GUI."""

    key: str
    kind: str  # "from" | "type"
    type_name: str
    summary: str
    from_index: int | None = None
    entry_index: int | None = None
    optional: bool = False


def profile_option_truthy(value: str | None) -> bool:
    if value is None:
        return False
    return value.strip().lower() in ("1", "true", "yes", "on")


@dataclass
class Profile:
    name: str
    header: ProfileHeader | None = None
    from_block: ProfileFromBlock | None = None
    entries: list[ProfileEntry] = field(default_factory=list)
    path: Path | None = None
    global_options: list[str] = field(default_factory=list)
    # When set (e.g. session instruction filter), preferred over re-parsing the file.
    from_blocks: list[ProfileFromBlock] | None = None

    @property
    def types(self) -> list[str]:
        return [e.type for e in self.entries]

    def has_manual_types(self) -> bool:
        for entry in self.entries:
            handler_cls = get_handler_class(entry.type)
            if handler_cls and handler_cls.supports_manual_edit:
                return True
        return False

    def allows_manual_edit(self) -> bool:
        for entry in self.entries:
            handler_cls = get_handler_class(entry.type)
            if handler_cls is None or not handler_cls.supports_manual_edit:
                return False
        return bool(self.entries)

    def addr_history_records(self) -> list[AddrHistoryRecord]:
        if self.header is None:
            return []
        if self.header.addr_history_records:
            return list(self.header.addr_history_records)
        return parse_addr_history_arg(self.header.addr_history)

    def addr_history_sets(self) -> list[AddressSet]:
        return addr_history_records_to_sets(self.addr_history_records())

    def with_selected_instructions(self, selected_keys: set[str] | None) -> "Profile":
        """Return a copy limited to selected instruction keys (None = all)."""
        from chaddr.profile.instructions import list_profile_instructions
        from chaddr.profile.parse import profile_from_blocks

        instructions = list_profile_instructions(self)
        if selected_keys is None:
            return self
        all_keys = {item.key for item in instructions}
        if selected_keys >= all_keys:
            return self
        from_blocks = [
            profile_from_blocks(self)[item.from_index]
            for item in instructions
            if item.kind == "from" and item.key in selected_keys and item.from_index is not None
        ]
        entries = [
            self.entries[item.entry_index]
            for item in instructions
            if item.kind == "type" and item.key in selected_keys and item.entry_index is not None
        ]
        from_block = from_blocks[0] if from_blocks else None
        return Profile(
            name=self.name,
            header=self.header,
            from_block=from_block,
            entries=entries,
            path=self.path,
            global_options=list(self.global_options),
            from_blocks=from_blocks,
        )
