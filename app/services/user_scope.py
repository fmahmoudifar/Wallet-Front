from __future__ import annotations


def filter_records_by_user(records, user_id: str, user_key: str = 'userId'):
    """Filter API-returned records to the logged-in user.

    This is a defense-in-depth measure for cases where the backend might return
    mixed-user data. We only keep dict records where record[user_key] matches.
    """
    uid = (user_id or '').strip()
    if not uid:
        return []

    out = []
    for r in (records or []):
        try:
            if isinstance(r, dict) and str(r.get(user_key, '')).strip() == uid:
                out.append(r)
        except Exception:
            continue
    return out
