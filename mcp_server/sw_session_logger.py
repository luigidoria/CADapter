"""
sw_session_logger.py
MCP proxy that intercepts calls to mcp_solidworks.py and records sessions as JSONL
for building the fine-tuning dataset.

Usage through .mcp.json:
  "command": ".venv\\Scripts\\python.exe",
  "args": ["mcp_server/sw_session_logger.py"],
  "env": {
    "SESSION_DIR": "sessions",
    "WRAPPED_SERVER": "mcp_server/mcp_solidworks.py"
  }

It adds two extra tools to the server:
  sw_start_session(description, level) -- start a session with context
  sw_end_session(success, notes)       -- finish the session
"""

import json
import os
import sys
import subprocess
import threading
import datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
import config_env  # noqa: E402
config_env.load_module("mcp_server")

# ── Configuration ─────────────────────────────────────────────────────────────

SESSION_DIR = Path(os.environ.get("SESSION_DIR", "sessions"))
WRAPPED_SERVER = os.environ.get(
    "WRAPPED_SERVER",
    str(Path(__file__).parent / "mcp_solidworks.py"),
)

# ── Session file ──────────────────────────────────────────────────────────────
# Each sw_start_session / sw_end_session pair writes to its own file, so several
# sessions can be recorded in a single run of the server. Tool calls are only logged
# while a session is active (which keeps the noise out).

SESSION_DIR.mkdir(parents=True, exist_ok=True)

_session_file = None        # allocated in sw_start_session
_session_id = None
_session_active = False
_turn = 0
_log_lock = threading.Lock()
_context: dict = {}


def _next_session_num() -> int:
    nums = []
    for p in SESSION_DIR.glob("session_*.jsonl"):
        try:
            nums.append(int(p.stem.split("_", 1)[1]))
        except (IndexError, ValueError):
            pass
    return (max(nums) + 1) if nums else 1


def _log(event: dict) -> None:
    if _session_file is None:
        return
    with _log_lock:
        with open(_session_file, "a", encoding="utf-8") as fh:
            fh.write(json.dumps(event, ensure_ascii=False) + "\n")


# ── Subprocess ────────────────────────────────────────────────────────────────

_proc = subprocess.Popen(
    [sys.executable, WRAPPED_SERVER],
    stdin=subprocess.PIPE,
    stdout=subprocess.PIPE,
    stderr=sys.stderr,
    env={**os.environ, "PYTHONUNBUFFERED": "1"},
)

_pending: dict = {}          # msg_id -> {name, args, turn}
_tools_list_ids: set = set() # IDs of tools/list requests, to patch the response
_client_lock = threading.Lock()


# ── Extra tools (answered locally, not forwarded) ─────────────────────────────

_EXTRA_TOOLS = [
    {
        "name": "sw_start_session",
        "description": (
            "Start a recording session for the fine-tuning dataset. "
            "Call it BEFORE starting to model, describing what will be created."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "description": {
                    "type": "string",
                    "description": "What will be modelled in this session (e.g. '50x30x20mm block with a through hole')",
                },
                "level": {
                    "type": "string",
                    "enum": ["1", "2", "3"],
                    "description": "Complexity: 1=primitive, 2=multi-feature, 3=assembly",
                    "default": "1",
                },
            },
            "required": ["description"],
        },
    },
    {
        "name": "sw_end_session",
        "description": (
            "Finish the current recording session. "
            "Call it AFTER completing (or aborting) the modelling."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "success": {
                    "type": "boolean",
                    "description": "Did the session complete successfully?",
                },
                "notes": {
                    "type": "string",
                    "description": "Optional notes",
                },
            },
            "required": ["success"],
        },
    },
]

_EXTRA_TOOL_NAMES = {t["name"] for t in _EXTRA_TOOLS}


def _handle_extra_tool(name: str, arguments: dict, msg_id) -> dict:
    global _context, _session_file, _session_id, _session_active, _turn
    ts = datetime.datetime.now().isoformat()

    if name == "sw_start_session":
        num = _next_session_num()
        _session_file = SESSION_DIR / f"session_{num:03d}.jsonl"
        _session_id = f"{num:03d}"
        _session_active = True
        _turn = 0
        _context = {
            "description": arguments.get("description", ""),
            "level": arguments.get("level", "1"),
        }
        _log({
            "session_id": _session_id,
            "turn": 0,
            "role": "session_start",
            "context": _context,
            "timestamp": ts,
        })
        text = f"Session {_session_id} started. Recording to {_session_file.name}."

    elif name == "sw_end_session":
        success = arguments.get("success", True)
        notes = arguments.get("notes", "")
        _log({
            "session_id": _session_id,
            "turn": 0,
            "role": "session_end",
            "success": success,
            "notes": notes,
            "timestamp": ts,
        })
        status = "completed" if success else "aborted"
        text = f"Session {_session_id} {status}." + (f" {notes}" if notes else "")
        _session_active = False

    else:
        text = f"Unknown tool: {name}"

    return {
        "jsonrpc": "2.0",
        "id": msg_id,
        "result": {
            "content": [{"type": "text", "text": text}],
            "isError": False,
        },
    }


# ── Patch tools/list to inject the extra tools ────────────────────────────────

def _patch_tools_list(msg: dict) -> dict:
    result = msg.get("result", {})
    tools = list(result.get("tools", []))
    tools.extend(_EXTRA_TOOLS)
    result["tools"] = tools
    msg["result"] = result
    return msg


# ── Sending to the client (Claude Code) ───────────────────────────────────────

def _send(msg: dict) -> None:
    line = json.dumps(msg, ensure_ascii=False) + "\n"
    with _client_lock:
        sys.stdout.write(line)
        sys.stdout.flush()


def _send_raw(line: str) -> None:
    with _client_lock:
        sys.stdout.write(line + "\n")
        sys.stdout.flush()


# ── Thread: stdin -> subprocess ───────────────────────────────────────────────

def _forward_to_server() -> None:
    global _turn
    for raw in sys.stdin.buffer:
        line = raw.decode("utf-8", errors="replace").rstrip("\n")
        if not line:
            continue

        try:
            msg = json.loads(line)
        except json.JSONDecodeError:
            _proc.stdin.write((line + "\n").encode())
            _proc.stdin.flush()
            continue

        method = msg.get("method", "")
        msg_id = msg.get("id")

        if method == "tools/list":
            _tools_list_ids.add(msg_id)

        if method == "tools/call":
            params = msg.get("params", {})
            name = params.get("name", "")
            args = params.get("arguments", {})

            # Extra tools: answered locally, not forwarded
            if name in _EXTRA_TOOL_NAMES:
                _send(_handle_extra_tool(name, args, msg_id))
                continue

            # Log the call to the real server only while a session is active
            if _session_active:
                _turn += 1
                _pending[msg_id] = {"name": name, "args": args, "turn": _turn}
                _log({
                    "session_id": _session_id,
                    "turn": _turn,
                    "role": "tool_call",
                    "tool_call": {"name": name, "arguments": args},
                    "timestamp": datetime.datetime.now().isoformat(),
                })

        _proc.stdin.write((line + "\n").encode())
        _proc.stdin.flush()

    _proc.stdin.close()


# ── Thread: subprocess -> stdout ──────────────────────────────────────────────

def _forward_from_server() -> None:
    for raw in _proc.stdout:
        line = raw.decode("utf-8", errors="replace").rstrip("\n")
        if not line:
            continue

        try:
            msg = json.loads(line)
        except json.JSONDecodeError:
            _send_raw(line)
            continue

        msg_id = msg.get("id")

        # Inject the extra tools into the tools/list response
        if msg_id in _tools_list_ids:
            _tools_list_ids.discard(msg_id)
            if "result" in msg and "tools" in msg.get("result", {}):
                msg = _patch_tools_list(msg)

        # Log the tool call's result
        if msg_id in _pending:
            pending = _pending.pop(msg_id)
            result = msg.get("result", {})
            content = result.get("content", [])
            result_text = " ".join(
                c.get("text", "") for c in content if isinstance(c, dict)
            )
            _log({
                "session_id": _session_id,
                "turn": pending["turn"],
                "role": "tool_result",
                "tool_call": {
                    "name": pending["name"],
                    "arguments": pending["args"],
                },
                "tool_result": result_text,
                "is_error": result.get("isError", False),
                "timestamp": datetime.datetime.now().isoformat(),
            })

        _send(msg)


# ── Main ──────────────────────────────────────────────────────────────────────

if __name__ == "__main__":
    t_in = threading.Thread(target=_forward_to_server, daemon=True)
    t_out = threading.Thread(target=_forward_from_server, daemon=True)
    t_in.start()
    t_out.start()
    t_in.join()
    t_out.join()
    _proc.wait()
