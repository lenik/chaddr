# SPDX-FileCopyrightText: 2026 Lenik <lenik@bodz.net>
# SPDX-License-Identifier: AGPL-3.0-or-later
#
# Unit tests for chaddr (unittest + meson test).

from __future__ import annotations

import unittest

from chaddr.address import is_ipv4, is_ipv6, parse_address_set
from chaddr.i18n import TEXT_DOMAIN, init_i18n
from chaddr.profile import (
    AddrHistoryRecord,
    parse_addr_history_arg,
    parse_history_addr_args,
)
from chaddr.profile_lexer import parse_shell_words


class AddressTests(unittest.TestCase):
    def test_is_ipv4(self) -> None:
        self.assertTrue(is_ipv4("203.0.113.10"))
        self.assertFalse(is_ipv4("2001:db8::1"))

    def test_is_ipv6(self) -> None:
        self.assertTrue(is_ipv6("2001:db8::1"))
        self.assertFalse(is_ipv6("203.0.113.10"))

    def test_parse_address_set(self) -> None:
        addrs = parse_address_set("203.0.113.10", "2001:db8::1")
        self.assertEqual(addrs.ipv4, "203.0.113.10")
        self.assertEqual(addrs.ipv6, "2001:db8::1")


class HistoryAddrTests(unittest.TestCase):
    def test_parse_history_addr_args(self) -> None:
        raw = '203.0.113.10 "2026-06-24 12:00:00" "2026-06-25 09:15:00"'
        records = parse_history_addr_args(parse_shell_words(raw))
        self.assertEqual(len(records), 1)
        self.assertEqual(records[0].address, "203.0.113.10")
        self.assertEqual(records[0].insert_time, "2026-06-24 12:00:00")
        self.assertEqual(records[0].event_time, "2026-06-25 09:15:00")

    def test_format_line_quotes(self) -> None:
        record = AddrHistoryRecord(
            "198.51.100.4",
            insert_time="2026-06-24 12:00:00",
            event_time="2026-06-20 09:00:00",
        )
        self.assertEqual(
            record.format_line(),
            'history-addr: 198.51.100.4 "2026-06-24 12:00:00" "2026-06-20 09:00:00"',
        )

    def test_legacy_addr_history(self) -> None:
        records = parse_addr_history_arg("198.51.100.4 2026-06-24 12:00:00")
        self.assertEqual(len(records), 1)
        self.assertEqual(records[0].insert_time, "2026-06-24 12:00:00")
        self.assertEqual(records[0].event_time, "")


class I18nTests(unittest.TestCase):
    def test_init_i18n_fallback(self) -> None:
        trans = init_i18n("")
        self.assertEqual(TEXT_DOMAIN, "chaddr")
        self.assertEqual(trans.gettext("Increase logging verbosity"), "Increase logging verbosity")


if __name__ == "__main__":
    unittest.main()
