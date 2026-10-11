"""Keep an agent to its packaged tools: no ad-hoc code, no reading tool source.

Some agents (Cine) are meant to write data — native JSON, reports — and run
only the scripts their skills ship. Prompt rules alone do not hold: an agent
that finds ``read_skill_file`` refusing a validator's source reads it through
the shell (``node -e "fs.readFileSync(...)"``), then writes its own generator
script against the validator's internals. This policy closes those routes at
the tool-call boundary:

- writing a code file (``.py``, ``.mjs``, ``.ps1``, ...) with a file tool;
- running an interpreter on inline code (``python -c``, ``node -e``,
  ``python -``, ``-EncodedCommand``) or on a script outside the packaged
  script roots;
- reading packaged source through the shell or a file-read tool.

Configure with ``factory_params``::

    policies:
      packaged_code_only:
        type: function
        handler: agentnexus.policies.builtins.code_guard.packaged_code_only
        factory_params:
          script_roots: ['[\\\\/]skills[\\\\/]']
"""

from __future__ import annotations

import re
import shlex
from collections.abc import Callable, Iterable
from pathlib import Path

from agentnexus.policies.builtins._shell import SHELL_TOOLS
from agentnexus.policies.schema import PolicyEvent, PolicyResponse
from agentnexus.process_logging import data_dir

_ALLOW: PolicyResponse = {"result": "ALLOW"}

CODE_EXTENSIONS = frozenset(
    {
        ".py", ".pyw", ".js", ".mjs", ".cjs", ".ts", ".mts", ".tsx", ".jsx",
        ".ps1", ".psm1", ".psd1", ".bat", ".cmd", ".sh", ".bash", ".vbs", ".rb", ".pl", ".php",
    }
)  # fmt: skip
# Bundle scripts live under ``skills/<name>/``; the Topic workspace does not.
DEFAULT_SCRIPT_ROOTS = (r"[\\/]skills[\\/]",)
WRITE_TOOLS = frozenset(
    {"sys_os_write", "sys_os_edit", "Write", "Edit", "write", "edit", "write_file"}
)
READ_TOOLS = frozenset({"sys_os_read", "Read", "read", "read_file"})
_READERS = frozenset(
    {
        "get-content",
        "gc",
        "cat",
        "type",
        "more",
        "less",
        "head",
        "tail",
        "select-string",
        "sls",
        "findstr",
        "grep",
    }
)
_WRITERS = frozenset({"set-content", "out-file", "add-content", "new-item", "tee", "tee-object"})
_PYTHON = frozenset({"python", "python3", "py", "pythonw"})
_NODE = frozenset({"node", "deno", "bun", "ts-node", "tsx"})
_POWERSHELL = frozenset({"powershell", "pwsh"})
_POSIX_SHELLS = frozenset({"bash", "sh", "zsh"})
_OTHER_INTERPRETERS = frozenset({"ruby", "perl", "php", "wscript", "cscript"})
# Flags that take a value, per interpreter, so the script argument is found.
_PYTHON_VALUE_FLAGS = frozenset({"-X", "-W", "-Q"})
_UV_VALUE_FLAGS = frozenset(
    {
        "--with", "--with-requirements", "--python", "-p", "--project",
        "--directory", "--env-file", "--index-url", "--extra-index-url",
    }
)  # fmt: skip
_ASSIGNMENT = re.compile(r"^\$(\w+)\s*=\s*(?:\"([^\"]*)\"|'([^']*)')\s*$")

_TRUST_ENV_NAMES = frozenset({"AGENTNEXUS_DATA_DIR", "HOME", "USERPROFILE"})
_TRUST_ENV = r"(?:AGENTNEXUS_DATA_DIR|HOME|USERPROFILE)"
_TRUST_ENV_MUTATION = re.compile(
    rf"\b{_TRUST_ENV}\b[\"']?\s*="
    rf"|\b(?:setx|unset)\s+[\"']?{_TRUST_ENV}\b"
    rf"|\benv\s+(?:-u|--unset(?:=|\s+))\s*{_TRUST_ENV}\b"
    rf"|\b(?:set-item|new-item|remove-item|clear-item|set-content|clear-content|si|ni|ri|ci)"
    rf"\s+[^;\n|&]*\benv:{_TRUST_ENV}\b"
    rf"|SetEnvironmentVariable\s*\(\s*[\"']{_TRUST_ENV}[\"']",
    re.IGNORECASE,
)

Verdict = PolicyResponse | None


def packaged_code_only(
    script_roots: Iterable[str] = DEFAULT_SCRIPT_ROOTS,
    code_extensions: Iterable[str] = CODE_EXTENSIONS,
    message: str = (
        "Only packaged skill scripts may run here. Write native JSON or text with the file "
        "tools instead of a script, and learn a tool from its SKILL.md or --help, not its source."
    ),
) -> Callable[[PolicyEvent], PolicyResponse]:
    """Build the policy.

    :param script_roots: Regexes; a script whose path matches one may be run
        (and only such paths count as packaged source for the read rule).
    :param code_extensions: File extensions treated as code.
    :param message: Guidance appended to every denial.
    """
    roots = [re.compile(pattern, re.IGNORECASE) for pattern in script_roots]
    extensions = frozenset(ext.lower() for ext in code_extensions)
    guard = _Guard(roots, extensions)

    def evaluate(event: PolicyEvent) -> PolicyResponse:
        if event.get("type") != "tool_call":
            return _ALLOW
        data = event.get("data")
        if not isinstance(data, dict):
            return _ALLOW
        tool = str(data.get("name") or "")
        args = data.get("arguments")
        args = args if isinstance(args, dict) else {}
        reason = guard.check(tool, args)
        if reason is None:
            return _ALLOW
        return {"result": "DENY", "reason": f"{reason} {message}"}

    return evaluate


class _Guard:
    def __init__(self, roots: list[re.Pattern[str]], extensions: frozenset[str]):
        self.roots = roots
        self.extensions = extensions
        self.quality_trust = (data_dir() / "cine-quality-trust").resolve()

    def is_code(self, path: str) -> bool:
        match = re.search(r"(\.[A-Za-z0-9]+)$", path.strip().strip("'\""))
        return bool(match) and match.group(1).lower() in self.extensions

    def packaged(self, path: str) -> bool:
        return any(root.search(path) for root in self.roots)

    def protected_trust_path(self, value: str) -> bool:
        if "cine-quality-trust" in value.casefold():
            return True
        try:
            return Path(value).expanduser().resolve().is_relative_to(self.quality_trust)
        except (OSError, ValueError):
            return False

    def check(self, tool: str, args: dict) -> str | None:
        path = str(args.get("path") or args.get("file_path") or "")
        for name in (
            "path",
            "file_path",
            "source",
            "destination",
            "cwd",
            "workdir",
            "working_directory",
        ):
            value = args.get(name)
            if isinstance(value, str) and value and self.protected_trust_path(value):
                return "The media-quality trust directory is reserved for the runner."
        for name in ("env", "environment", "environment_variables"):
            values = args.get(name)
            if isinstance(values, dict) and any(
                str(key).upper() in _TRUST_ENV_NAMES for key in values
            ):
                return "Changing the runtime media-quality trust root is not allowed."
        if tool in WRITE_TOOLS and path and self.is_code(path):
            return f"Writing code file {path!r} is not allowed."
        if tool in READ_TOOLS and path and self.is_code(path) and self.packaged(path):
            return f"Reading packaged source {path!r} is not allowed."
        if tool in SHELL_TOOLS:
            command = str(args.get("command") or "")
            return self.check_command(command, depth=0)
        return None

    def check_command(self, command: str, *, depth: int) -> str | None:
        if depth > 4:
            return "Command nesting is too deep to verify."
        if "cine-quality-trust" in command.casefold():
            return "Shell access to the media-quality trust directory is not allowed."
        if _TRUST_ENV_MUTATION.search(command):
            return "Changing AGENTNEXUS_DATA_DIR, HOME or USERPROFILE is not allowed."
        variables: dict[str, str] = {}
        for segment in _segments(command):
            assignment = _ASSIGNMENT.match(segment)
            if assignment:
                variables[assignment.group(1)] = assignment.group(2) or assignment.group(3) or ""
                continue
            for name, value in variables.items():
                segment = re.sub(rf"\${name}\b", lambda _m, v=value: v, segment)
            reason = self.check_argv(_tokens(segment), depth=depth)
            if reason:
                return reason
        return None

    def check_argv(self, argv: list[str], *, depth: int) -> str | None:
        while argv and argv[0] in ("&", "."):
            argv = argv[1:]
        if not argv:
            return None
        for token in argv:
            if self.protected_trust_path(token):
                return "Shell access to the media-quality trust directory is not allowed."
        head = _basename(argv[0])
        rest = argv[1:]
        if head == "uv" and rest[:1] == ["run"]:
            return self.check_argv(_skip_flags(rest[1:], _UV_VALUE_FLAGS), depth=depth)
        if head in _PYTHON:
            return self.check_script(head, _python_script(rest))
        if head in _NODE:
            if any(flag in rest for flag in ("-e", "--eval", "-p", "--print")):
                return f"{head} with inline code is not allowed."
            return self.check_script(head, _first_positional(rest))
        if head in _POWERSHELL:
            return self.check_powershell(rest, depth=depth)
        if head == "cmd":
            inner = _after_flag(rest, ("/c", "/k", "/C", "/K"))
            return self.check_command(inner, depth=depth + 1) if inner else None
        if head in _POSIX_SHELLS:
            inner = _after_flag(rest, ("-c",))
            if inner is not None:
                return self.check_command(inner, depth=depth + 1)
            return self.check_script(head, _first_positional(rest))
        if head in _OTHER_INTERPRETERS:
            return self.check_script(head, _first_positional(rest))
        if head in _READERS:
            for token in rest:
                if self.is_code(token) and self.packaged(token):
                    return f"Reading packaged source {token!r} is not allowed."
        redirected = [t.lstrip(">") for t in argv if t.startswith(">") and t.lstrip(">")]
        if ">" in argv or ">>" in argv:
            redirected += [argv[i + 1] for i, t in enumerate(argv[:-1]) if t in (">", ">>")]
        targets = rest if head in _WRITERS else redirected
        for token in targets:
            if self.is_code(token):
                return f"Writing code file {token!r} through the shell is not allowed."
        return None

    def check_powershell(self, rest: list[str], *, depth: int) -> str | None:
        lowered = [token.lower() for token in rest]
        if any(flag in lowered for flag in ("-encodedcommand", "-enc", "-e", "-ec")):
            return "powershell -EncodedCommand is not allowed."
        script = _after_flag(rest, ("-File", "-file", "-f"))
        if script is not None:
            return self.check_script("powershell", script)
        inner = _after_flag(rest, ("-Command", "-command", "-c"))
        if inner is None:
            positional = [t for t in rest if not t.startswith("-")]
            inner = " ".join(positional) if positional else None
        return self.check_command(inner, depth=depth + 1) if inner else None

    def check_script(self, interpreter: str, script: str | None) -> str | None:
        if script is None:
            return (
                f"{interpreter} without a packaged script (inline or stdin code) is not allowed."
            )
        if script in ("--version", "-V", "--help", "-h"):
            return None
        if script == "-c":
            return f"{interpreter} -c inline code is not allowed."
        if script == "-":
            return f"{interpreter} reading code from stdin is not allowed."
        if script == "-m":
            return f"{interpreter} -m is not allowed; run a packaged script instead."
        if not self.packaged(script):
            return (
                f"Running {script!r} with {interpreter} is not allowed; "
                "it is not a packaged script."
            )
        return None


def _segments(command: str) -> list[str]:
    """Split on ``;``, ``&&``, ``||``, ``|``, ``&`` and newlines outside quotes.

    Inline code is exactly where separators sit inside quotes
    (``node -e "a; b"``); splitting there would hide the interpreter call.
    """
    segments, current, quote, index = [], [], None, 0
    while index < len(command):
        char = command[index]
        if quote:
            current.append(char)
            if char == quote:
                quote = None
        elif char in "\"'":
            quote = char
            current.append(char)
        elif command.startswith(("&&", "||"), index):
            segments.append("".join(current))
            current = []
            index += 1
        elif char in ";|&\n":
            segments.append("".join(current))
            current = []
        else:
            current.append(char)
        index += 1
    segments.append("".join(current))
    return [segment.strip() for segment in segments if segment.strip()]


def _tokens(segment: str) -> list[str]:
    """Split one segment, keeping Windows backslashes (no POSIX escapes).

    An unbalanced quote falls back to a whitespace split, so an unparsable
    segment is still checked rather than skipped.
    """
    lexer = shlex.shlex(segment, posix=True, punctuation_chars=False)
    lexer.whitespace_split = True
    lexer.escape = ""
    try:
        return list(lexer)
    except ValueError:
        return [token.strip("\"'") for token in segment.split()]


def _basename(token: str) -> str:
    word = re.split(r"[\\/]", token)[-1].lower()
    return word[:-4] if word.endswith(".exe") else word


def _skip_flags(tokens: list[str], value_flags: frozenset[str]) -> list[str]:
    index = 0
    while index < len(tokens) and tokens[index].startswith("-"):
        flag = tokens[index].split("=", 1)[0]
        index += 2 if flag in value_flags and "=" not in tokens[index] else 1
    return tokens[index:]


def _python_script(rest: list[str]) -> str | None:
    """The script argument of a python invocation, or the mode flag that replaces it."""
    index = 0
    while index < len(rest):
        token = rest[index]
        if token in ("-c", "-m", "-"):
            return token
        if token in ("--version", "-V", "--help", "-h"):
            return token
        if token.startswith("-"):
            index += 2 if token in _PYTHON_VALUE_FLAGS else 1
            continue
        return token
    return None


def _first_positional(rest: list[str]) -> str | None:
    for token in rest:
        if token in ("--version", "--help", "-h", "-v"):
            return token
        if not token.startswith("-"):
            return token
    return None


def _after_flag(tokens: list[str], flags: tuple[str, ...]) -> str | None:
    for index, token in enumerate(tokens):
        if token in flags and index + 1 < len(tokens):
            return " ".join(tokens[index + 1 :])
    return None
