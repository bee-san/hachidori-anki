// The add-on's relay as a plain process for the raw-socket contract tests.
// SPDX-License-Identifier: GPL-3.0-or-later
import { spawn } from "node:child_process";
import { fileURLToPath } from "node:url";

// CI points this at server.py extracted from the packaged add-on.
// `apiPort`: undefined leaves the HTTP API off; 0 picks a free port.
export async function startAnkiRelayServer({ port = 0, pingMs = 20_000, apiPort, apiTimeoutSeconds } = {}) {
  const server = process.env.HACHIDORI_RELAY_SERVER || fileURLToPath(new URL("../addon/server.py", import.meta.url));
  const args = [server, "--port", String(port), "--ping-seconds", String(pingMs / 1000)];
  if (apiPort !== undefined) args.push("--api-port", String(apiPort));
  if (apiTimeoutSeconds !== undefined) args.push("--api-timeout-seconds", String(apiTimeoutSeconds));
  const child = spawn("python3", args, { stdio: ["ignore", "pipe", "inherit"] });
  let exitCode = null;
  const exited = new Promise((resolveExit) => child.once("exit", (code, signal) => {
    exitCode = code ?? signal;
    resolveExit();
  }));
  // The relay prints `listening <port>`, then `api <port>` or `api-failed <reason>` when asked for the API.
  const announced = await new Promise((resolveAnnounced, rejectAnnounced) => {
    const found = {};
    let text = "";
    child.once("error", rejectAnnounced);
    exited.then(() => rejectAnnounced(new Error(`the Anki relay exited with ${exitCode}`)));
    child.stdout.on("data", (chunk) => {
      text += String(chunk);
      let newline;
      while ((newline = text.indexOf("\n")) !== -1) {
        const line = text.slice(0, newline).trim();
        text = text.slice(newline + 1);
        const [word, ...rest] = line.split(" ");
        if (word === "listening") found.port = Number(rest[0]);
        else if (word === "api") found.apiPort = Number(rest[0]);
        else if (word === "api-failed") found.apiError = rest.join(" ");
      }
      if (found.port !== undefined && (apiPort === undefined || found.apiPort !== undefined || found.apiError !== undefined)) resolveAnnounced(found);
    });
  });
  return {
    port: announced.port,
    apiPort: announced.apiPort ?? null,
    apiError: announced.apiError ?? null,
    get exitCode() { return exitCode; },
    close() {
      child.kill();
      return exited;
    },
  };
}
