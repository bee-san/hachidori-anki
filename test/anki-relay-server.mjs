// The add-on's relay as a plain process for the raw-socket contract tests.
// SPDX-License-Identifier: GPL-3.0-or-later
import { spawn } from "node:child_process";
import { fileURLToPath } from "node:url";

// CI points this at server.py extracted from the packaged add-on.
export async function startAnkiRelayServer({ port = 0, pingMs = 20_000 } = {}) {
  const server = process.env.HACHIDORI_RELAY_SERVER || fileURLToPath(new URL("../addon/server.py", import.meta.url));
  const child = spawn("python3", [server, "--port", String(port), "--ping-seconds", String(pingMs / 1000)], { stdio: ["ignore", "pipe", "inherit"] });
  let exitCode = null;
  const exited = new Promise((resolveExit) => child.once("exit", (code, signal) => {
    exitCode = code ?? signal;
    resolveExit();
  }));
  const boundPort = await new Promise((resolvePort, rejectPort) => {
    child.once("error", rejectPort);
    exited.then(() => rejectPort(new Error(`the Anki relay exited with ${exitCode}`)));
    child.stdout.once("data", (chunk) => resolvePort(Number(String(chunk).trim().split(" ")[1])));
  });
  return {
    port: boundPort,
    get exitCode() { return exitCode; },
    close() {
      child.kill();
      return exited;
    },
  };
}
