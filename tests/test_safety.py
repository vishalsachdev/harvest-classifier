"""The package must not be able to act. Enforced by parsing its own imports.

The previous version of this file matched substrings for `tmux`, `send-keys`
and similar. A review pointed out that `from subprocess import run`, `os.popen`,
`os.exec*`, `pty`, `importlib` and `socket` all passed it untouched. Naming the
bad thing is a weak check; naming the good things is a strong one.
"""
import ast
import pathlib
import re

ROOT = pathlib.Path(__file__).resolve().parents[1]
SRC = ROOT / "src"
PACKAGE = SRC / "harvest_classifier"

#: Every module this package may import. Anything else fails the build.
#: `urllib.request` and `ssl` are the network, and they exist for exactly one
#: endpoint. There is deliberately no process, terminal or dynamic-import
#: module here.
ALLOWED_IMPORTS = frozenset({
    "__future__", "argparse", "collections", "dataclasses", "datetime",
    "hashlib", "http", "http.client", "json", "os", "pathlib", "re",
    "ssl", "stat", "sys", "time", "tomllib", "typing", "urllib", "urllib.error",
    "urllib.request", "certifi",
})

#: Names that must not be imported out of an otherwise allowed module. `os` is
#: on the allow-list for paths and file modes, not for starting processes, and
#: a module-level allow-list alone would wave `from os import popen` through.
FORBIDDEN_NAMES = frozenset({
    "system", "popen", "fork", "forkpty", "posix_spawn", "posix_spawnp",
    "execl", "execle", "execlp", "execv", "execve", "execvp", "execvpe",
    "spawnl", "spawnv", "spawnve", "openpty",
})

#: Attribute calls that would let an allowed module do a forbidden thing.
FORBIDDEN_CALLS = re.compile(
    r"\bos\.(system|popen|exec[lv]?[ep]*|spawn\w*|fork|posix_spawn)\b"
    r"|\b__import__\s*\(")


def _root(name: str) -> str:
    return name.split(".")[0]


def forbidden_imports(path: pathlib.Path) -> list[str]:
    """Imports in this file that are not on the allow-list."""
    text = path.read_text()
    offenders: list[str] = []
    try:
        tree = ast.parse(text)
    except SyntaxError as exc:
        return [f"{path.name}: does not parse: {exc}"]

    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                if alias.name not in ALLOWED_IMPORTS and _root(alias.name) not in ALLOWED_IMPORTS:
                    offenders.append(f"{path.name}:{node.lineno}: import {alias.name}")
        elif isinstance(node, ast.ImportFrom):
            if node.level:            # relative import, inside this package
                continue
            module = node.module or ""
            if module not in ALLOWED_IMPORTS and _root(module) not in ALLOWED_IMPORTS:
                offenders.append(f"{path.name}:{node.lineno}: from {module} import ...")
            for alias in node.names:
                if alias.name in FORBIDDEN_NAMES:
                    offenders.append(
                        f"{path.name}:{node.lineno}: from {module} import {alias.name}")
        elif isinstance(node, ast.Call):
            func = node.func
            if isinstance(func, ast.Name) and func.id == "__import__":
                offenders.append(f"{path.name}:{node.lineno}: __import__(...)")

    for lineno, line in enumerate(text.splitlines(), 1):
        if FORBIDDEN_CALLS.search(line):
            offenders.append(f"{path.name}:{lineno}: forbidden call")
    return offenders


def _sources() -> list[pathlib.Path]:
    return list(PACKAGE.rglob("*.py"))


# --- the scanner itself -------------------------------------------------------

def test_the_scanner_catches_what_the_old_substring_check_missed(tmp_path):
    """Positive control. Each of these passed the previous implementation."""
    for line in ("from subprocess import run", "import subprocess",
                 "import pty", "import importlib", "from os import popen",
                 "os.system('x')", "os.popen('x')", "__import__('subprocess')"):
        module = tmp_path / "probe.py"
        module.write_text(f'"""doc."""\n{line}\n')
        assert forbidden_imports(module), f"scanner missed: {line}"


def test_the_scanner_accepts_an_ordinary_module(tmp_path):
    module = tmp_path / "fine.py"
    module.write_text('"""doc."""\nimport json\nfrom pathlib import Path\n')
    assert forbidden_imports(module) == []


def test_a_docstring_naming_a_forbidden_module_is_not_an_import(tmp_path):
    module = tmp_path / "prose.py"
    module.write_text('"""This module never calls subprocess or tmux."""\nimport json\n')
    assert forbidden_imports(module) == []


def test_src_is_not_empty_so_the_scan_is_not_vacuous():
    assert len(_sources()) >= 8


# --- the package -------------------------------------------------------------

def test_no_module_imports_anything_outside_the_allow_list():
    offenders = [line for path in _sources() for line in forbidden_imports(path)]
    assert offenders == [], (
        "the package must not be able to start a process, drive a terminal or "
        "import dynamically:\n" + "\n".join(offenders))


def test_the_allow_list_has_no_process_or_terminal_module():
    for banned in ("subprocess", "pty", "tty", "termios", "multiprocessing",
                   "importlib", "ctypes", "shutil", "socket"):
        assert banned not in ALLOWED_IMPORTS, banned


def test_the_package_does_not_import_subprocess():
    for path in _sources():
        assert "import subprocess" not in path.read_text(), path.name


def test_the_package_has_no_default_that_writes_outside_the_working_directory():
    from harvest_classifier.config import Config
    assert Config().log_dir.is_relative_to(pathlib.Path.cwd())
