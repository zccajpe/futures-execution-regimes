/**
 * Research-side batch fetcher. Reuses the existing headless client `rpc`.
 * Writes datasets into the platform's local database and only prints summaries.
 *
 * usage: node fetch_min.mjs <tasks.json> <results.jsonl> [--concurrency 2] [--limit N]
 *
 * Task shape:
 *   { "name": "...", "method": "get_future_min", "symbol": "RB2701.SHF",
 *     "start": "20260105", "end": "20260116", "tz": ["09:00","11:30"],
 *     "fields": ["datetime","trading_date","open","high","low","close","volume","amount","open_interest"] }
 * For non-minute methods (get_future_dominant / get_future_detail / get_future_daily)
 * pass "params" directly and omit symbol/start/end.
 *
 * The QuantStudio headless client is NOT part of this repository. It is resolved from the
 * operator's existing installation: ~/.local/share/quantstudio-headless by default, or the
 * directory named by QUANTSTUDIO_HEADLESS_DIR. Neither the client nor its config is copied
 * here, and the config content is never printed.
 */
import { readFile, appendFile } from 'node:fs/promises';
import { existsSync } from 'node:fs';
import { homedir } from 'node:os';
import { join } from 'node:path';
import { pathToFileURL } from 'node:url';

const args = process.argv.slice(2);
const tasksPath = args[0];
const resultsPath = args[1];
const opt = (name, dflt) => {
  const i = args.indexOf(name);
  return i >= 0 ? args[i + 1] : dflt;
};
const CONCURRENCY = Number(opt('--concurrency', '2'));
const LIMIT = Number(opt('--limit', '0')) || Infinity;

const HEADLESS_DIR = process.env.QUANTSTUDIO_HEADLESS_DIR
  || join(homedir(), '.local', 'share', 'quantstudio-headless');
const CLIENT_PATH = join(HEADLESS_DIR, 'client.mjs');
const CONFIG_PATH = join(HEADLESS_DIR, 'config.json');

// Resolved lazily: an empty task list needs neither the client nor its config.
let _rpc = null;
let _config = null;
async function client() {
  if (_rpc === null) {
    if (!existsSync(CLIENT_PATH)) {
      throw new Error(`quantstudio headless client not found at ${CLIENT_PATH}; `
        + 'set QUANTSTUDIO_HEADLESS_DIR to the existing installation');
    }
    ({ rpc: _rpc } = await import(pathToFileURL(CLIENT_PATH).href));
  }
  if (_config === null) {
    if (!existsSync(CONFIG_PATH)) {
      throw new Error(`quantstudio headless config not found at ${CONFIG_PATH}; `
        + 'set QUANTSTUDIO_HEADLESS_DIR to the existing installation');
    }
    _config = JSON.parse(await readFile(CONFIG_PATH, 'utf8'));
  }
  return { rpc: _rpc, config: _config };
}

const tasks = JSON.parse(await readFile(tasksPath, 'utf8'));

const done = new Set();
if (existsSync(resultsPath)) {
  for (const line of (await readFile(resultsPath, 'utf8')).split('\n')) {
    if (!line.trim()) continue;
    try {
      const rec = JSON.parse(line);
      if (rec.ok) done.add(rec.name);
    } catch { /* ignore malformed trailing line */ }
  }
}

const pending = tasks.filter(t => !done.has(t.name)).slice(0, LIMIT);
if (!pending.length) {
  console.log(JSON.stringify({ pending: 0, already: done.size }));
  process.exit(0);
}

const DEFAULT_FIELDS = ['datetime', 'trading_date', 'open', 'high', 'low', 'close', 'volume', 'amount', 'open_interest'];

function toInput(t) {
  if (t.params) {
    return { name: t.name, source: { kind: 'pandadata', method: t.method, params: t.params },
      kind: t.kind ?? 'timeseries', category: t.category ?? 'other',
      ...(t.dateColumn ? { dateColumn: t.dateColumn } : {}), ttlSeconds: t.ttlSeconds ?? 31536000 };
  }
  const params = {
    symbol: t.symbol, start_date: t.start, end_date: t.end,
    frequency: t.frequency ?? '1m', time_zone: t.tz, fields: t.fields ?? DEFAULT_FIELDS,
  };
  return { name: t.name, source: { kind: 'pandadata', method: t.method ?? 'get_future_min', params },
    kind: 'timeseries', category: 'market', dateColumn: 'datetime', ttlSeconds: t.ttlSeconds ?? 31536000 };
}

async function run(t) {
  const t0 = Date.now();
  try {
    const { rpc, config } = await client();
    const out = await rpc(config, 'pandaMcp/databaseFetch', { input: toInput(t) });
    return { name: t.name, ok: true, id: out.id, rowCount: out.rowCount, from: out.from, to: out.to,
      bytes: out.bytes, ms: Date.now() - t0 };
  } catch (error) {
    return { name: t.name, ok: false, error: String(error.message ?? error), ms: Date.now() - t0 };
  }
}

let cursor = 0;
let finished = 0;
async function worker() {
  while (cursor < pending.length) {
    const t = pending[cursor++];
    const rec = await run(t);
    await appendFile(resultsPath, JSON.stringify(rec) + '\n');
    finished++;
    console.log(JSON.stringify(rec));
  }
}

await Promise.all(Array.from({ length: Math.max(1, Math.min(CONCURRENCY, pending.length)) }, worker));
console.log(JSON.stringify({ finished, total_pending: pending.length, already: done.size }));
