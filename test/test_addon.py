# SPDX-License-Identifier: GPL-3.0-or-later
"""The Anki entry point (addon/__init__.py) with aqt and the server stubbed: ports and warnings."""
import importlib
import sys
import threading
import types
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
PACKAGE = "hachidori_relay_under_test"


def install_package(config, serve):
    """Imports addon/ as a package named PACKAGE, with aqt and .server replaced."""
    warnings = []
    main_calls = []

    aqt = types.ModuleType("aqt")
    aqt.mw = types.SimpleNamespace(
        addonManager=types.SimpleNamespace(getConfig=lambda name: dict(config)),
        taskman=types.SimpleNamespace(run_on_main=lambda call: main_calls.append(call) or call()),
    )
    aqt_utils = types.ModuleType("aqt.utils")
    aqt_utils.showWarning = lambda text, title=None: warnings.append((title, text))
    server = types.ModuleType(f"{PACKAGE}.server")
    server.serve = serve
    saved = {name: sys.modules.get(name) for name in ("aqt", "aqt.utils", f"{PACKAGE}.server", PACKAGE)}
    sys.modules["aqt"], sys.modules["aqt.utils"], sys.modules[f"{PACKAGE}.server"] = aqt, aqt_utils, server
    try:
        spec = importlib.util.spec_from_file_location(PACKAGE, ROOT / "addon" / "__init__.py", submodule_search_locations=[str(ROOT / "addon")])
        module = importlib.util.module_from_spec(spec)
        sys.modules[PACKAGE] = module
        spec.loader.exec_module(module)
    finally:
        for name, value in saved.items():
            if value is None:
                sys.modules.pop(name, None)
            else:
                sys.modules[name] = value
    return module, warnings, main_calls


class EntryPointTest(unittest.TestCase):
    def run_addon(self, config, on_serve):
        calls = []
        started = threading.Event()

        def serve(port, ping_seconds=None, announce=None, api_port=None, announce_api=None):
            calls.append({"port": port, "api_port": api_port})
            on_serve(port, api_port, announce_api)
            started.set()
            threading.Event().wait()  # the real serve() never returns

        module, warnings, main_calls = install_package(config, serve)
        self.assertTrue(started.wait(5), "serve() runs on the add-on's thread")
        return module, calls, warnings, main_calls

    def test_both_ports_reach_serve_and_no_warning_when_they_bind(self):
        _, calls, warnings, _ = self.run_addon({"port": 8771, "yomitan_api_port": 19633}, lambda port, api_port, announce_api: announce_api(api_port, None))
        self.assertEqual(calls, [{"port": 8771, "api_port": 19633}])
        self.assertEqual(warnings, [])

    def test_a_null_api_port_disables_the_api(self):
        _, calls, _, _ = self.run_addon({"port": 8771, "yomitan_api_port": None}, lambda *_: None)
        self.assertEqual(calls, [{"port": 8771, "api_port": None}])

    def test_a_config_from_an_older_release_gets_the_default_api_port(self):
        _, calls, _, _ = self.run_addon({"port": 9000}, lambda *_: None)
        self.assertEqual(calls, [{"port": 9000, "api_port": 19633}])

    def test_an_occupied_api_port_warns_once_on_the_main_thread_and_names_the_port(self):
        def on_serve(port, api_port, announce_api):
            announce_api(None, OSError(98, "Address already in use"))
            announce_api(None, OSError(98, "Address already in use"))

        _, _, warnings, main_calls = self.run_addon({"port": 8771, "yomitan_api_port": 19633}, on_serve)
        self.assertEqual(len(warnings), 1)
        self.assertEqual(len(main_calls), 1)
        title, text = warnings[0]
        self.assertEqual(title, "Hachidori Relay")
        self.assertIn("19633", text)
        self.assertIn("Sharing through Anki still works", text)
        self.assertIn("yomitan_api_port", text)


if __name__ == "__main__":
    unittest.main()
