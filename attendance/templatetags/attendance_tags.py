from datetime import timedelta

from django import template
from django.utils.safestring import mark_safe

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


# Line icons for the menu and breadcrumbs, drawn on a 24 by 24 grid.
ICONS = {
    "home": '<path d="M4 11.5 12 5l8 6.5V19a1 1 0 0 1-1 1h-4.5v-5.5h-5V20H5a1 1 0 0 1-1-1v-7.5Z"/>',
    "chevron": '<path d="m9.5 6 6 6-6 6"/>',
    "clock": '<circle cx="12" cy="12" r="8.5"/><path d="M12 7.5V12l3 2"/>',
    "calendar": '<rect x="4" y="5.5" width="16" height="14" rx="2"/><path d="M4 10h16M8.5 3.5v4M15.5 3.5v4"/>',
    "inbox": '<path d="M4 13l2.5-7h11L20 13v5H4v-5Zm0 0h5a3 3 0 0 0 6 0h5"/>',
    "pulse": '<path d="M3.5 12h4l2.5-6 4 12 2.5-6h4"/>',
    "bars": '<path d="M5 19v-9M12 19V5M19 19v-6"/>',
    "people": (
        '<circle cx="9" cy="8.5" r="3"/><path d="M3.5 19c.6-3 2.8-4.5 5.5-4.5s4.9 1.5 5.5 4.5'
        'M16 5.8a3 3 0 0 1 0 5.4M17.5 14.8c1.6.6 2.7 1.9 3 4.2"/>'
    ),
    "grid": '<rect x="4" y="5" width="16" height="14" rx="2"/><path d="M4 10h16M4 14.5h16M9.5 10v9M14.5 10v9"/>',
    "star": (
        '<path d="m12 4 2.3 4.9 5.2.7-3.8 3.7.9 5.3L12 16.1l-4.6 2.5.9-5.3'
        'L4.5 9.6l5.2-.7L12 4Z"/>'
    ),
    "briefcase": (
        '<rect x="4" y="8" width="16" height="11" rx="2"/>'
        '<path d="M9 8V6.5A1.5 1.5 0 0 1 10.5 5h3A1.5 1.5 0 0 1 15 6.5V8"/>'
    ),
    "mail": '<rect x="3.5" y="5.5" width="17" height="13" rx="2"/><path d="m4.5 7.5 7.5 5.5 7.5-5.5"/>',
    "key": '<circle cx="8" cy="15.5" r="3.5"/><path d="m10.5 13 8-8M15.5 8l2.5 2.5"/>',
    "leave": '<path d="M14 5h4a1 1 0 0 1 1 1v12a1 1 0 0 1-1 1h-4M10 8l-4 4 4 4M6 12h9"/>',
    "menu": '<path d="M4 7h16M4 12h16M4 17h16"/>',
    "close": '<path d="m6 6 12 12M18 6 6 18"/>',
}


@register.simple_tag
def icon(name, size="size-5"):
    """An icon as inline SVG. `size` is a Tailwind size class."""
    return mark_safe(
        f'<svg class="{size} shrink-0" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.8" '
        f'stroke-linecap="round" stroke-linejoin="round" aria-hidden="true">{ICONS[name]}</svg>'
    )
