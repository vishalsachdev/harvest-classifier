"""Resolve which agent a status record belongs to.

Producers name a status file from an environment variable when one is set, and
fall back to a pane id or a session id otherwise. Those ids match nothing in an
allow-list, so sessions an operator has deliberately named get refused for a
reason that has nothing to do with intent.

Two rules:

1. An **opaque** name is resolved to the basename of the session's working
   directory, which the record always carries. An explicit name is never
   overridden, because it was set deliberately.
2. A session whose working directory sits under a configured denied path is
   refused whatever it reports. A name cannot tell you that a session is
   sitting in a directory full of other people's data; a path can.
"""
from __future__ import annotations

import pathlib
import re
from typing import Any

#: Anything that is not a plain, human-chosen name: pane ids, UUID session ids,
#: shell job ids, and empty values.
_REAL_NAME = re.compile(r"^[A-Za-z][A-Za-z0-9._-]{1,63}$")
_SESSION_ID = re.compile(r"^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-"
                         r"[0-9a-f]{4}-[0-9a-f]{12}$", re.IGNORECASE)


def is_opaque_name(name: str | None) -> bool:
    if not isinstance(name, str) or not name.strip():
        return True
    if _SESSION_ID.match(name):
        return True
    return not _REAL_NAME.match(name)


def resolve_agent(rec: dict[str, Any]) -> str | None:
    name = rec.get("agent")
    if not is_opaque_name(name):
        return name
    cwd = rec.get("cwd")
    if not isinstance(cwd, str) or not cwd.strip():
        return name
    return pathlib.PurePath(cwd.rstrip("/")).name or name


def under_denied_path(cwd: str | None, denied: tuple[str, ...]) -> bool:
    """True if the session works under a denied directory.

    With no denied paths configured there is no rule to apply, so a missing
    `cwd` is tolerated. Once a rule exists, a missing `cwd` cannot be checked
    against it and is refused.
    """
    if not denied:
        return False
    if not isinstance(cwd, str) or not cwd.strip():
        return True
    parts = {part.casefold() for part in pathlib.PurePath(cwd).parts}
    return bool(parts & {d.strip("/").casefold() for d in denied})
