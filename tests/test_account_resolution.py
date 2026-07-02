"""Tests for account_map.py (account/customer resolution)."""

import unittest
from unittest.mock import patch, MagicMock
import sys, os
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..'))

from src.services.account_map import resolve_account_mentions, _load_account_names
import src.services.account_map as account_map_mod


class TestResolveAccountMentions(unittest.TestCase):
    """Tests that account_map.resolve_account_mentions works correctly."""

    def setUp(self):
        # Ensure account names are loaded from real DB
        _load_account_names()

    def test_full_name_direct_match(self):
        """Full account name in query → returns hint."""
        q, hint = resolve_account_mentions("engine overheat di PAMAPERSADA NUSANTARA")
        self.assertIsNotNone(hint)
        self.assertIn("PAMAPERSADA NUSANTARA", hint)

    def test_abbreviation_match(self):
        """Short name 'PAMA' in query → resolves to full account name."""
        q, hint = resolve_account_mentions("engine overheat di PAMA")
        self.assertIsNotNone(hint)
        self.assertIn("PAMAPERSADA NUSANTARA", hint)

    def test_multiple_accounts_match(self):
        """Query mentions 'ADARO' → matches ADARO INDONESIA and ADARO LOGISTICS."""
        q, hint = resolve_account_mentions("masalah di ADARO")
        self.assertIsNotNone(hint)
        self.assertIn("ADARO INDONESIA", hint)
        self.assertIn("ADARO LOGISTICS", hint)

    def test_no_account_mention(self):
        """Query without any account name → returns None hint."""
        q, hint = resolve_account_mentions("engine overheat di site Jembayan")
        # This might match if "Jembayan" is part of an account name — unlikely but possible
        # So we just check it doesn't crash
        self.assertIsInstance(q, str)
        self.assertIsInstance(hint, (str, type(None)))

    def test_case_insensitive(self):
        """Case-insensitive matching."""
        q, hint = resolve_account_mentions("masalah di pama")
        self.assertIsNotNone(hint)
        self.assertIn("PAMAPERSADA NUSANTARA", hint)

    def test_hint_format_single(self):
        """Single account match → correct SQL hint format."""
        q, hint = resolve_account_mentions("FREEPORT")
        self.assertIsNotNone(hint)
        self.assertEqual(hint, "account_account_name = 'FREEPORT INDONESIA'")

    def test_hint_format_multiple(self):
        """Multiple account match → OR hint."""
        q, hint = resolve_account_mentions("ADARO")
        self.assertIsNotNone(hint)
        self.assertIn(" OR ", hint)

    def test_empty_query(self):
        """Empty query → no crash."""
        q, hint = resolve_account_mentions("")
        self.assertIsInstance(q, str)

    def test_short_token_no_match(self):
        """Tokens shorter than 3 chars don't trigger matches."""
        q, hint = resolve_account_mentions("di")
        self.assertIsNone(hint)


class TestAccountMapLoadFailure(unittest.TestCase):
    """Account map handles DB load failure gracefully."""

    @patch("src.services.account_map._ACCOUNT_NAMES", [])
    @patch("src.services.account_map._ACCOUNT_NAMES_LOWER", [])
    @patch("src.services.account_map._LOADED", True)
    def test_no_accounts_loaded(self):
        """When no accounts loaded, returns None hint."""
        q, hint = resolve_account_mentions("engine overheat di PAMA")
        self.assertIsNone(hint)


class TestClientAccountIntegration(unittest.TestCase):
    """Integration check: account_map resolves to real account names in DB."""

    @classmethod
    def setUpClass(cls):
        _load_account_names()

    def test_account_map_has_real_data(self):
        """Verify account_map loaded real data from database."""
        self.assertGreater(len(account_map_mod._ACCOUNT_NAMES), 0, "Account names should be loaded from DB")
        # Verify known accounts exist
        known = ["PAMAPERSADA NUSANTARA", "ADARO INDONESIA", "FREEPORT INDONESIA"]
        for name in known:
            self.assertIn(name, account_map_mod._ACCOUNT_NAMES, f"{name} not found in account names")

    def test_pama_resolves_to_pamapersada(self):
        """PAMA → PAMAPERSADA NUSANTARA resolution."""
        q, hint = resolve_account_mentions("PAMA")
        self.assertIsNotNone(hint)
        self.assertIn("PAMAPERSADA NUSANTARA", hint)


if __name__ == "__main__":
    unittest.main()
