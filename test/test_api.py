# SPDX-License-Identifier: GPL-3.0-or-later
"""ApiSession against a stub relay: correlation, ordering, timeouts and host loss, without sockets."""
import importlib.util
import json
import threading
import unittest
from collections import namedtuple
from pathlib import Path

Handlers = namedtuple("Handlers", ["message", "closed"])

ROOT = Path(__file__).resolve().parents[1]


def load_api():
    # addon/__init__.py imports Anki, so the module is loaded from its file instead of the package.
    spec = importlib.util.spec_from_file_location("hachidori_relay_api", ROOT / "addon" / "api.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


api = load_api()


class StubRelay:
    """Records what the session sends and lets a test play the host."""

    def __init__(self, has_host=True):
        self.has_host = has_host
        self.connection = None
        self.origin = None
        self.address = None
        self.sent = []
        self.closed = 0
        self.arrived = threading.Condition()

    def connect_client(self, connection, origin, address):
        if not self.has_host:
            return None
        self.connection, self.origin, self.address = connection, origin, address

        def message(text):
            with self.arrived:
                self.sent.append(json.loads(text))
                self.arrived.notify_all()

        def closed():
            with self.arrived:
                self.closed += 1
                self.arrived.notify_all()

        return Handlers(message, closed)

    def closed_soon(self, count, timeout=5):
        with self.arrived:
            return self.arrived.wait_for(lambda: self.closed >= count, timeout)

    def frames(self, count, timeout=5):
        with self.arrived:
            if not self.arrived.wait_for(lambda: len(self.sent) >= count, timeout):
                raise AssertionError(f"expected {count} frames, saw {self.sent}")
            return list(self.sent)

    def requests(self):
        return [frame for frame in self.sent if frame["kind"] == "request"]

    def host_says(self, frame):
        self.connection.send(json.dumps(frame))

    def hello(self, capabilities=(api.CAPABILITY,), version="9.9"):
        self.host_says({"kind": "hello", "protocol": 1, "version": version, "name": "Stub", "dictionaryCount": 0, "capabilities": list(capabilities), "snapshot": {}})

    def reply(self, request_id, response):
        self.host_says({"kind": "reply", "id": request_id, "response": response})


def in_thread(function):
    result = {}

    def run():
        try:
            result["value"] = function()
        except BaseException as error:  # noqa: BLE001 - re-raised by join()
            result["error"] = error

    thread = threading.Thread(target=run, daemon=True)
    thread.start()

    def join(timeout=5):
        thread.join(timeout)
        if thread.is_alive():
            raise AssertionError("the request did not finish")
        if "error" in result:
            raise result["error"]
        return result["value"]

    return join


class SessionTest(unittest.TestCase):
    def ready(self, relay, **hello):
        session = api.ApiSession(relay, "0.0.4", lookup_timeout=0.3)
        started = in_thread(session.ready)
        relay.frames(1)
        relay.hello(**hello)
        return started()

    def test_no_host_is_503_and_nothing_is_sent(self):
        relay = StubRelay(has_host=False)
        with self.assertRaises(api.ApiError) as raised:
            api.ApiSession(relay, "0.0.4").ready()
        self.assertEqual((raised.exception.status, raised.exception.message), (503, api.NO_HOST))
        self.assertEqual(relay.sent, [])

    def test_hello_handshake_identifies_the_relay(self):
        relay = StubRelay()
        session = self.ready(relay)
        self.assertEqual((relay.origin, relay.address), (api.API_ORIGIN, api.API_ADDRESS))
        self.assertEqual(relay.sent[0], {"kind": "hello", "protocol": 1, "version": "hachidori-relay/0.0.4", "name": api.API_NAME, "capabilities": [api.CAPABILITY]})
        self.assertEqual(session.host_version, "9.9")
        self.assertIs(session.ready(), session, "ready() is idempotent")
        self.assertEqual(len(relay.sent), 1)

    def test_host_without_capability_is_501(self):
        relay = StubRelay()
        with self.assertRaises(api.ApiError) as raised:
            self.ready(relay, capabilities=("other",))
        self.assertEqual(raised.exception.status, 501)

    def test_hello_that_never_comes_is_504_and_closes_the_session(self):
        relay = StubRelay()
        session = api.ApiSession(relay, "0.0.4", lookup_timeout=0.3)
        with self.assertRaises(api.ApiError) as raised:
            session.ready()
        self.assertEqual(raised.exception.status, 504)
        self.assertFalse(session.alive)
        self.assertEqual(relay.closed, 1, "the relay learns the client is gone")

    def test_replies_are_correlated_by_id_even_out_of_order(self):
        relay = StubRelay()
        session = self.ready(relay)
        first = in_thread(lambda: session.request("hd_api_version", {}))
        second = in_thread(lambda: session.request("hd_api_term_entries", {"terms": ["a"]}))
        relay.frames(3)
        requests = {frame["message"]["type"]: frame for frame in relay.requests()}
        self.assertEqual(set(requests), {"hd_api_version", "hd_api_term_entries"})
        self.assertNotEqual(requests["hd_api_version"]["id"], requests["hd_api_term_entries"]["id"])
        self.assertEqual(requests["hd_api_term_entries"]["message"], {"target": api.TARGET, "type": "hd_api_term_entries", "terms": ["a"]})
        relay.reply(requests["hd_api_term_entries"]["id"], {"results": []})
        relay.reply("api-unknown", {"ignored": True})
        relay.reply(requests["hd_api_version"]["id"], {"version": "9.9"})
        self.assertEqual(second(), {"results": []})
        self.assertEqual(first(), {"version": "9.9"})

    def test_payload_cannot_override_target_or_type(self):
        relay = StubRelay()
        session = self.ready(relay)
        pending = in_thread(lambda: session.request("hd_api_anki_fields", {"type": "kanji", "target": "elsewhere", "text": "x"}))
        relay.frames(2)
        message = relay.requests()[0]["message"]
        self.assertEqual(message, {"target": api.TARGET, "type": "hd_api_anki_fields", "text": "x"})
        relay.reply(relay.requests()[0]["id"], {"fields": []})
        pending()

    def test_error_replies_map_to_500_or_404(self):
        relay = StubRelay()
        session = self.ready(relay)
        for response, status in (({"error": "boom"}, 500), ({"error": "gone", "notFound": True}, 404), ({"error": "x", "notFound": "yes"}, 500)):
            count = len(relay.sent) + 1
            pending = in_thread(lambda: session.request("hd_api_dictionary_open", {"id": "d"}))
            relay.frames(count)
            relay.reply(relay.requests()[-1]["id"], response)
            with self.assertRaises(api.ApiError) as raised:
                pending()
            self.assertEqual((raised.exception.status, raised.exception.message), (status, response["error"]))
        self.assertTrue(session.alive, "an error reply does not end the session")

    def test_timeout_is_504_and_a_late_reply_is_ignored(self):
        relay = StubRelay()
        session = self.ready(relay)
        with self.assertRaises(api.ApiError) as raised:
            session.request("hd_api_version", {}, timeout=0.05)
        self.assertEqual(raised.exception.status, 504)
        relay.reply(relay.requests()[0]["id"], {"version": "late"})
        self.assertTrue(session.alive)
        pending = in_thread(lambda: session.request("hd_api_version", {}))
        relay.frames(3)
        relay.reply(relay.requests()[1]["id"], {"version": "fresh"})
        self.assertEqual(pending(), {"version": "fresh"})

    def test_host_loss_fails_every_pending_request_with_503(self):
        relay = StubRelay()
        session = self.ready(relay)
        waits = [in_thread(lambda: session.request("hd_api_version", {})) for _ in range(3)]
        relay.frames(4)
        relay.connection.close()
        for wait in waits:
            with self.assertRaises(api.ApiError) as raised:
                wait()
            self.assertEqual(raised.exception.status, 503)
            self.assertIn("disconnected", raised.exception.message)
        self.assertFalse(session.alive)
        with self.assertRaises(api.ApiError) as raised:
            session.request("hd_api_version", {})
        self.assertEqual(raised.exception.status, 503)
        with self.assertRaises(api.ApiError):
            session.ready()
        self.assertTrue(relay.closed_soon(1), "the relay is told off its own thread; after host loss that is a no-op there")

    def test_a_reply_that_arrives_just_before_host_loss_still_stands(self):
        relay = StubRelay()
        session = self.ready(relay)
        pending = in_thread(lambda: session.request("hd_api_version", {}))
        relay.frames(2)
        relay.reply(relay.requests()[0]["id"], {"version": "arrived"})
        relay.connection.close()
        self.assertEqual(pending(), {"version": "arrived"})
        self.assertFalse(session.alive)

    def test_a_host_close_of_the_client_removes_it_from_the_relay(self):
        relay = StubRelay()
        session = self.ready(relay)
        relay.connection.close()  # what SharingRelay does for a host {kind: "close", clientId}
        self.assertTrue(relay.closed_soon(1), "handlers.closed() runs so the relay forgets the client and tells the host")
        self.assertFalse(session.alive)

    def test_a_late_reply_after_a_timeout_reaches_the_late_callback(self):
        relay = StubRelay()
        session = self.ready(relay)
        late = []
        arrived = threading.Event()

        def on_late(response):
            late.append(response)
            arrived.set()

        with self.assertRaises(api.ApiError) as raised:
            session.request("hd_api_dictionary_open", {"id": "d"}, timeout=0.05, late=on_late)
        self.assertEqual(raised.exception.status, 504)
        relay.reply(relay.requests()[0]["id"], {"token": "dl-9", "size": 1})
        self.assertTrue(arrived.wait(2), "the late callback runs")
        self.assertEqual(late, [{"token": "dl-9", "size": 1}])
        self.assertEqual(session._pending, {}, "the waiter is gone once its late reply came")
        # Without a callback the waiter is dropped at the timeout and the late reply is ignored.
        with self.assertRaises(api.ApiError):
            session.request("hd_api_version", {}, timeout=0.05)
        self.assertEqual(session._pending, {})

    def test_bye_from_the_host_ends_the_session_with_its_reason(self):
        relay = StubRelay()
        session = self.ready(relay)
        pending = in_thread(lambda: session.request("hd_api_version", {}))
        relay.frames(2)
        relay.host_says({"kind": "bye", "reason": "malformed sharing frame"})
        with self.assertRaises(api.ApiError) as raised:
            pending()
        self.assertEqual(raised.exception.status, 503)
        self.assertIn("malformed sharing frame", raised.exception.message)
        self.assertTrue(relay.closed_soon(1), "after bye the relay forgets the client")

    def test_ping_is_answered_with_pong_and_other_frames_are_ignored(self):
        relay = StubRelay()
        session = self.ready(relay)
        relay.host_says({"kind": "storage", "changes": {}})
        relay.host_says("not an object")
        relay.connection.send("{not json")
        relay.host_says({"kind": "ping"})
        frames = relay.frames(2)
        self.assertEqual(frames[1], {"kind": "pong"})
        self.assertTrue(session.alive)

    def test_notify_sends_without_waiting_and_is_silent_after_loss(self):
        relay = StubRelay()
        session = self.ready(relay)
        session.notify("hd_api_dictionary_close", {"token": "t"})
        self.assertEqual(relay.requests()[0]["message"], {"target": api.TARGET, "type": "hd_api_dictionary_close", "token": "t"})
        relay.connection.close()
        session.notify("hd_api_dictionary_close", {"token": "t"})
        self.assertEqual(len(relay.requests()), 1)


class ServerSessionTest(unittest.TestCase):
    def test_server_replaces_a_dead_session(self):
        relay = StubRelay()
        server = api.ApiServer(relay, 0, version="0.0.4", lookup_timeout=0.3)
        started = in_thread(server.session)
        relay.frames(1)
        relay.hello()
        first = started()
        self.assertIs(server.session(), first)
        relay.connection.close()
        relay.sent.clear()
        started = in_thread(server.session)
        relay.frames(1)
        relay.hello()
        second = started()
        self.assertIsNot(second, first)
        self.assertTrue(second.alive)

    def test_version_comes_from_the_manifest_beside_the_module(self):
        manifest = json.loads((ROOT / "addon" / "manifest.json").read_text(encoding="utf-8"))
        self.assertEqual(api.manifest_version(), manifest["human_version"])
        self.assertEqual(api.ApiServer(StubRelay(), 0).version, manifest["human_version"])


if __name__ == "__main__":
    unittest.main()
