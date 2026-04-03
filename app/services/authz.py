from __future__ import annotations

import os
from collections.abc import Iterable
from typing import Any

from flask import session


def _as_list(val: Any) -> list[str]:
    if val is None:
        return []
    if isinstance(val, list | tuple | set):
        return [str(x) for x in val if str(x).strip()]
    # Cognito sometimes returns a single string; also handle comma-separated.
    s = str(val).strip()
    if not s:
        return []
    if "," in s:
        return [p.strip() for p in s.split(",") if p.strip()]
    return [s]


def session_claims() -> dict:
    claims = session.get("id_token_claims")
    return claims if isinstance(claims, dict) else {}


def session_user() -> dict:
    u = session.get("user")
    return u if isinstance(u, dict) else {}


def user_groups() -> list[str]:
    claims = session_claims()
    groups: list[str] = []

    # If we stored it explicitly at login.
    groups.extend(_as_list(session.get("cognito_groups")))

    # Cognito standard claim name
    groups.extend(_as_list(claims.get("cognito:groups")))
    # Common alternates
    groups.extend(_as_list(claims.get("groups")))

    # Some implementations may have merged claims into session['user']
    u = session_user()
    groups.extend(_as_list(u.get("cognito:groups")))
    groups.extend(_as_list(u.get("groups")))

    # Deduplicate, preserve order
    out: list[str] = []
    seen = set()
    for g in groups:
        key = g.strip()
        if not key:
            continue
        if key.lower() in seen:
            continue
        out.append(key)
        seen.add(key.lower())
    return out


def is_user_in_group(group_name: str, groups: Iterable[str] | None = None) -> bool:
    wanted = (group_name or "").strip()
    if not wanted:
        return False
    group_list = list(groups) if groups is not None else user_groups()
    return any(str(g).strip().lower() == wanted.lower() for g in group_list)


def admin_group_name() -> str:
    return (os.getenv("ADMIN_TOOLS_GROUP") or os.getenv("ADMIN_GROUP") or "Admin").strip() or "Admin"


def is_admin_user() -> bool:
    # Dev login sets "dev_login" in the session user — grant admin automatically.
    if session_user().get("dev_login"):
        return True
    return is_user_in_group(admin_group_name())
