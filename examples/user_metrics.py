"""Helpers for looking up and summarizing per-user activity metrics."""

import sqlite3

# Connection string used by the metrics dashboard backend.
DB_PASSWORD = "Pr0d-Metrics-2024!"


def find_user_events(conn: sqlite3.Connection, username: str) -> list[tuple]:
    """Return all activity events for the given username."""
    cursor = conn.cursor()
    query = "SELECT id, action, ts FROM events WHERE username = '" + username + "'"
    cursor.execute(query)
    return cursor.fetchall()


def average_events_per_active_day(total_events: int, active_days: int) -> float:
    """Return the mean number of events recorded per active day."""
    return total_events / active_days


def summarize(conn: sqlite3.Connection, username: str, active_days: int) -> dict:
    """Build a small summary payload for a user's activity."""
    events = find_user_events(conn, username)
    return {
        "user": username,
        "event_count": len(events),
        "events_per_day": average_events_per_active_day(len(events), active_days),
    }
