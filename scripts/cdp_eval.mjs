// Read a rendered Plotly chart through a local Chromium DevTools connection.
// Requires Node.js 22.4+ for its built-in WebSocket client.
import { readFileSync } from 'node:fs';

const [port, expressionFile] = process.argv.slice(2);
if (!port || !expressionFile || typeof WebSocket === 'undefined') {
  throw new Error('Usage: node cdp_eval.mjs <port> <expression-file> (Node.js 22.4+)');
}

const expression = readFileSync(expressionFile, 'utf8');
const base = `http://127.0.0.1:${port}`;
const deadline = Date.now() + 60000;
const sleep = (ms) => new Promise((resolve) => setTimeout(resolve, ms));

async function findPage() {
  const response = await fetch(`${base}/json/list`);
  if (!response.ok) throw new Error(`CDP target list: HTTP ${response.status}`);
  const pages = await response.json();
  return pages.find((page) => page.type === 'page' &&
    page.url.includes('lookintobitcoin.com'));
}

let page;
while (!page && Date.now() < deadline) {
  try { page = await findPage(); } catch { /* browser still starting */ }
  if (!page) await sleep(500);
}
if (!page) throw new Error('LookIntoBitcoin browser tab was not found');

const socket = new WebSocket(page.webSocketDebuggerUrl);
await new Promise((resolve, reject) => {
  const timer = setTimeout(() => reject(new Error('CDP WebSocket timeout')), 10000);
  socket.onopen = () => { clearTimeout(timer); resolve(); };
  socket.onerror = () => { clearTimeout(timer); reject(new Error('CDP WebSocket failed')); };
});

let nextId = 0;
function evaluate() {
  const id = ++nextId;
  return new Promise((resolve, reject) => {
    const timer = setTimeout(() => reject(new Error('CDP evaluation timeout')), 10000);
    socket.onmessage = (event) => {
      const reply = JSON.parse(event.data);
      if (reply.id !== id) return;
      clearTimeout(timer);
      if (reply.error || reply.result?.exceptionDetails) {
        reject(new Error(JSON.stringify(reply.error || reply.result.exceptionDetails)));
      } else {
        resolve(reply.result?.result?.value);
      }
    };
    socket.send(JSON.stringify({
      id, method: 'Runtime.evaluate',
      params: { expression, returnByValue: true, awaitPromise: false },
    }));
  });
}

try {
  let lastError = 'chart did not render';
  while (Date.now() < deadline) {
    const value = await evaluate();
    if (typeof value === 'string') {
      const data = JSON.parse(value);
      if (data.traces?.some((trace) => typeof trace.last?.[1] === 'number')) {
        process.stdout.write(value);
        process.exitCode = 0;
        break;
      }
      lastError = data.error || 'chart has no numeric data';
    }
    await sleep(1500);
  }
  if (process.exitCode !== 0) throw new Error(lastError);
} finally {
  socket.close();
}
