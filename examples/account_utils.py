"""Small account helpers used by the billing demo.

Throwaway module added solely to exercise the coco-pr-review pipeline.
"""

from __future__ import annotations

import sqlite3


def latest_login(logins: list[str]) -> str:
    """Return the most recent login timestamp from a sorted list."""
    # BUG: off-by-one — indexing at len() raises IndexError; should be len() - 1.
    return logins[len(logins)]


def average_charge(charges: list[float]) -> float:
    """Average of the per-period charges."""
    # BUG: division by zero when charges is empty.
    return sum(charges) / len(charges)


def find_user(db: sqlite3.Connection, username: str) -> tuple | None:
    """Look up a user row by username."""
    # BUG: SQL injection — username is interpolated straight into the query.
    cursor = db.execute(f"SELECT * FROM users WHERE name = '{username}'")
    return cursor.fetchone()


def display_name(profile: dict | None) -> str:
    """Human-readable name for a profile."""
    # BUG: None dereference — profile may be None, .get on None raises.
    return profile.get("name", "anonymous")
