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

## 1. Launch Firefox in debug mode

Firefox must be started with the remote-debugging flag so it exposes a BiDi
WebSocket at `ws://127.0.0.1:9222/session`.

**Quit Firefox completely first.** If Firefox is already running on the profile,
the flag just opens a tab in the existing instance and the remote agent does
**not** start.

Then either run the helper:

```bat
start-firefox-debug.bat
```

or launch manually:

```bat
"C:\Program Files\Mozilla Firefox\firefox.exe" --remote-debugging-port 9222
```

**To make it permanent**, edit your Firefox shortcut: right-click -> Properties ->
append ` --remote-debugging-port 9222` to the end of the **Target** field. Every
launch from that shortcut then enables the remote agent.

Note: `127.0.0.1` is required (not `localhost`) - Firefox's remote agent checks
the Host header. The server only ever connects to loopback.

---

## 2. Install

```bat
cd C:\Users\Tyler\Claude\firefox-mcp
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
claude mcp add firefox-mcp -- "C:\Users\Tyler\Claude\firefox-mcp\.venv\Scripts\firefox-mcp.exe"
```

### Claude Desktop

Add to `claude_desktop_config.json` (or via the Connectors / MCP settings UI):

```json
{
  "mcpServers": {
    "firefox-mcp": {
      "command": "C:\\Users\\Tyler\\Claude\\firefox-mcp\\.venv\\Scripts\\firefox-mcp.exe"
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
| `browser_status` | - | Connection state, Firefox version, active tab URL/title |
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
2. `browser_status` -> should report Connected + version.
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
- **"relaunch Firefox with --remote-debugging-port".** Firefox wasn't started with
  the flag, or another instance was already running when you launched it. Fully
  quit Firefox, then use `start-firefox-debug.bat`.
- **"Active tab: (none)".** No tabs are open - open one.

## Out of scope

Launching/managing Firefox; multi-browser support; file upload/download;
cookie or credential access; network request modification.
