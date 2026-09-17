/*
 * The engine runs here, off the main thread, so a two-second evaluation never
 * freezes the board. Messages in: { id, method, path, body }. Messages out:
 * { id, result } for requests and { type: 'status', text, ready } for boot
 * progress. The Python side is demo/engine.py; the engine package itself is
 * research.zip, built by demo/build.py from the shipped research/ tree.
 */
// Pyodide 314 ships as an ES module and refuses classic workers, so this file
// is loaded with `new Worker('worker.js', { type: 'module' })` by shim.js.
import { loadPyodide } from 'https://cdn.jsdelivr.net/pyodide/v314.0.7/full/pyodide.mjs';

const PYODIDE_INDEX = 'https://cdn.jsdelivr.net/pyodide/v314.0.7/full/';
// One scientific trial every few seconds: fast enough that the discovery
// budget closes inside a minute, slow enough that a visitor arriving cold
// watches the ledger fill instead of finding a finished board.
const TICK_MS = 3500;
let engine = null;
let ready = false;

function status(text, extra) {
  postMessage(Object.assign({ type: 'status', text, ready }, extra || {}));
}

async function boot() {
  status('Loading Pyodide (Python 3.14 in WebAssembly)…');
  const pyodide = await loadPyodide({ indexURL: PYODIDE_INDEX });
  await pyodide.loadPackage('tzdata');
  status('Fetching the engine package…');
  const zip = await (await fetch('research.zip', { cache: 'no-cache' })).arrayBuffer();
  pyodide.unpackArchive(zip, 'zip');
  const source = await (await fetch('engine.py', { cache: 'no-cache' })).text();
  pyodide.FS.writeFile('engine.py', source);
  status('Materializing the synthetic tape and freezing the stage stores…');
  engine = pyodide.pyimport('engine');
  engine.boot();
  ready = true;
  status('Engine ready', { ready: true, mission: engine.MISSION });
  // One scientific trial per tick. The board polls every five seconds, so a
  // viewer sees the ledger grow a row or two at a time — the point of the show.
  setInterval(() => {
    if (!engine) return;
    try {
      const result = engine.tick();
      const detail = result && typeof result.toJs === 'function' ? result.toJs({ dict_converter: Object.fromEntries }) : result;
      if (detail && !detail.idle) postMessage({ type: 'tick', detail });
      if (result && typeof result.destroy === 'function') result.destroy();
    } catch (error) {
      postMessage({ type: 'log', text: `tick failed: ${error}` });
    }
  }, TICK_MS);
}

const booting = boot().catch((error) => {
  status(`Boot failed: ${error && error.message ? error.message : error}`, { failed: true });
  throw error;
});

self.onmessage = async (event) => {
  const { id, method, path, body } = event.data;
  try {
    await booting;
    const result = engine.handle(method, path, body == null ? null : JSON.stringify(body));
    postMessage({ id, result });
  } catch (error) {
    postMessage({ id, result: JSON.stringify([503, { error: String(error && error.message || error) }]) });
  }
};
