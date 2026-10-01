<!-- SPDX-License-Identifier: GPL-3.0-or-later -->

# Hachidori Relay for Anki

Share one [Hachidori](https://github.com/bee-san/hachidori) library between
browser installs. Anki runs the small WebSocket relay while it is open; the
browser with your dictionaries hosts, and your other browsers link to it.

## Install

1. Download [hachidori-relay.ankiaddon v0.0.5](https://github.com/bee-san/hachidori-anki/releases/download/v0.0.5/hachidori-relay.ankiaddon).
2. Double-click it, or in Anki choose **Tools → Add-ons → Install from file…**.
3. Restart Anki and keep it open. Hachidori's **Settings → Sharing** will show
   **Sharing through Anki** once the host has dictionaries.
4. In your other browser, choose the shared Hachidori offered by startup or
   **Settings → Sharing**.

For your other computers, enable **Also with my other computers** on the host
and enter one of the listed addresses in the other browser. This exposes the
relay to that network until the host turns sharing off or disconnects. Use it
on a trusted home network or Tailscale; there is no password or token.

Native Hoshidicts ecosystem apps link with the shared WebSocket Origin
`hoshi://hoshidicts`. This exact scheme/host origin is accepted only on `/link`;
`/host` remains limited to Hachidori Chrome extensions on the same computer.
Ordinary web-page origins remain refused.

See Hachidori's [sharing guide](https://github.com/bee-san/hachidori/blob/main/docs/sharing.md)
for the complete setup. The default port is 8771; change it in both Anki's
add-on Config and Hachidori's Sharing → Advanced, then restart Anki.

## Yomitan API

While Anki is open, the add-on also serves the HTTP API of
[Kuuuube/yomitan-api](https://github.com/Kuuuube/yomitan-api) on Yomitan's
default port 19633, answered by the sharing Hachidori's dictionaries. Tools
written for that API, such as
[backfill-anki-yomitan](https://github.com/Manhhao/backfill-anki-yomitan),
work unchanged when pointed at `http://127.0.0.1:19633`:

| Method and path | Answers |
|-----------------|---------|
| `POST /serverVersion` | `{"version": "<add-on version>"}`, with or without a sharing Hachidori |
| `POST /yomitanVersion` | `{"version": "<host Hachidori version>"}` |
| `POST /termEntries` | `{"term": string \| string[]}` → term entries, one `{index, ...}` per input |
| `POST /kanjiEntries` | `{"character": string \| string[]}` → kanji entries |
| `POST /ankiFields` | `{"text", "type", "markers", "maxEntries", "includeMedia"}` → `{"fields", "dictionaryMedia", "audioMedia"}` |
| `POST /ankiCardFormats` | `{"profileIndex"}` (optional) → the host's Anki card formats, `[{"name", "icon", "deck", "model", "fields", "type"}]` |
| `POST /tokenize` | `{"text": string \| string[], "scanLength", "parser"}` → parsed segments |

Bodies and answers follow Kuuuube's `docs/api_paths`; the few divergences are
listed at the end of [docs/host-contract.md](docs/host-contract.md). Every
answer allows any browser origin (`Access-Control-Allow-Origin: *`), and errors
are JSON: `400` for a malformed body, `404` for an unknown path, `405` for the
wrong method, `500` when the host reports an error (Yomitan parity, tools read
it as "no result"), `501` when the sharing Hachidori is too old to serve the
API, `503` when no Hachidori is sharing through Anki, and `504` when the host
does not answer in time.

The API runs beside the relay and needs it: if the relay port is taken, Anki
shows the existing warning and neither listener starts until the port is free;
if only the API port is taken, the relay keeps running and Anki warns once,
naming the port.

The API needs a Hachidori that shares through Anki and advertises the
`hoshidicts-api-v1` capability; until the extension does, every endpoint
except `/serverVersion` answers `501` with a message to update Hachidori. It
has the same trust model as the relay: no password or token, this computer only
until the host enables **Also with my other computers**, which opens both ports
to that network together. Change or disable the port with `yomitan_api_port` in
the add-on Config; see [addon/config.md](addon/config.md).

## Copying dictionaries

Linked apps can copy the sharing Hachidori's dictionaries through the same
port. `GET /dictionaries` lists them:

```sh
curl http://127.0.0.1:19633/dictionaries
```

`GET /dictionaries/<id>` downloads one as the archive Hachidori's own Import
accepts, streamed straight from the host with `Content-Length` when the host
knows the size and a `Content-Disposition` file name:

```sh
curl -OJ http://127.0.0.1:19633/dictionaries/<id>
```

An unknown id is `404`; the host-state errors are the same as above. The
relay pulls the file in 4 MiB pieces and never holds a whole dictionary in
memory, so several downloads can run at once.

## Documentation

- [docs/host-contract.md](docs/host-contract.md): what a sharing Hachidori
  must implement for the Yomitan API and dictionary downloads: the
  `hoshidicts-api-v1` capability, the `relay://yomitan-api` client, every
  `hd_api_*` request and reply with examples, error semantics, chunked reads,
  and the divergences from Kuuuube's yomitan-api. It is written for the
  extension side and needs no reading of the relay code; the fake host in
  `test/fake-host.mjs` implements it and `test/test_docs.py` keeps the two
  identical.
- [addon/config.md](addon/config.md): the add-on's settings (`port`,
  `yomitan_api_port`), as shown in Anki's add-on Config dialog.
- Hachidori's [sharing guide](https://github.com/bee-san/hachidori/blob/main/docs/sharing.md)
  covers the browser side of sharing.

### Versions and updates

The add-on has its own version, independent of the extension. Version 0.0.1
contains the relay extracted from Hachidori commit
[`265c278`](https://github.com/bee-san/hachidori/tree/265c278c83483edafa50d931e1822a0a022138d2).
The extension pins a compatible release URL, including when vendored by
GameSentenceMiner. Use the version offered by that extension's download button.

Version 0.0.2 fixes idle listener timeouts on Python 3.9, where
`socket.timeout` is not yet an alias of the built-in `TimeoutError`.

Version 0.0.3 carries Hachidori's slow-client isolation fix from
[`63d380f`](https://github.com/bee-san/hachidori/commit/63d380feac53c3c28b9f60f341eccd73a399406e).
Healthy sockets keep direct sends; a backed-up socket drains its own ordered
queue without holding the relay lock. Other browsers, pings and disconnects
remain responsive while a client stops reading.

Version 0.0.4 adds the Yomitan-compatible HTTP API and dictionary downloads
described above, on a second port that follows the relay's network switch, with
a new `yomitan_api_port` setting. The host side of that API is specified in
[docs/host-contract.md](docs/host-contract.md) and needs a Hachidori release
that advertises `hoshidicts-api-v1`; the relay behaviour of 0.0.3 is unchanged.

Version 0.0.5 adds Yomitan's `POST /ankiCardFormats`
([yomitan#2409](https://github.com/yomidevs/yomitan/pull/2409)): the sharing
Hachidori's Anki Templates as Yomitan card formats, with their deck, note type
and field markers but never the AnkiConnect address or key. Tools such as GSM
Companion and Yomine read them instead of asking the user to retype every field.
A Hachidori without the new request answers it with `500`; the other endpoints
are unchanged.

These GitHub installs are updated by installing a new `.ankiaddon` file and
restarting Anki. The stable `hachidori-relay` package ID updates the existing
add-on. Anki keeps user configuration in `meta.json`, which is excluded from
the archive. GitHub's generated source ZIP is for development; install the
`.ankiaddon` release asset.

Each release includes `hachidori-relay.ankiaddon.sha256`. With both downloaded
into the same directory, verify it with:

```sh
sha256sum --check hachidori-relay.ankiaddon.sha256
```

## Develop and test

The add-on and packager use Python 3.9+ and its standard library. The socket
tests also need Node 22+. Anki is needed only for the optional desktop check.

```sh
python3 -m unittest discover -s test -p 'test_*.py'
python3 scripts/package-addon.py
python3 -m zipfile -e dist/hachidori-relay.ankiaddon dist/unpacked
HACHIDORI_RELAY_SERVER=dist/unpacked/server.py node --test test/sharing-relay.test.mjs test/api.test.mjs
```

The package tests verify root-level files, package identity, version, commit
timestamp, checksum, reproducibility, and exclusion of caches and user config.
`test_api.py` drives the API's relay session against a stub relay (reply
correlation, out-of-order replies, timeouts, host loss); `test_addon.py` runs
the Anki entry point with `aqt` stubbed; `test_docs.py` keeps
`docs/host-contract.md` and the fake host's fixtures identical. The socket
tests run the packaged relay and cover origins, host ownership, idle
connections, bidirectional text, broadcast, pings, disconnections, stalled
peers with ordered large UTF-8 replies, and enabling and disabling network
sharing. `api.test.mjs` starts a fake sharing Hachidori (`test/fake-host.mjs`)
on `/host` and exercises every HTTP endpoint, streamed downloads, client
aborts, the network switch and an occupied API port. Network cases require a
non-loopback address; the suites report when the machine has none.

With a Python interpreter that can import the installed `anki` and `aqt`:

```sh
python3 test/anki-relay-desktop.py dist/hachidori-relay.ankiaddon
```

This launches Anki with a temporary profile, extracts the archive there, and
checks the relay. To run the relay by itself during development:

```sh
python3 addon/server.py --port 8771 --api-port 19633
```

`addon/` contains the Anki entry point, relay server, HTTP API, and defaults. The packager
puts these and the license directly at the ZIP root, as required by
[Anki's distribution format](https://addon-docs.ankiweb.net/sharing.html).
It uses stored ZIP entries, fixed dates/permissions/order, and the source
commit's timestamp for `manifest.json`'s `mod`, so builds of the same commit
produce the same bytes. `human_version` comes from `addon/manifest.json`.

## Release

1. Update `human_version` in `addon/manifest.json` and the installation link
   above in a dedicated branch; open and merge a pull request after Checks passes.
2. Tag that merged commit `v<version>` and push the tag.
3. The Release workflow runs Checks against the packaged relay, verifies the
   tag matches the manifest version, and publishes the add-on and its checksum.
4. Verify the asset download before updating Hachidori's pinned add-on version.
   Test the extension and its GSM snapshot with that release.

## License and origin

GPL-3.0-or-later; see [LICENSE](LICENSE). The relay and its socket/desktop tests
were extracted from [Hachidori](https://github.com/bee-san/hachidori) at the
commit linked above. The Python relay behavior is preserved in the initial
release; this repository supplies its independent packaging and distribution.
