# SPDX-License-Identifier: GPL-3.0-or-later
"""The Yomitan-compatible HTTP API and dictionary downloads of the Hachidori Relay.

Tools that speak to Yomitan's API (backfill-anki-yomitan, scripts, curl) POST
JSON to /termEntries, /kanjiEntries, /ankiFields, /ankiCardFormats, /tokenize,
/yomitanVersion and /serverVersion. Linked apps list the sharing Hachidori's
dictionaries with GET /dictionaries and download one with GET /dictionaries/<id>.

Nothing here reads a dictionary. ApiServer answers HTTP; ApiSession is an
ordinary relay client that lives inside the process (origin relay://yomitan-api)
and asks the sharing Hachidori over hd_api_* request/reply frames, exactly as a
linked browser would. docs/host-contract.md describes those frames. Standard
library only, Python 3.9+.
"""
from __future__ import annotations

import base64
import errno
import json
import socket
import sys
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import quote, unquote, urlsplit

DEFAULT_API_PORT = 19633
CAPABILITY = "hoshidicts-api-v1"
API_ORIGIN = "relay://yomitan-api"
API_ADDRESS = "127.0.0.1"
API_NAME = "Hachidori Relay API"
PROTOCOL_VERSION = 1
TARGET = "hoshidicts-offscreen"
LOOKUP_TIMEOUT_SECONDS = 30.0
CHUNK_TIMEOUT_SECONDS = 60.0
CHUNK_LENGTH = 4 * 1024 * 1024
MAX_BODY_BYTES = 16 * 1024 * 1024
SHUTDOWN_POLL_SECONDS = 0.1

NO_HOST = "No sharing Hachidori is connected to the relay."
NO_CAPABILITY = "The sharing Hachidori does not support the relay API; update Hachidori."
HOST_TIMEOUT = "The sharing Hachidori did not answer in time."


def manifest_version():
    """The add-on version from manifest.json beside this file, or "unknown" outside a package."""
    try:
        return str(json.loads((Path(__file__).resolve().parent / "manifest.json").read_text(encoding="utf-8"))["human_version"])
    except (OSError, ValueError, KeyError):
        return "unknown"


class ApiError(Exception):
    """An HTTP status and the message for its {"error": ...} body."""

    def __init__(self, status, message):
        super().__init__(message)
        self.status = status
        self.message = message


class ApiConnection:
    """What the relay holds as the client's socket: send() delivers to the session, close() ends it."""

    def __init__(self, session):
        self._session = session

    def send(self, text):
        self._session.receive(text)

    def close(self):
        self._session.lost("The sharing Hachidori disconnected.")


def _envelope(message_type, payload):
    """A runtime message for the host; the payload never overrides target or type."""
    message = dict(payload)
    message["target"] = TARGET
    message["type"] = message_type
    return message


class _Waiter:
    __slots__ = ("event", "response", "error", "late")

    def __init__(self, late=None):
        self.event = threading.Event()
        self.response = None
        self.error = None
        # After a timeout the requester is gone; a reply that still arrives goes here instead.
        self.late = late


class ApiSession:
    """One synthetic relay client: hello handshake, then correlated request/reply frames toward the host.

    The relay calls receive() and lost() with its own lock held, so neither may
    call back into the relay on the same thread; sends toward the host happen on
    request threads (or a helper thread for pong) with no session lock held.
    """

    def __init__(self, relay, version, lookup_timeout=LOOKUP_TIMEOUT_SECONDS):
        self._relay = relay
        self._version = version
        self.lookup_timeout = lookup_timeout
        self._lock = threading.Lock()
        self._open_lock = threading.Lock()
        self._handlers = None
        self._pending = {}
        self._next_id = 0
        self._hello = threading.Event()
        self.host_capabilities = ()
        self.host_version = ""
        self.alive = True
        self._failure = None
        self._ready = False

    def _message(self, frame):
        handlers = self._handlers
        if handlers is None:
            raise ApiError(503, self._failure or NO_HOST)
        handlers.message(json.dumps(frame, ensure_ascii=False))

    def ready(self):
        """Connects and shakes hands on first use; raises ApiError 503/501/504 when the host cannot serve."""
        with self._open_lock:
            if not self.alive:
                raise ApiError(503, self._failure or NO_HOST)
            if self._ready:
                return self
            if self._handlers is None:
                handlers = self._relay.connect_client(ApiConnection(self), API_ORIGIN, API_ADDRESS)
                if handlers is None:
                    self.alive = False
                    self._failure = NO_HOST
                    raise ApiError(503, NO_HOST)
                self._handlers = handlers
                self._message({"kind": "hello", "protocol": PROTOCOL_VERSION, "version": f"hachidori-relay/{self._version}",
                               "name": API_NAME, "capabilities": [CAPABILITY]})
            if not self._hello.wait(self.lookup_timeout):
                if not self.alive:
                    raise ApiError(503, self._failure or NO_HOST)
                self.close()
                raise ApiError(504, HOST_TIMEOUT)
            if not self.alive:
                raise ApiError(503, self._failure or NO_HOST)
            if CAPABILITY not in self.host_capabilities:
                # Leave the session in place: the answer will not change until the host does.
                raise ApiError(501, NO_CAPABILITY)
            self._ready = True
            return self

    def request(self, message_type, payload, timeout=None, late=None):
        """The host's response to one hd_api_* request. {error} answers raise 500, or 404 with notFound.

        `late(response)` runs on its own thread if the reply arrives after the
        timeout, so a lease the host granted too late can still be released.
        """
        waiter = _Waiter(late)
        with self._lock:
            if not self.alive:
                raise ApiError(503, self._failure or NO_HOST)
            self._next_id += 1
            request_id = f"api-{self._next_id}"
            self._pending[request_id] = waiter
        try:
            self._message({"kind": "request", "id": request_id, "message": _envelope(message_type, payload)})
            if not waiter.event.wait(self.lookup_timeout if timeout is None else timeout):
                with self._lock:
                    # A reply that races the timeout wins; otherwise the waiter stays only for its late callback.
                    if not waiter.event.is_set():
                        waiter.error = ApiError(504, HOST_TIMEOUT)
                        waiter.event.set()
                        if waiter.late is None:
                            self._pending.pop(request_id, None)
        finally:
            with self._lock:
                if waiter.error is None or waiter.error.status != 504 or waiter.late is None:
                    self._pending.pop(request_id, None)
        if waiter.error is not None:
            raise waiter.error
        response = waiter.response
        if isinstance(response, dict) and "error" in response:
            error = str(response.get("error"))
            raise ApiError(404 if response.get("notFound") is True else 500, error)
        return response

    def notify(self, message_type, payload):
        """Sends a request whose reply nobody waits for; used to release host resources on the way out."""
        with self._lock:
            if not self.alive:
                return
            self._next_id += 1
            request_id = f"api-{self._next_id}"
        try:
            self._message({"kind": "request", "id": request_id, "message": _envelope(message_type, payload)})
        except ApiError:
            pass

    def receive(self, text):
        """One frame from the host (or the relay's own ping), delivered by the relay with its lock held."""
        try:
            frame = json.loads(text)
        except ValueError:
            return
        if not isinstance(frame, dict):
            return
        kind = frame.get("kind")
        if kind == "hello":
            capabilities = frame.get("capabilities")
            self.host_capabilities = tuple(item for item in capabilities if isinstance(item, str)) if isinstance(capabilities, list) else ()
            self.host_version = str(frame.get("version", ""))
            self._hello.set()
        elif kind == "reply":
            late = None
            with self._lock:
                waiter = self._pending.get(frame.get("id"))
                if waiter is not None and not waiter.event.is_set():
                    waiter.response = frame.get("response")
                    waiter.event.set()
                elif waiter is not None and waiter.late is not None:
                    self._pending.pop(frame.get("id"), None)
                    late = waiter.late
            if late is not None:
                # The relay holds its lock here; the callback may send toward the host.
                threading.Thread(target=late, args=(frame.get("response"),), name="hachidori-api-late", daemon=True).start()
        elif kind == "ping":
            # The relay pings with its lock held; answering inline would wait for that same lock.
            threading.Thread(target=self._pong, name="hachidori-api-pong", daemon=True).start()
        elif kind == "bye":
            self.lost(f"The sharing Hachidori ended the session: {frame.get('reason', '')}".rstrip(": "))

    def _pong(self):
        try:
            self._message({"kind": "pong"})
        except ApiError:
            pass

    def lost(self, reason, tell_relay=True):
        """Ends the session; every request still waiting gets 503, replies that already arrived stand.

        The relay calls this with its lock held (host loss, or a host `close`),
        so the relay learns of the closed client on a helper thread. With the
        host gone the relay has already forgotten the client and that is a no-op;
        after a host `close` or `bye` it is what removes the client and tells the host.
        """
        with self._lock:
            if not self.alive:
                return
            self.alive = False
            self._failure = reason
            waiters = list(self._pending.values())
            self._pending.clear()
            for waiter in waiters:
                if not waiter.event.is_set():
                    waiter.error = ApiError(503, reason)
                    waiter.event.set()
        self._hello.set()
        if tell_relay and self._handlers is not None:
            threading.Thread(target=self._handlers.closed, name="hachidori-api-closed", daemon=True).start()

    def close(self):
        """Ends the session from this side, so the host sees the client close."""
        handlers = self._handlers
        self.lost("The relay closed its API session.", tell_relay=False)
        if handlers is not None:
            handlers.closed()


def _strings(value, name):
    """A list of strings from Yomitan's string|array input; anything else is a 400."""
    if isinstance(value, str):
        return [value], True
    if isinstance(value, list) and all(isinstance(item, str) for item in value):
        return value, False
    raise ApiError(400, f'expected "{name}" to be a string or a string array')


def _results(response, count):
    results = response.get("results") if isinstance(response, dict) else None
    if not isinstance(results, list) or len(results) != count:
        raise ApiError(502, "The sharing Hachidori sent a malformed reply.")
    return results


def _content_disposition(file_name):
    safe = "".join(character if 32 <= ord(character) < 127 and character not in '"\\' else "_" for character in file_name) or "dictionary.zip"
    header = f'attachment; filename="{safe}"'
    if safe != file_name:
        header += f"; filename*=UTF-8''{quote(file_name, safe='')}"
    return header


class ApiHandler(BaseHTTPRequestHandler):
    """Routes one HTTP request. Every answer is JSON except a dictionary download."""

    protocol_version = "HTTP/1.1"

    def log_message(self, format, *args):  # noqa: A002 - the base class names it so
        # Anki reports anything on stderr as an error; the API stays quiet.
        return

    @property
    def api(self):
        return self.server.api

    # Paths and the method each accepts; /dictionaries/<id> is matched separately.
    ROUTES = {
        "/serverVersion": ("POST", "server_version"),
        "/yomitanVersion": ("POST", "yomitan_version"),
        "/termEntries": ("POST", "term_entries"),
        "/kanjiEntries": ("POST", "kanji_entries"),
        "/ankiFields": ("POST", "anki_fields"),
        "/ankiCardFormats": ("POST", "anki_card_formats"),
        "/tokenize": ("POST", "tokenize"),
        "/dictionaries": ("GET", "dictionaries"),
    }

    def _cors(self):
        self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header("Access-Control-Allow-Methods", "GET, POST, OPTIONS")
        self.send_header("Access-Control-Allow-Headers", "Content-Type")
        self.send_header("Cache-Control", "no-store")

    def _send_json(self, status, body):
        data = json.dumps(body, ensure_ascii=False).encode("utf-8")
        self.send_response(status)
        self._cors()
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        if self.command != "HEAD":
            self.wfile.write(data)

    def _read_json(self):
        length = self.headers.get("Content-Length")
        try:
            length = int(length) if length else 0
        except ValueError:
            raise ApiError(400, "malformed Content-Length") from None
        if length > MAX_BODY_BYTES:
            raise ApiError(400, "request body too large")
        raw = self.rfile.read(length) if length else b""
        if not raw.strip():
            return {}
        try:
            body = json.loads(raw.decode("utf-8"))
        except (ValueError, UnicodeDecodeError):
            raise ApiError(400, "malformed JSON body") from None
        if not isinstance(body, dict):
            raise ApiError(400, "expected a JSON object body")
        return body

    def _discard_body(self):
        length = self.headers.get("Content-Length")
        try:
            length = int(length) if length else 0
        except ValueError:
            length = 0
        while length > 0:
            chunk = self.rfile.read(min(length, 1 << 16))
            if not chunk:
                break
            length -= len(chunk)

    def do_OPTIONS(self):
        self.send_response(204)
        self._cors()
        self.send_header("Allow", "GET, POST, OPTIONS")
        self.send_header("Content-Length", "0")
        self.end_headers()

    def do_GET(self):
        self._dispatch("GET")

    def do_POST(self):
        self._dispatch("POST")

    # Every other verb gets the same JSON 405 (known path) or 404 (unknown path).
    def do_HEAD(self):
        self._dispatch("HEAD")

    def do_PUT(self):
        self._dispatch("PUT")

    def do_DELETE(self):
        self._dispatch("DELETE")

    def do_PATCH(self):
        self._dispatch("PATCH")

    def _dispatch(self, method):
        path = urlsplit(self.path).path
        try:
            route = self.ROUTES.get(path)
            if route is not None:
                expected, name = route
                if method != expected:
                    self._discard_body()
                    raise ApiError(405, f"{path} accepts {expected} only")
                if method == "POST":
                    body = self._read_json()
                    getattr(self, f"handle_{name}")(body)
                else:
                    getattr(self, f"handle_{name}")()
                return
            if path.startswith("/dictionaries/"):
                dictionary_id = unquote(path[len("/dictionaries/"):])
                if method != "GET":
                    self._discard_body()
                    raise ApiError(405, "/dictionaries/<id> accepts GET only")
                if not dictionary_id or "/" in dictionary_id:
                    raise ApiError(404, "unknown path")
                self.handle_download(dictionary_id)
                return
            self._discard_body()
            raise ApiError(404, "unknown path")
        except ApiError as error:
            self._send_json(error.status, {"error": error.message})
        except (BrokenPipeError, ConnectionResetError):
            self.close_connection = True

    # Routes

    def handle_server_version(self, body):
        self._send_json(200, {"version": self.api.version})

    def handle_yomitan_version(self, body):
        self._send_json(200, self.api.session().request("hd_api_version", {}))

    def handle_term_entries(self, body):
        terms, single = _strings(body.get("term"), "term")
        results = _results(self.api.session().request("hd_api_term_entries", {"terms": terms}), len(terms))
        self._send_json(200, results[0] if single else results)

    def handle_kanji_entries(self, body):
        characters, single = _strings(body.get("character"), "character")
        results = _results(self.api.session().request("hd_api_kanji_entries", {"characters": characters}), len(characters))
        if single:
            # Yomitan answers a string input with the bare entry list.
            entries = results[0].get("dictionaryEntries") if isinstance(results[0], dict) else None
            self._send_json(200, entries if isinstance(entries, list) else [])
        else:
            self._send_json(200, results)

    def handle_anki_fields(self, body):
        text = body.get("text")
        kind = body.get("type", "term")
        markers = body.get("markers")
        max_entries = body.get("maxEntries", 0)
        include_media = body.get("includeMedia", False)
        if not isinstance(text, str):
            raise ApiError(400, 'expected "text" to be a string')
        if kind not in ("term", "kanji"):
            raise ApiError(400, 'expected "type" to be "term" or "kanji"')
        if not isinstance(markers, list) or not all(isinstance(marker, str) for marker in markers):
            raise ApiError(400, 'expected "markers" to be a string array')
        if isinstance(max_entries, bool) or not isinstance(max_entries, int):
            raise ApiError(400, 'expected "maxEntries" to be an integer')
        if not isinstance(include_media, bool):
            raise ApiError(400, 'expected "includeMedia" to be a boolean')
        # Yomitan's "type" (term or kanji) travels as entryType: "type" names the message itself.
        response = self.api.session().request("hd_api_anki_fields", {
            "text": text, "entryType": kind, "markers": markers, "maxEntries": max_entries, "includeMedia": include_media,
        })
        if not isinstance(response, dict) or not isinstance(response.get("fields"), list):
            raise ApiError(502, "The sharing Hachidori sent a malformed reply.")
        self._send_json(200, response)

    def handle_anki_card_formats(self, body):
        # Yomitan reads a number as a profile index and anything else as the active profile.
        profile_index = body.get("profileIndex")
        numeric = isinstance(profile_index, (int, float)) and not isinstance(profile_index, bool)
        response = self.api.session().request("hd_api_anki_card_formats", {"profileIndex": profile_index} if numeric else {})
        formats = response.get("cardFormats") if isinstance(response, dict) else None
        if not isinstance(formats, list):
            raise ApiError(502, "The sharing Hachidori sent a malformed reply.")
        # Yomitan answers the bare array.
        self._send_json(200, formats)

    def handle_tokenize(self, body):
        texts, _ = _strings(body.get("text"), "text")
        scan_length = body.get("scanLength")
        parser = body.get("parser", "scanning-parser")
        if isinstance(scan_length, bool) or not isinstance(scan_length, (int, float)):
            raise ApiError(400, 'expected "scanLength" to be a number')
        if not isinstance(parser, str):
            raise ApiError(400, 'expected "parser" to be a string')
        response = self.api.session().request("hd_api_tokenize", {"texts": texts, "scanLength": scan_length, "parser": parser})
        results = response.get("results") if isinstance(response, dict) else None
        if not isinstance(results, list):
            raise ApiError(502, "The sharing Hachidori sent a malformed reply.")
        self._send_json(200, results)

    def handle_dictionaries(self):
        response = self.api.session().request("hd_api_dictionaries", {})
        if not isinstance(response, dict) or not isinstance(response.get("dictionaries"), list):
            raise ApiError(502, "The sharing Hachidori sent a malformed reply.")
        self._send_json(200, response)

    def handle_download(self, dictionary_id):
        session = self.api.session()

        def close_late(response):
            # The host granted the lease after we stopped waiting: release it anyway.
            if isinstance(response, dict) and isinstance(response.get("token"), (str, int)):
                session.notify("hd_api_dictionary_close", {"token": response["token"]})

        opened = session.request("hd_api_dictionary_open", {"id": dictionary_id}, late=close_late)
        if not isinstance(opened, dict) or not isinstance(opened.get("token"), (str, int)):
            raise ApiError(502, "The sharing Hachidori sent a malformed reply.")
        token = opened["token"]
        size = opened.get("size")
        size = size if isinstance(size, int) and not isinstance(size, bool) and size >= 0 else None
        file_name = opened.get("fileName")
        file_name = file_name if isinstance(file_name, str) and file_name else f"{dictionary_id}.zip"
        try:
            self.send_response(200)
            self._cors()
            self.send_header("Content-Type", "application/zip")
            self.send_header("Content-Disposition", _content_disposition(file_name))
            if size is not None:
                self.send_header("Content-Length", str(size))
            else:
                # Without a length the end of the body is the end of the connection.
                self.send_header("Connection", "close")
                self.close_connection = True
            self.end_headers()
            self.wfile.flush()
            offset = 0
            while size is None or offset < size:
                chunk = session.request("hd_api_dictionary_read", {"token": token, "offset": offset, "length": CHUNK_LENGTH}, self.api.chunk_timeout)
                if not isinstance(chunk, dict):
                    raise ApiError(502, "malformed chunk")
                try:
                    data = base64.b64decode(chunk.get("data") or "", validate=True)
                except ValueError:
                    raise ApiError(502, "malformed chunk") from None
                if size is not None and offset + len(data) > size:
                    data = data[:size - offset]
                if data:
                    self.wfile.write(data)
                    self.wfile.flush()
                    offset += len(data)
                if chunk.get("eof") is True:
                    break
                if not data:
                    raise ApiError(502, "empty chunk before eof")
            if size is not None and offset < size:
                # The host stopped short; the client sees a truncated body, never a wrong one.
                self.close_connection = True
        except (ApiError, OSError):
            # Headers are already out; ending the connection is the only honest signal left.
            self.close_connection = True
        finally:
            session.notify("hd_api_dictionary_close", {"token": token})
        if self.close_connection:
            try:
                self.wfile.flush()
                self.connection.shutdown(socket.SHUT_WR)
            except OSError:
                pass


class _Server(ThreadingHTTPServer):
    daemon_threads = True
    # Frees the port straight after a restart; Windows would instead let two listeners share it.
    allow_reuse_address = sys.platform != "win32"
    request_queue_size = 32

    def __init__(self, address, api):
        self.api = api
        super().__init__(address, ApiHandler)


class ApiServer:
    """The HTTP listener, on this computer only or on every interface while the host asks for the network."""

    def __init__(self, relay, port, version=None, lookup_timeout=LOOKUP_TIMEOUT_SECONDS, chunk_timeout=CHUNK_TIMEOUT_SECONDS):
        self._relay = relay
        self.port = port
        self.version = version if version is not None else manifest_version()
        self.lookup_timeout = lookup_timeout
        self.chunk_timeout = chunk_timeout
        self.network = False
        self._server = None
        self._thread = None
        self._session = None
        self._session_lock = threading.Lock()
        self._bind_lock = threading.Lock()

    def open(self, network):
        """Binds afresh; the old listener goes first because Linux refuses a wildcard bind beside a loopback one."""
        with self._bind_lock:
            self._stop()
            address = "0.0.0.0" if network else "127.0.0.1"
            server = None
            for attempt in range(40):
                try:
                    server = _Server((address, self.port), self)
                    break
                except OSError as error:
                    if error.errno != errno.EADDRINUSE or attempt == 39:
                        raise
                    time.sleep(0.05)
            self._server = server
            self.port = server.server_address[1]
            self.network = network
            self._thread = threading.Thread(target=server.serve_forever, args=(SHUTDOWN_POLL_SECONDS,), name="hachidori-api-http", daemon=True)
            self._thread.start()

    def _stop(self):
        server, self._server = self._server, None
        if server is not None:
            server.shutdown()
            server.server_close()

    def set_network(self, enabled):
        """Follows the relay's network switch. Returns an error text when every interface could not be bound."""
        if enabled == self.network:
            return None
        try:
            self.open(enabled)
        except OSError as error:
            self.open(False)
            return f"Yomitan API stays on this computer: {error}"
        return None

    def close(self):
        with self._bind_lock:
            self._stop()
        with self._session_lock:
            session, self._session = self._session, None
        if session is not None:
            session.close()

    def session(self):
        """A ready session toward the current host; a new one after the previous host went away."""
        with self._session_lock:
            session = self._session
            if session is None or not session.alive:
                session = ApiSession(self._relay, self.version, self.lookup_timeout)
                self._session = session
        return session.ready()
