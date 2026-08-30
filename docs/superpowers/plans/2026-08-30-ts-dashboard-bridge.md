# TS Dashboard Bridge Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Bring the Google AI Studio dashboard design fully into the local repo and integrate it with the live Python backend, so the TypeScript-served dashboard shows real data (no mocks) and the launcher scripts run the whole stack.

**Architecture:** A zero-dependency TypeScript server (`server.ts`) on port **8800** serves the AI Studio dashboard design from `dashboard/static/` and reverse-proxies all `/api/*` requests to the live Python FastAPI backend on port **8799** (which stays authoritative). The TS layer is presentation-only: it serves the frontend, streams the real SSE cycle stream, injects the real control token (scraped from Python's own HTML, exactly like the PowerShell menu does), and forwards control actions only when explicitly enabled. The PowerShell menu (`scripts/spread-hunter-menu.ps1`) launches, records, and stops the bridge alongside the Python stack.

**Tech Stack:** Node.js ≥ 24 (native TypeScript type-stripping — **no `npm install`**), built-in `node:test` runner for TS tests, Python pytest for contract tests, PowerShell 7 launcher.

## Global Constraints

- Node.js must be ≥ 24 (`node --version` → `v24.x`). Type stripping is built in; do NOT add tsconfig, tsc, tsx, or any npm dependency.
- Zero dependencies: `server.ts` may only import from `node:http`, `node:fs`, `node:fs/promises`, `node:path`, `node:url`.
- Python FastAPI on `127.0.0.1:8799` is the single source of truth for data. Never write mock data into the bridge.
- Bridge listens on `127.0.0.1:8800` (env `PORT`). Python keeps 8799. Never change the Python dashboard port.
- Control actions are OFF by default. POST forwarding only happens when env `BRIDGE_CONTROL=1` is set. No exceptions.
- Read-only guarantee: with `BRIDGE_CONTROL` unset, every POST /api/* returns HTTP 501.
- Control-token contract is fixed: the frontend reads `const CONTROL_TOKEN = "<token>"` from served HTML; Python validates header `x-control-token`. The bridge must inject Python's real token into the HTML it serves — never a placeholder, never a made-up token.
- Operator commands are PowerShell; sequence with `;`, never `&&`.
- `python-BACKUP/` is the frozen snapshot of the Python world — do not modify or delete it.
- Python tests run with `python -m pytest -q`; TypeScript tests run with `node --test tests/bridge/`.
- Commit per task with a conventional message (`feat:`, `fix:`, `test:`, `chore:`).

---

### Task 1: Obtain the AI Studio dashboard files

**Files:**
- Modify (destination): `dashboard/static/app.js`, `dashboard/static/index.html`, `dashboard/static/styles.css`, `dashboard/static/strategy_explainer.html`
- Reference (do not deploy): `server.ts` from the AI Studio push (kept for contract reading only)
- Test: `tests/test_ts_frontend_present.py`

**Interfaces:**
- Consumes: an operator push/export of AI Studio's uncommitted dashboard work (the "5 changed files": `dashboard/static/app.js`, `dashboard/static/index.html`, `dashboard/static/styles.css`, `server.ts`, `dashboard/server.py`).
- Produces: the AI Studio design files present under `dashboard/static/` in the working tree, later consumed by the bridge's static serving (Task 3). Contract: `dashboard/static/index.html` contains the line `const CONTROL_TOKEN = "__LIVE_DASH_CONTROL_TOKEN__";`.

**Precondition (operator action):** From Google AI Studio, push the dashboard changes to the repository (recommended) or export as zip. The agent cannot do this — AI Studio is a separate client.

- [ ] **Step 1: Fetch whatever AI Studio pushed**

Run: `git fetch origin --prune && git branch -r | grep -iE "dashboard|ai"`

If AI Studio pushed to a branch (e.g. `origin/feat/dashboard-ai-studio`), note the name. If the user exported a zip, unzip it somewhere outside the repo and note the path. If AI Studio pushed to `main`, use `git diff 574269c..origin/main --stat` to see the delta.

- [ ] **Step 2: Copy the AI Studio design files into the working tree**

Do NOT commit server.ts yet. Copy only the static design files (replace the local Python-era versions):

```bash
# Replace with the real source location depending on Step 1:
#   git show origin/<branch>:dashboard/static/app.js > dashboard/static/app.js
# or: cp <unzipped-path>/dashboard/static/app.js dashboard/static/app.js
# Repeat for index.html, styles.css, strategy_explainer.html
git show origin/<branch>:dashboard/static/app.js > dashboard/static/app.js
git show origin/<branch>:dashboard/static/index.html > dashboard/static/index.html
git show origin/<branch>:dashboard/static/styles.css > dashboard/static/styles.css
git show origin/<branch>:dashboard/static/strategy_explainer.html > dashboard/static/strategy_explainer.html
```

(When AI Studio pushes, replace `<branch>` with the real remote branch name.)

- [ ] **Step 3: Verify the control-token placeholder survived**

Run:

```bash
grep -n 'CONTROL_TOKEN' dashboard/static/index.html | head
```

Expected: exactly one line `const CONTROL_TOKEN = "__LIVE_DASH_CONTROL_TOKEN__";`. If the placeholder is missing or renamed, STOP and report — the frontend contract is broken and every later task depends on it.

- [ ] **Step 4: Write the presence test (fails before the copy)**

Create `tests/test_ts_frontend_present.py`:

```python
"""The AI Studio dashboard design must be present in dashboard/static.

Every later task (bridge static serving, token injection) depends on these
files and on the control-token placeholder surviving the import.
"""
from pathlib import Path

STATIC = Path(__file__).resolve().parents[1] / "dashboard" / "static"


def test_design_files_present():
    for name in ("app.js", "index.html", "styles.css", "strategy_explainer.html"):
        assert (STATIC / name).is_file(), f"missing {name}"


def test_control_token_placeholder_present():
    html = (STATIC / "index.html").read_text(encoding="utf-8")
    assert 'const CONTROL_TOKEN = "__LIVE_DASH_CONTROL_TOKEN__";' in html


def test_frontend_calls_cycle_stream():
    app = (STATIC / "app.js").read_text(encoding="utf-8")
    assert "api/cycle-stream" in app
```

- [ ] **Step 5: Run the test — must pass**

Run: `python -m pytest tests/test_ts_frontend_present.py -q`
Expected: `3 passed`

- [ ] **Step 6: Commit**

```bash
git add dashboard/static/app.js dashboard/static/index.html dashboard/static/styles.css dashboard/static/strategy_explainer.html tests/test_ts_frontend_present.py
git commit -m "feat(dashboard): import AI Studio dashboard design into static/"
```

---

### Task 2: Freeze the API contract between frontend and Python

**Files:**
- Test: `tests/test_ts_frontend_contract.py` (new)

**Interfaces:**
- Consumes: `dashboard/static/app.js` (the AI Studio frontend) and `dashboard/server.py` (the Python routes).
- Produces: a regression test asserting that every endpoint the frontend calls is served by Python. Later tasks rely on this to know the proxy surface is complete.

**Background (verified):** The frontend calls exactly 16 endpoints: `api/active-markets`, `api/closed-markets`, `api/cycle-stream`, `api/guardrail-alerts`, `api/guardrail-health`, `api/kpi`, `api/pairs-activity`, `api/parameters`, `api/scan-state`, `api/state`, `api/system/cancel-all`, `api/system/reset`, `api/system/start`, `api/system/status`, `api/system/stop`, `api/system/sync`. Python defines all 22 `/api/*` routes including these 16. The test below proves that subset relationship forever.

- [ ] **Step 1: Write the contract test**

Create `tests/test_ts_frontend_contract.py`:

```python
"""Every /api/* endpoint the TS frontend calls must exist on the Python server.

The bridge (server.ts) can only proxy what Python actually serves; a frontend
calling an endpoint Python does not define would silently 404 through the
bridge. This test is the drift guard.
"""
import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
APP_JS = ROOT / "dashboard" / "static" / "app.js"
SERVER_PY = ROOT / "dashboard" / "server.py"


def _frontend_api_calls() -> set[str]:
    text = APP_JS.read_text(encoding="utf-8")
    return set(re.findall(r"api/[a-z0-9_/-]+", text))


def _python_api_routes() -> set[str]:
    text = SERVER_PY.read_text(encoding="utf-8")
    return set(re.findall(r'"/api/[a-z0-9_/-]+"', text))


def test_every_frontend_call_is_served_by_python():
    calls = _frontend_api_calls()
    routes = _python_api_routes()
    missing = {c for c in calls if c not in routes}
    assert not missing, f"frontend calls not served by Python: {sorted(missing)}"


def test_frontend_control_surface_is_expected():
    """The POST control surface is exactly the 6 endpoints the menu supports."""
    calls = _frontend_api_calls()
    control = {c for c in calls if "system/" in c}
    assert control == {
        "api/system/cancel-all",
        "api/system/reset",
        "api/system/start",
        "api/system/status",
        "api/system/stop",
        "api/system/sync",
    }
```

- [ ] **Step 2: Run the test — must pass**

Run: `python -m pytest tests/test_ts_frontend_contract.py -q`
Expected: `2 passed`

- [ ] **Step 3: Commit**

```bash
git add tests/test_ts_frontend_contract.py
git commit -m "test(dashboard): freeze frontend/Python API contract"
```

---

### Task 3: Bridge serves the AI Studio design and injects the real control token

**Files:**
- Modify: `server.ts`
- Test: `tests/bridge/token-inject.test.ts` (new)

**Interfaces:**
- Consumes: `dashboard/static/*` (Task 1), upstream Python at `http://127.0.0.1:8799` (env `PY_DASH_URL`).
- Produces:
  - `serveStatic(req, res, pathname)` — serves files from `dashboard/static`, path-traversal safe.
  - `scrapeControlToken(html: string): string` — regex-extracts the token Python baked into its own `/` HTML.
  - `injectToken(html: string, token: string): string` — replaces `__LIVE_DASH_CONTROL_TOKEN__` with the real token.
  - `getControlToken(): Promise<string>` — fetches `PY_DASH_URL + '/'`, scrapes, caches 60 s.
  - The `/` handler serves `index.html` with the placeholder replaced by the scraped token.

**Background (verified):** Python's `/` returns `index.html` with the line `const CONTROL_TOKEN = "<real-token>";`. The PowerShell menu already scrapes this exact pattern (`CONTROL_TOKEN\s*=\s*"([^"]+)"`). The bridge must do the same so that when the frontend POSTs, its `x-control-token` header carries a token Python will accept.

- [ ] **Step 1: Write the failing TS test**

Create `tests/bridge/token-inject.test.ts`:

```typescript
import { test } from 'node:test';
import assert from 'node:assert/strict';

// Import the pure functions from server.ts (type stripping handles the .ts import).
import { scrapeControlToken, injectToken } from '../../server.ts';

test('scrapeControlToken extracts the token Python baked into HTML', () => {
  const html = '<html><script>const CONTROL_TOKEN = "abc123";</script></html>';
  assert.equal(scrapeControlToken(html), 'abc123');
});

test('scrapeControlToken returns empty string when token missing', () => {
  assert.equal(scrapeControlToken('<html>no token</html>'), '');
});

test('injectToken replaces the placeholder', () => {
  const html = 'const CONTROL_TOKEN = "__LIVE_DASH_CONTROL_TOKEN__";';
  assert.equal(injectToken(html, 'xyz'), 'const CONTROL_TOKEN = "xyz";');
});

test('injectToken leaves HTML alone when token is empty', () => {
  const html = 'const CONTROL_TOKEN = "__LIVE_DASH_CONTROL_TOKEN__";';
  assert.equal(injectToken(html, ''), html);
});
```

- [ ] **Step 2: Run test to verify it fails**

Run: `node --test tests/bridge/token-inject.test.ts`
Expected: FAIL — `Cannot find module` / functions not exported (they do not exist yet).

- [ ] **Step 3: Implement token functions in server.ts**

Add near the top of `server.ts` (after the imports and constants):

```typescript
const TOKEN_PLACEHOLDER = '__LIVE_DASH_CONTROL_TOKEN__';
const TOKEN_RE = /CONTROL_TOKEN\s*=\s*"([^"]+)"/;

/** Extract the control token Python baked into its served HTML. */
export function scrapeControlToken(html: string): string {
  const m = html.match(TOKEN_RE);
  return m ? m[1] : '';
}

/** Replace the frontend placeholder with the real token. */
export function injectToken(html: string, token: string): string {
  if (!token) return html;
  return html.split(TOKEN_PLACEHOLDER).join(token);
}

let cachedToken = '';
let cachedAt = 0;
const TOKEN_TTL_MS = 60_000;

/** Fetch Python's own HTML and scrape the live token, cached 60 s. */
export async function getControlToken(): Promise<string> {
  const now = Date.now();
  if (cachedToken && now - cachedAt < TOKEN_TTL_MS) return cachedToken;
  try {
    const res = await fetch(`${up.origin}/`, { signal: AbortSignal.timeout(4000) });
    const html = await res.text();
    cachedToken = scrapeControlToken(html);
    cachedAt = now;
  } catch {
    cachedToken = '';
  }
  return cachedToken;
}
```

(`fetch` is global in Node 24 — no import needed.)

- [ ] **Step 4: Wire the token into the `/` handler**

In `server.ts`, change the `serveStatic` call for the `/` path so the HTML is token-injected. Replace the routing block's root branch:

```typescript
  if (pathname === '/' || pathname === '/index.html' || pathname === '/strategy_explainer.html') {
    if (pathname === '/strategy_explainer.html') {
      return serveStatic(req, res, '/strategy_explainer.html');
    }
    return serveIndex(req, res);
  }
```

And add `serveIndex` (near `serveStatic`):

```typescript
async function serveIndex(req: IncomingMessage, res: ServerResponse): Promise<void> {
  const indexPath = path.join(STATIC_DIR, 'index.html');
  try {
    if (!existsSync(indexPath)) {
      return json(res, 404, { ok: false, error: 'index.html not found' });
    }
    const html = await readFile(indexPath, 'utf8');
    const token = await getControlToken();
    const out = injectToken(html, token);
    res.writeHead(200, {
      'Content-Type': 'text/html; charset=utf-8',
      'Cache-Control': 'no-cache, no-store, must-revalidate',
    });
    res.end(out);
  } catch {
    json(res, 500, { ok: false, error: 'unreadable' });
  }
}
```

- [ ] **Step 5: Run the test — must pass**

Run: `node --test tests/bridge/token-inject.test.ts`
Expected: PASS (`4 tests`).

- [ ] **Step 6: Verify the running bridge now serves the injected token**

Restart the bridge, then compare against Python's own token:

```powershell
# stop old bridge, start new one (see Task 5 for the canonical launcher; manual for now)
Get-CimInstance Win32_Process | Where-Object { $_.Name -eq "node.exe" -and $_.CommandLine -match "server.ts" } | ForEach-Object { Stop-Process -Id $_.ProcessId -Force -ErrorAction SilentlyContinue }
Start-Sleep 1
$env:PORT = "8800"
Start-Process "node" -ArgumentList "server.ts" -WorkingDirectory "C:\Users\Tiger\Agents\Projects\spread-hunter-live" -RedirectStandardOutput "$PWD\runtime\ts-bridge.log" -RedirectStandardError "$PWD\runtime\ts-bridge.err.log" -WindowStyle Hidden
Start-Sleep 3
```

Then:

```bash
echo "token served by bridge:"; curl -s http://127.0.0.1:8800/ | grep -oE 'CONTROL_TOKEN = "[^"]+"' | head -1
echo "token baked by python: "; curl -s http://127.0.0.1:8799/ | grep -oE 'CONTROL_TOKEN = "[^"]+"' | head -1
```

Expected: the two tokens are **equal** and neither contains `__LIVE_DASH_CONTROL_TOKEN__`.

- [ ] **Step 7: Commit**

```bash
git add server.ts tests/bridge/token-inject.test.ts
git commit -m "feat(dashboard): bridge injects live Python control token"
```

---

### Task 4: Forward control actions behind an explicit switch

**Files:**
- Modify: `server.ts`
- Test: `tests/bridge/control-gate.test.ts` (new)

**Interfaces:**
- Consumes: `getControlToken()` (Task 3), Python's POST endpoints (`/api/system/start|stop|cancel-all|reset|sync`), the frontend's `x-control-token` header.
- Produces: `proxyControl(req, res, pathname)` — forwards POST /api/system/* to Python with the frontend's token header; the read-only 501 guard remains for every other non-GET request and for all POSTs while `BRIDGE_CONTROL` is unset.

**Constraint (Global):** control forwarding is OFF unless env `BRIDGE_CONTROL=1`.

- [ ] **Step 1: Write the failing test**

Create `tests/bridge/control-gate.test.ts`:

```typescript
import { test } from 'node:test';
import assert from 'node:assert/strict';
import { allowControl } from '../../server.ts';

test('control is off by default', () => {
  delete process.env.BRIDGE_CONTROL;
  assert.equal(allowControl(), false);
});

test('control is on only for explicit 1', () => {
  process.env.BRIDGE_CONTROL = '1';
  assert.equal(allowControl(), true);
});

test('any other value keeps it off', () => {
  process.env.BRIDGE_CONTROL = 'yes';
  assert.equal(allowControl(), false);
});
```

- [ ] **Step 2: Run test to verify it fails**

Run: `node --test tests/bridge/control-gate.test.ts`
Expected: FAIL — `allowControl` not exported.

- [ ] **Step 3: Implement the gate and the POST forwarder in server.ts**

Add:

```typescript
/** Control actions are OFF unless BRIDGE_CONTROL=1 explicitly. */
export function allowControl(): boolean {
  return process.env.BRIDGE_CONTROL === '1';
}
```

Replace the read-only guard in the routing block:

```typescript
  if (pathname.startsWith('/api/')) {
    const method = (req.method || 'GET').toUpperCase();
    if (method !== 'GET' && method !== 'HEAD') {
      const isControl = pathname.startsWith('/api/system/');
      if (!isControl || !allowControl()) {
        // Read-only bridge: protect live state unless control is explicitly on.
        return json(res, 501, { ok: false, error: 'read-only bridge; control actions are not proxied yet' });
      }
      return proxyControl(req, res, pathname);
    }
    return proxyApi(req, res, pathname);
  }
```

Add `proxyControl` (near `proxyApi`):

```typescript
/** Forward a POST control action to Python, passing the frontend's token. */
function proxyControl(clientReq: IncomingMessage, clientRes: ServerResponse, pathname: string): void {
  const search = clientReq.url ? url.parse(clientReq.url).search || '' : '';
  const upUrl = `${up.origin}${pathname}${search}`;

  const headers: Record<string, string> = {
    accept: (clientReq.headers.accept as string) ?? '*/*',
    'content-type': (clientReq.headers['content-type'] as string) ?? 'application/json',
  };
  const token = clientReq.headers['x-control-token'] as string | undefined;
  if (token) headers['x-control-token'] = token;

  const proxyReq = httpRequest(
    upUrl,
    { method: 'POST', headers },
    (upRes) => {
      clientRes.writeHead(upRes.statusCode || 502, { ...upRes.headers });
      upRes.pipe(clientRes);
      clientReq.on('close', () => upRes.destroy());
    },
  );
  proxyReq.on('error', () => {
    if (!clientRes.writableEnded) {
      json(clientRes, 502, { ok: false, error: 'upstream unreachable' });
    }
  });
  clientReq.pipe(proxyReq);
}
```

- [ ] **Step 4: Run the unit test — must pass**

Run: `node --test tests/bridge/control-gate.test.ts`
Expected: PASS (`3 tests`).

- [ ] **Step 5: Verify the gate live (still read-only without the env)**

Restart the bridge with `BRIDGE_CONTROL` unset (see Task 3 Step 6), then:

```bash
curl -s -X POST http://127.0.0.1:8800/api/system/status -o /dev/null -w "POST without control: HTTP %{http_code}\n"
```

Expected: `HTTP 501`.

Then restart with control enabled and confirm it forwards (Python will answer `400`/`200` — the point is it is no longer 501; it reached Python):

```powershell
$env:BRIDGE_CONTROL = "1"
Start-Process "node" -ArgumentList "server.ts" -WorkingDirectory "C:\Users\Tiger\Agents\Projects\spread-hunter-live" -RedirectStandardOutput "$PWD\runtime\ts-bridge.log" -RedirectStandardError "$PWD\runtime\ts-bridge.err.log" -WindowStyle Hidden
Start-Sleep 3
```

```bash
curl -s -X POST http://127.0.0.1:8800/api/system/status -o /dev/null -w "POST with control: HTTP %{http_code}\n"
```

Expected: NOT 501 (Python's own response, e.g. `400` for a control action sent to the wrong endpoint or `200` for a valid one). After the check, restart the bridge with `BRIDGE_CONTROL` unset so tonight's session stays read-only.

- [ ] **Step 6: Commit**

```bash
git add server.ts tests/bridge/control-gate.test.ts
git commit -m "feat(dashboard): forward control actions behind BRIDGE_CONTROL switch"
```

---

### Task 5: Launch the bridge from the PowerShell menu

**Files:**
- Modify: `scripts/spread-hunter-menu.ps1`
- Test: parse check + live status check (no unit test framework for PS1; the repo convention is parse-verify + hands-on verify)

**Interfaces:**
- Consumes: the bridge start command `node server.ts` with `PORT=8800` and the session DB path; the existing shadow-session recording structure (`runtime/shadow-session.json`).
- Produces:
  - `Start-TsBridge` / `Stop-TsBridge` functions.
  - A new `ts_bridge` entry in the shadow session record (pid + port).
  - Status page shows the bridge as its own row (`TS Bridge   ON   server.ts   PID ... · http://127.0.0.1:8800`).
  - `stop-shadow` stops the bridge along with the Python processes.

**Background (verified):** The menu already launches the Python dashboard via `python -m dashboard.server --db <db> --port 8799` and records PIDs in `runtime/shadow-session.json`. The bridge is a sibling process: `node server.ts` with env `PORT=8800`.

- [ ] **Step 1: Add the bridge launch/stop functions**

Insert after the existing shadow-dashboard launch helpers in `scripts/spread-hunter-menu.ps1`:

```powershell
function Start-TsBridge {
    <# Launch the TypeScript dashboard bridge (server.ts) detached on :8800.
       It reverse-proxies /api/* to the Python dashboard on :8799. #>
    param([string]$LogPrefix = "ts-bridge")
    $log = Join-Path $script:RuntimeDir "$LogPrefix.log"
    $err = Join-Path $script:RuntimeDir "$LogPrefix.err.log"
    $old = Get-Process -Name node -ErrorAction SilentlyContinue |
        Where-Object { $_.Path -and $_.Path -match 'node' -and (Get-CimInstance Win32_Process -Filter "ProcessId = $($_.Id)" -ErrorAction SilentlyContinue).CommandLine -match 'server\.ts' }
    foreach ($p in $old) { Stop-Process -Id $p.Id -Force -ErrorAction SilentlyContinue }
    Start-Sleep -Milliseconds 500
    $psi = New-Object System.Diagnostics.ProcessStartInfo
    $psi.FileName = "node"
    $psi.Arguments = "server.ts"
    $psi.WorkingDirectory = $script:ProjectRoot
    $psi.UseShellExecute = $false
    $psi.EnvironmentVariables["PORT"] = "8800"
    $psi.RedirectStandardOutput = $true
    $psi.RedirectStandardError = $true
    $proc = [System.Diagnostics.Process]::Start($psi)
    $proc.OutputDataReceived.Add({ if ($args[1]) { $args[1].Data | Out-File -FilePath $log -Append } }) | Out-Null
    $proc.ErrorDataReceived.Add({ if ($args[1]) { $args[1].Data | Out-File -FilePath $err -Append } }) | Out-Null
    $proc.BeginOutputReadLine(); $proc.BeginErrorReadLine()
    Start-Sleep -Seconds 3
    $ok = $false
    try { $ok = (Invoke-WebRequest -Uri "http://127.0.0.1:8800/" -UseBasicParsing -TimeoutSec 5).StatusCode -eq 200 } catch {}
    if (-not $ok) { throw "TS bridge did not come up on :8800 (see $err)" }
    return @{ pid = $proc.Id; port = 8800 }
}

function Stop-TsBridge {
    <# Stop the TS bridge (and any orphaned node server.ts). #>
    Get-CimInstance Win32_Process | Where-Object { $_.Name -eq 'node.exe' -and $_.CommandLine -match 'server\.ts' } |
        ForEach-Object { Stop-Process -Id $_.ProcessId -Force -ErrorAction SilentlyContinue }
}
```

- [ ] **Step 2: Start the bridge in the statistical-run flow**

Find the block in the statistical-run action that starts the dashboard and records `shadow_db`/`run_id`, and add the bridge start + record after the dashboard start. If the session record is written as JSON, add `"ts_bridge": { "pid": <pid>, "port": 8800 }`:

```powershell
# after the Python dashboard is confirmed serving:
$tsBridge = Start-TsBridge -LogPrefix "ts-bridge-$($session.run_id)"
$session.ts_bridge = $tsBridge
$session | ConvertTo-Json -Depth 6 | Set-Content -Path $script:ShadowSessionFile -Encoding UTF8
Write-Host " [✅ ] TS dashboard bridge on http://127.0.0.1:8800 (PID $($tsBridge.pid)) -> proxies :8799"
```

- [ ] **Step 3: Stop the bridge in the shadow-stop flow**

Find `stop-shadow` / the shadow teardown block and add `Stop-TsBridge` before or alongside the dashboard stop:

```powershell
Stop-TsBridge
```

- [ ] **Step 4: Show the bridge in status**

In the status renderer, add a row in the shadow-dashboard section:

```powershell
$tsPid = $session.ts_bridge.pid
Write-Host "  TS Bridge                 $(if ($tsPid -and (Get-Process -Id $tsPid -ErrorAction SilentlyContinue)) { 'ON' } else { 'OFF' })" -NoNewline
```

(follow the existing row format/colors used by adjacent rows; keep relative-path/color conventions from the status page).

- [ ] **Step 5: Parse-check the menu**

Run:

```powershell
pwsh -NoProfile -Command '$errors = $null; $tokens = $null; [System.Management.Automation.Language.Parser]::ParseFile("C:\Users\Tiger\Agents\Projects\spread-hunter-live\scripts\spread-hunter-menu.ps1", [ref]$tokens, [ref]$errors) | Out-Null; if ($errors.Count -eq 0) { "PARSE OK" } else { $errors | ForEach-Object { $_.Message }; exit 1 }'
```

Expected: `PARSE OK`.

- [ ] **Step 6: Verify live against tonight's session**

Run:

```powershell
.\scripts\spread-hunter-menu.ps1 status
```

Expected: the shadow-dashboard section now shows `TS Bridge  ON  server.ts  PID <pid> · http://127.0.0.1:8800`, the Python rows still `ON`, and BOT STACK `OFF`. Then open `http://127.0.0.1:8800` and confirm the dashboard renders the AI Studio design with real data (run ID, DB path, filter cycles from the SSE stream).

- [ ] **Step 7: Commit**

```bash
git add scripts/spread-hunter-menu.ps1
git commit -m "feat(menu): launch and stop TS dashboard bridge alongside shadow stack"
```

---

### Task 6: End-to-end verification and operator docs

**Files:**
- Modify: `README.md` (or `docs/operators/*` if the repo keeps operator docs there)
- Test: hands-on verification (per repo rules, operator verification is surface behavior, not pytest)

**Interfaces:**
- Consumes: everything from Tasks 1–5.

- [ ] **Step 1: Full-stack smoke test**

Confirm all processes and both ports:

```powershell
.\scripts\spread-hunter-menu.ps1 status
```

Expected: Python dashboard `ON` (:8799), TS Bridge `ON` (:8800), validation loop/observer/watcher `ON`, BOT STACK `OFF`.

```bash
curl -s http://127.0.0.1:8800/api/state | head -c 200; echo
curl -s -N --max-time 2 http://127.0.0.1:8800/api/cycle-stream | head -c 120; echo
curl -s -X POST http://127.0.0.1:8800/api/system/start -o /dev/null -w "POST guard: %{http_code}\n"
```

Expected: real state JSON; real SSE events; `POST guard: 501` (read-only for tonight).

- [ ] **Step 2: Verify the token round-trip**

```bash
TOK=$(curl -s http://127.0.0.1:8800/ | grep -oE 'CONTROL_TOKEN = "[^"]+"' | cut -d'"' -f2)
curl -s -X POST http://127.0.0.1:8800/api/system/status -H "x-control-token: $TOK" -o /dev/null -w "guarded POST with token: %{http_code}\n"
```

Expected: still `501` (read-only mode ignores even valid tokens — by design tonight).

- [ ] **Step 3: Write the operator docs**

Add to `README.md` a short "TS dashboard bridge" section:

```markdown
## TS Dashboard Bridge

The TypeScript dashboard (`server.ts`) serves the AI Studio frontend from
`dashboard/static/` on **http://127.0.0.1:8800** and reverse-proxies `/api/*`
to the live Python backend on **:8799** — real data only, no mocks.

- Start: `.\scripts\spread-hunter-menu.ps1 statistical-run -Hours 24` (starts the
  bridge alongside the Python stack).
- Stop: `.\scripts\spread-hunter-menu.ps1 stop-shadow`.
- Read-only by default: POST control actions return 501 unless the bridge is
  started with `BRIDGE_CONTROL=1`. Do not enable this during shadow runs.
- Manual run: `node server.ts` (Node ≥ 24, no npm install; `PORT` env overrides,
  `PY_DASH_URL` overrides the upstream).
- The bridge injects the live control token into the HTML it serves, exactly
  like the Python dashboard does, so the frontend's control buttons carry a
  token Python will accept once control forwarding is enabled.
```

- [ ] **Step 4: Commit**

```bash
git add README.md
git commit -m "docs(dashboard): document the TS dashboard bridge"
```

---

### Task 7 (optional, only if AI Studio's server.ts has unique presentation): reconcile AI Studio server.ts

**Files:**
- Read: AI Studio's `server.ts` (from Task 1 fetch)
- Modify: `server.ts` only if review finds unique presentation logic (e.g. HTML injection, route decorators) missing from the bridge

**Interfaces:**
- Consumes: the fetched AI Studio `server.ts`.
- Produces: a decision record — either "bridge already covers it" (likely; the mock routes mirror Python's) or a small diff porting any genuinely novel presentation behavior.

- [ ] **Step 1: Diff AI Studio's server.ts against the bridge's behavior**

```bash
git show origin/<branch>:server.ts | grep -nE "app\.(get|post)\(" | head -40
```

Compare against the endpoint table in this plan's Task 2. Every route AI Studio mocks already exists on Python; the bridge proxies them all.

- [ ] **Step 2: Port only genuine presentation logic, if any**

If AI Studio's server.ts contains anything the bridge does not (e.g. response decoration, extra headers, template injection beyond the token), port it into `server.ts` with a test in `tests/bridge/`. If not — the likely case — record the decision:

```bash
git commit -m "chore(dashboard): confirm AI Studio server.ts presentation covered by bridge"
```

(Use `git add -A` only if there are actually changes; otherwise skip the commit and note the decision in the PR description.)

---

## Self-Review

**1. Spec coverage.**
- "Bring AI Studio dashboard design in full" → Task 1 (static files) + Task 3 (bridge serves them with token injection).
- "Integrate the backend logic and scripts" → Task 2 (contract freeze), Task 4 (control forwarding behind a switch), Task 5 (PowerShell launcher integration), Task 6 (operator docs).
- "Fully serve actual data, not mocks" → the bridge proxies Python; no mock data is written anywhere (Task 3/4 code contains none); Task 6 verifies real data end-to-end.

**2. Placeholder scan.** The only external dependency is Task 1's operator push/export from AI Studio (unavoidable — the files do not exist locally yet); the copy step names the exact source and the placeholder-check step fails fast if the contract breaks. No "implement later"/"handle edge cases" steps remain; every code step shows complete code.

**3. Type consistency.** `scrapeControlToken(html): string`, `injectToken(html, token): string`, `getControlToken(): Promise<string>`, `allowControl(): boolean`, `proxyControl(req, res, pathname)`, `serveIndex(req, res)`, `Start-TsBridge`/`Stop-TsBridge` are defined once each and referenced with identical signatures across tasks. Test files import from `../../server.ts` consistently. Env names `PORT`, `PY_DASH_URL`, `BRIDGE_CONTROL` and ports 8799/8800 are used identically everywhere.

**Known gap (accepted):** Gemini — AI Studio's `server.ts` imports `@google/genai` but never calls `generateContent`; the feature is inert upstream. Wiring real Gemini is out of scope for this plan (would add an npm dependency, violating the zero-dep constraint). Recorded as a follow-up, not a task.
