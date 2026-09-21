# SPDX-License-Identifier: GPL-3.0-or-later
"""docs/host-contract.md and test/fixtures/host-contract.json describe the same host: every JSON example is a fixture."""
import json
import re
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
DOC = ROOT / "docs" / "host-contract.md"
FIXTURES = ROOT / "test" / "fixtures" / "host-contract.json"
MESSAGE_TYPES = (
    "hd_api_version", "hd_api_term_entries", "hd_api_kanji_entries", "hd_api_anki_fields", "hd_api_tokenize",
    "hd_api_dictionaries", "hd_api_dictionary_open", "hd_api_dictionary_read", "hd_api_dictionary_close",
)


def canonical(value):
    return json.dumps(value, sort_keys=True, ensure_ascii=False, separators=(",", ":"))


def doc_examples():
    text = DOC.read_text(encoding="utf-8")
    blocks = re.findall(r"```json\n(.*?)```", text, flags=re.DOTALL)
    examples = []
    for block in blocks:
        try:
            examples.append(json.loads(block))
        except ValueError as error:
            raise AssertionError(f"docs/host-contract.md has a JSON example that does not parse: {error}\n{block}") from None
    return text, examples


class HostContractDocTest(unittest.TestCase):
    def setUp(self):
        self.fixtures = json.loads(FIXTURES.read_text(encoding="utf-8"))
        self.text, self.examples = doc_examples()
        self.shown = {canonical(example) for example in self.examples}

    def assert_shown(self, value, what):
        self.assertIn(canonical(value), self.shown, f"docs/host-contract.md does not show the fixture for {what}: {canonical(value)}")

    def test_every_message_type_has_its_request_and_reply_example_in_the_doc(self):
        for message_type in MESSAGE_TYPES:
            fixture = self.fixtures[message_type]
            self.assertIn(f"### `{message_type}`", self.text, f"{message_type} needs its own section")
            self.assertIn(f"| `{message_type}` |", self.text, f"{message_type} belongs in the summary table")
            self.assert_shown(fixture["request"], f"{message_type} request")
            self.assert_shown(fixture["reply"], f"{message_type} reply")
            if "notFound" in fixture:
                self.assert_shown(fixture["notFound"], f"{message_type} notFound reply")

    def test_the_handshake_and_frame_examples_are_the_fixtures(self):
        for name in ("hello", "hostHello", "request", "reply", "error"):
            self.assert_shown(self.fixtures[name], name)

    def test_the_fixtures_only_use_the_documented_message_types(self):
        extra = {name for name in self.fixtures if name.startswith("hd_api_")} - set(MESSAGE_TYPES)
        self.assertEqual(extra, set(), "a fixture type has no documentation")
        self.assertEqual(self.fixtures["request"]["message"]["target"], "hoshidicts-offscreen")
        self.assertIn(self.fixtures["request"]["message"]["type"], MESSAGE_TYPES)

    def test_every_doc_example_is_a_fixture(self):
        known = set()
        for name, fixture in self.fixtures.items():
            if name.startswith("hd_api_"):
                known.update(canonical(fixture[key]) for key in ("request", "reply", "notFound") if key in fixture)
            else:
                known.add(canonical(fixture))
        for example in self.examples:
            self.assertIn(canonical(example), known, f"a JSON example in docs/host-contract.md is not in the fixtures: {canonical(example)}")

    def test_the_doc_names_the_capability_origin_and_chunk_size_the_code_uses(self):
        api = (ROOT / "addon" / "api.py").read_text(encoding="utf-8")
        for constant in ('CAPABILITY = "hoshidicts-api-v1"', 'API_ORIGIN = "relay://yomitan-api"', 'TARGET = "hoshidicts-offscreen"', "CHUNK_LENGTH = 4 * 1024 * 1024"):
            self.assertIn(constant, api)
        for text in ("hoshidicts-api-v1", "relay://yomitan-api", "hoshidicts-offscreen", "4194304"):
            self.assertIn(text, self.text)
        self.assertEqual(self.fixtures["hd_api_dictionary_read"]["request"]["length"], 4 * 1024 * 1024)
        version = json.loads((ROOT / "addon" / "manifest.json").read_text(encoding="utf-8"))["human_version"]
        self.assertEqual(self.fixtures["hello"]["version"], f"hachidori-relay/{version}")


if __name__ == "__main__":
    unittest.main()
