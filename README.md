<!-- SPDX-License-Identifier: GPL-3.0-or-later -->

# Hachidori Relay for Anki

Share one [Hachidori](https://github.com/bee-san/hachidori) library between
browser installs. Anki runs the small WebSocket relay while it is open; the
browser with your dictionaries hosts, and your other browsers link to it.

## Install

1. Download [hachidori-relay.ankiaddon v0.0.3](https://github.com/bee-san/hachidori-anki/releases/download/v0.0.3/hachidori-relay.ankiaddon).
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
HACHIDORI_RELAY_SERVER=dist/unpacked/server.py node --test test/sharing-relay.test.mjs
```

The package tests verify root-level files, package identity, version, commit
timestamp, checksum, reproducibility, and exclusion of caches and user config.
The socket tests run the packaged relay and cover origins, host ownership,
idle connections, bidirectional text, broadcast, pings, disconnections, stalled
peers with ordered large UTF-8 replies, and enabling and disabling
network sharing. Network cases require a non-loopback address; the suite reports
when the machine has none.

With a Python interpreter that can import the installed `anki` and `aqt`:

```sh
python3 test/anki-relay-desktop.py dist/hachidori-relay.ankiaddon
```

This launches Anki with a temporary profile, extracts the archive there, and
checks the relay. To run the relay by itself during development:

```sh
python3 addon/server.py --port 8771
```

`addon/` contains the Anki entry point, relay server, and defaults. The packager
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
