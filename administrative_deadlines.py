"""Deadline calculations kept independent from Streamlit widgets."""
from datetime import datetime, timezone, timedelta

DEFAULT_DUE_DAYS = 7


def parse_utc(value):
    if not value:
        return None
    if isinstance(value, datetime):
        dt = value
    else:
        text = str(value).strip().replace(" UTC", "")
        try:
            dt = datetime.fromisoformat(text.replace("Z", "+00:00"))
        except ValueError:
            return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(timezone.utc)


def deadline_state(claim, now=None):
    """Return on_time, near_due, overdue, finished or no_due_date."""
    now = now or datetime.now(timezone.utc)
    status = claim.get("status")
    resolved_at = parse_utc(claim.get("resolved_at"))
    if status in ("Résolue", "Acceptée", "Rejetée", "Clôturée"):
        return "Terminé"
    due = parse_utc(claim.get("due_at"))
    if not due:
        return "Sans échéance"
    if now > due:
        return "En retard"
    if due - now <= timedelta(days=2):
        return "Échéance proche"
    return "Dans les délais"


def treatment_duration(claim, now=None):
    created = parse_utc(claim.get("created_at"))
    if not created:
        return None
    if claim.get("status") in ("Résolue", "Acceptée", "Rejetée", "Clôturée"):
        end = parse_utc(claim.get("resolved_at")) or now or datetime.now(timezone.utc)
    else:
        end = now or datetime.now(timezone.utc)
    return max(0, int((end - created).total_seconds() // 86400))


def normalize_due_date(value, default_due_days=DEFAULT_DUE_DAYS):
    if not value:
        return None
    parsed = parse_utc(str(value).strip())
    if parsed is None:
        raise ValueError("Date limite invalide.")
    if parsed.date() < datetime.now(timezone.utc).date():
        raise ValueError("La date limite ne peut pas être antérieure à aujourd'hui.")
    return parsed.strftime("%Y-%m-%d %H:%M:%S UTC")
