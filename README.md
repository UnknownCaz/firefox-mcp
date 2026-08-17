# firefox-mcp

An **attach-only** [MCP](https://modelcontextprotocol.io) server that lets Claude
operate **your own already-running Firefox** - read pages, see your tabs, click,
type, navigate - over raw [WebDriver BiDi](https://w3c.github.io/webdriver-bidi/).

You launch Firefox yourself (your normal profile, your logged-in sessions). This
server **attaches** to it and **never launches or closes Firefox**.

> Built in Python (the machine has no Node). MCP servers are launched by the
> client as a stdio subprocess, so the implementation language is invisible to
> Claude - the tools it exposes are identical either way.

---

## 0. Read this before using the debug flag

`--remote-debugging-port` starts Firefox's WebDriver Remote Agent, which calls
`RecommendedPreferences.applyPreferences()` and writes **~108 automation prefs
into whatever profile it attaches to**. They land on the *user* pref branch -
the one that persists to `prefs.js` - and are reverted only by the
`xpcom-shutdown` observer, i.e. a **clean exit**. Any force-kill, crash, or
power loss makes them permanent.

This is not theoretical. It happened to this profile: Safe Browsing off,
extension updates off, password saving off, a completely blank Firefox Home, and
Firefox Accounts / remote settings / telemetry repointed at unresolvable
`%(server)s` placeholder hosts. 101 prefs, silently.

**The fix is one line in the profile's `user.js`:**

```
user_pref("remote.prefs.recommended", false);
```

The shipped default is `true` (`greprefs.js`). Setting it false makes
`applyPreferences()` a no-op. **firefox-mcp still attaches fine** - those prefs
are test-harness conveniences, not a BiDi requirement.

`ensure-profile-guard.ps1` installs and verifies that line, and every launcher
here runs it first. **The guard lives in the profile, not in this repo**, so a
profile reset silently removes it - which is why it is checked on every launch
rather than assumed. If a profile already carries leaked prefs, strip them with
`depoison-profile.ps1` (Firefox must be fully closed).

---

## 1. Launch Firefox in debug mode

Firefox must be started with the remote-debugging flag so it exposes a BiDi
WebSocket at `ws://127.0.0.1:9222/session`.

**Quit Firefox completely first.** If Firefox is already running on the profile,
the flag just opens a tab in the existing instance and the remote agent does
**not** start.

Use the helper - it runs the profile guard before launching:

```bat
start-firefox-debug.bat
```

To launch manually, check the guard yourself first:

```bat
powershell -ExecutionPolicy Bypass -NoProfile -File ensure-profile-guard.ps1
"C:\Program Files\Mozilla Firefox\firefox.exe" --remote-debugging-port 9222
```

**Do not put `--remote-debugging-port` in your everyday Firefox shortcut.** An
earlier version of this README suggested exactly that; it is the worst version
of this problem, because it skips the guard check and leaves the remote agent
enabled on every single launch, including the ones where Firefox later dies
uncleanly. Launch through `start-firefox-debug.bat` when you actually want
Claude attached.

Note: `127.0.0.1` is required (not `localhost`) - Firefox's remote agent checks
the Host header. The server only ever connects to loopback.

---

## 1b. Two browsers: `main` and `sandbox`

One server, two Firefoxes, switched with the `switch_browser` tool:

| Target | Port | Profile | What it is |
|---|---|---|---|
| `main` | 9222 | Tyler's own | His tabs, logins, extensions. The default. |
| `sandbox` | 9223 | throwaway | Empty, signed in to nothing. |

Start the sandbox (it can run at the same time as his normal Firefox):

```bat
powershell -ExecutionPolicy Bypass -NoProfile -File start-firefox-sandbox.ps1
```

```bat
powershell -ExecutionPolicy Bypass -NoProfile -File start-firefox-sandbox.ps1 -Status
powershell -ExecutionPolicy Bypass -NoProfile -File start-firefox-sandbox.ps1 -Reset
```

Use `sandbox` for browsing, scraping, or testing that should not touch Tyler's
session; use `main` to see or act on what he actually has open.

**A separate profile cannot see his tabs, and that is not a limitation to work
around - it is the whole point.** A profile *is* the tabs, cookies, logins,
extensions and prefs, in one directory. There is no way to share the tabs but
split the settings; they are the same object. If a task needs his real session,
it needs `main`.

Implementation notes worth knowing:

- Each target holds its own BiDi client, session, console buffer, and remembered
  active tab, so switching does not disturb either browser.
- `--no-remote` is **required** for the second instance. Without it a second
  `firefox.exe` just hands its command line to the running one and exits - you
  get a new tab in Tyler's window and no sandbox.
- The sandbox profile lives at `%LOCALAPPDATA%\firefox-mcp\sandbox-profile`,
  outside this repo, because it is disposable state and would be a large
  accidental commit.
- The sandbox is guarded too. Its automation prefs would be survivable (it is
  throwaway), but they also switch Safe Browsing off, and this is the browser
  most likely to be pointed at pages nobody vouched for.
- Shutdown ends **every** target's session. A sandbox session left open holds
  that Firefox's single session slot and the next start fails with "Maximum
  number of active sessions".
- If the sandbox starts refusing sessions (`session.status` reports
  `ready:false, "Session already started"` with nothing connected to the port),
  a client died mid-`session.new` and Firefox is holding the session for a
  connection that no longer exists. Restarting the browser is not always enough,
  because an unclean close leaves crash-recovery state that can make the next
  `session.new` exceed the 30s command timeout and orphan a session all over
  again. `-Reset` wipes the profile and breaks the loop. (This is a side effect
  of guarding the sandbox: `browser.sessionstore.resume_from_crash` is no longer
  force-disabled, so crash recovery actually runs.) Always call `close_all()` in
  a `finally` when driving the tools from a script.

---

## 2. Install

```bat
cd C:\Users\Tyler\Claude\Projects\Work-In-Project\firefox-mcp
python -m venv .venv
.venv\Scripts\python -m pip install -e .
```

Run the tests (no browser needed):

```bat
.venv\Scripts\python -m unittest discover -s tests -t .
```

---

## 3. Register the server with Claude

### Claude Code (CLI)

```bat
claude mcp add firefox-mcp -- "C:\Users\Tyler\Claude\Projects\Work-In-Project\firefox-mcp\.venv\Scripts\firefox-mcp.exe"
```

### Claude Desktop

Add to `claude_desktop_config.json` (or via the Connectors / MCP settings UI):

```json
{
  "mcpServers": {
    "firefox-mcp": {
      "command": "C:\\Users\\Tyler\\Claude\\Projects\\Work-In-Project\\firefox-mcp\\.venv\\Scripts\\firefox-mcp.exe",
      "args": []
    }
  }
}
```

Restart the client after registering. A new MCP server can't be hot-loaded into
a session that's already running.

---

## 4. Tools

| Tool | Args | Behavior |
|---|---|---|
| `switch_browser` | `target?` | Choose `main` or `sandbox`; no arg lists both |
| `firefox_status` | - | Current target, Firefox version, active tab URL/title |
| `list_tabs` | - | Open tabs: index, title, URL; marks the active one |
| `select_tab` | `index` | Set the active tab (all page tools act on it) |
| `new_tab` | `url?` | Open a new tab, make it active |
| `navigate` | `url` | Navigate the active tab, wait for load |
| `back` / `forward` | - | History traversal |
| `snapshot` | - | Element-ref tree of the active tab (see below) |
| `read_page` | - | Readable text of the active tab (~20k chars) |
| `screenshot` | - | PNG of the active tab |
| `click` | `ref`, `confirmed?` | Click an element by ref |
| `type` | `ref`, `text`, `submit?` | Type into an element; Enter after if `submit` |
| `select_option` | `ref`, `value` | Set a `<select>` value |
| `press_key` | `key`, `confirmed?` | One key (Enter, Escape, Tab, arrows, ...) |
| `scroll` | `direction`, `amount?` | Scroll the active tab |
| `console_logs` | - | Recent console entries (ring buffer, last 100) |

### The snapshot / ref model

`snapshot` returns a compact tree; each interactive element gets a ref:

```
[e1] link "Inbox (3)"
[e2] button "Compose"
[e3] textbox "Search mail" value=""
heading "Today"
[e4] checkbox "Select all" checked=false
```

`click`, `type`, and `select_option` take those refs. Refs are resolved
server-side against a map stored in the page - there is **no coordinate
clicking**. After the page changes or navigates, old refs go stale; re-run
`snapshot`.

---

## 5. Safety model

This is a **guardrail, not a sandbox.**

- **Confirmation-gated actions.** `click` and `press_key` refuse - and tell Claude
  to ask you first - when the target is a submit button / form submission, or a
  link/button whose text or URL matches: send, submit, buy, pay, order, confirm,
  delete, unsubscribe, download, sign in / log in, checkout. After you say yes in
  chat, Claude re-calls with `confirmed=true`.
- **Never types passwords.** `type` refuses `input[type=password]` and
  credential fields (`autocomplete` = current-password / new-password / cc-*).
  Type those yourself.
- **Domain allow/deny.** `~/.config/firefox-mcp/config.json` (Windows:
  `C:\Users\Tyler\.config\firefox-mcp\config.json`). See `config.example.json`.
  - `blockedDomains`: never operate on tabs whose host matches (default has
    banking examples - edit them).
  - `allowedDomains`: if non-empty, operate **only** on those.
- **Loopback only.** Connects to `127.0.0.1` and nothing else.

---

## 6. Manual smoke test

1. Run `start-firefox-debug.bat` (Firefox fully quit first).
2. `firefox_status` -> should report Connected + version.
3. `list_tabs` -> `select_tab` a tab.
4. `snapshot` -> `read_page`.
5. `click` a plain link -> `snapshot` again (refs refreshed).
6. `type` into a search box (`submit=true`) -> `screenshot`.

Or run the scripted version (with Firefox in debug mode):

```bat
.venv\Scripts\python smoke.py
```

---

## Troubleshooting

- **"Maximum number of active sessions" / can't connect after a crash.** Firefox's
  BiDi remote agent allows only **one** session at a time, and a hard crash of the
  server can leave a stale one that a fresh connection can't clear. Fully quit
  Firefox and relaunch it with the flag. (Clean shutdowns end the session
  automatically, so this is rare.)

  The usual cause is a **stray server process** rather than Firefox itself: this
  server runs as a stdio subprocess of the Claude Code client, and those
  subprocesses can outlive the client, each still holding the one BiDi session.
  Recover with `restart-firefox-mcp.ps1`, run **after** fully quitting Claude Code
  and **before** relaunching it:

  ```bat
  powershell -ExecutionPolicy Bypass -File restart-firefox-mcp.ps1 -DryRun
  powershell -ExecutionPolicy Bypass -File restart-firefox-mcp.ps1
  ```

  It refuses to run while the client is up (`-Force` overrides), prints every
  process before killing it, and leaves an already-listening Firefox alone.
- **"relaunch Firefox with --remote-debugging-port".** Firefox wasn't started with
  the flag, or another instance was already running when you launched it. Fully
  quit Firefox, then use `start-firefox-debug.bat`.
- **"Active tab: (none)".** No tabs are open - open one.

## Out of scope

Launching/managing Firefox; multi-browser support; file upload/download;
cookie or credential access; network request modification.
