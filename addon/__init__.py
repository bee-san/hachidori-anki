# SPDX-License-Identifier: GPL-3.0-or-later
"""Hachidori Relay, the Anki add-on.

Keeps server.py listening for as long as Anki runs, so the Hachidori installs
on this computer, and on the person's other computers when the sharing
Hachidori asks for it, can share one library. Beside the relay it serves the
Yomitan-compatible HTTP API and dictionary downloads of api.py. The two ports
are the add-on's only settings; see config.md.
"""
import functools
import threading
import time

from aqt import mw
from aqt.utils import showWarning

from .server import serve

RETRY_SECONDS = 10


def warn(text):
    # Nothing goes to stderr, which Anki shows as an error.
    mw.taskman.run_on_main(functools.partial(showWarning, text, title="Hachidori Relay"))


def run(port, api_port):
    warned = set()

    def warn_once(key, text):
        if key not in warned:
            warned.add(key)
            warn(text)

    def api_announced(bound_port, error):
        # The relay keeps running without the API; say so once, naming the port.
        if error is not None:
            warn_once("api", (
                f"Hachidori Relay could not use port {api_port} for the Yomitan API: {error}.\n\n"
                "Sharing through Anki still works. Change yomitan_api_port under Tools → Add-ons → "
                "Hachidori Relay → Config (or set it to null to turn the API off), then restart Anki."
            ))

    while True:
        try:
            serve(port, api_port=api_port, announce_api=api_announced)
        except OSError as error:
            # Another program holds the relay port: say so once, then keep trying.
            warn_once("relay", (
                f"Hachidori Relay could not use port {port}: {error}.\n\n"
                "Change the port under Tools → Add-ons → Hachidori Relay → Config, and under "
                "Settings → Sharing → Advanced in Hachidori, then restart Anki."
            ))
            time.sleep(RETRY_SECONDS)


def configured_ports():
    config = mw.addonManager.getConfig(__name__)
    api_port = config.get("yomitan_api_port", 19633)
    return int(config["port"]), (int(api_port) if api_port else None)


threading.Thread(target=run, args=configured_ports(), name="hachidori-relay", daemon=True).start()
