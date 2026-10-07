from datetime import timedelta

from django import template

from ..dates import duration_text

register = template.Library()

# Full class names are spelled out so the Tailwind build can find them.
DAY_STYLES = {
    "present": "border-emerald-200 bg-emerald-50 text-emerald-900",
    "absent": "border-rose-200 bg-rose-50 text-rose-900",
    "leave": "border-sky-200 bg-sky-50 text-sky-900",
    "holiday": "border-violet-200 bg-violet-50 text-violet-900",
    "missing": "border-amber-300 bg-amber-50 text-amber-900",
    "off": "border-slate-200 bg-slate-100 text-slate-500",
    "today": "border-slate-200 bg-white text-slate-900",
    "future": "border-slate-200 bg-white text-slate-400",
    "none": "border-slate-200 bg-white text-slate-400",
    "before": "border-slate-100 bg-white text-slate-300",
}

DOT_STYLES = {
    "present": "bg-emerald-500",
    "absent": "bg-rose-500",
    "leave": "bg-sky-500",
    "holiday": "bg-violet-500",
    "missing": "bg-amber-500",
    "off": "bg-slate-400",
}

BADGE_STYLES = {
    # week states
    "met": "bg-emerald-100 text-emerald-800",
    "short": "bg-rose-100 text-rose-800",
    "progress": "bg-sky-100 text-sky-800",
    "future": "bg-slate-100 text-slate-500",
    "untracked": "bg-slate-100 text-slate-600",
    "before": "bg-slate-100 text-slate-400",
    # request statuses
    "pending": "bg-amber-100 text-amber-800",
    "approved": "bg-emerald-100 text-emerald-800",
    "rejected": "bg-rose-100 text-rose-800",
    "cancelled": "bg-slate-100 text-slate-600",
}

CELL_STYLES = {
    "met": "bg-emerald-50 text-emerald-900",
    "short": "bg-rose-50 text-rose-900",
    "progress": "bg-sky-50 text-sky-900",
}

MESSAGE_STYLES = {
    "success": "border-emerald-200 bg-emerald-50 text-emerald-900",
    "error": "border-rose-200 bg-rose-50 text-rose-900",
    "warning": "border-amber-200 bg-amber-50 text-amber-900",
}


@register.filter
def duration(value):
    """A timedelta as "7h 05m"."""
    if not isinstance(value, timedelta):
        return "—"
    return duration_text(value)


@register.filter
def signed_duration(value):
    """A timedelta as "+1h 10m" or "−2h 50m"."""
    if not isinstance(value, timedelta):
        return "—"
    if value < timedelta(0):
        return f"−{duration_text(-value)}"
    return f"+{duration_text(value)}"


@register.filter
def day_style(status):
    return DAY_STYLES.get(status, DAY_STYLES["future"])


@register.filter
def dot_style(status):
    return DOT_STYLES.get(status, "bg-slate-200")


@register.filter
def badge_style(key):
    return BADGE_STYLES.get(key, "bg-slate-100 text-slate-600")


@register.filter
def cell_style(state):
    return CELL_STYLES.get(state, "text-slate-400")


@register.filter
def message_style(tags):
    return MESSAGE_STYLES.get(tags, "border-slate-200 bg-white text-slate-800")


@register.simple_tag(takes_context=True)
def active(context, *prefixes):
    """Returns the active nav class when the current URL name starts with a prefix."""
    match = getattr(context.get("request"), "resolver_match", None)
    name = match.url_name if match else ""
    if name and any(name == prefix or name.startswith(prefix) for prefix in prefixes):
        return "nav-link-active"
    return ""
