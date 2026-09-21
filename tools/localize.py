"""Language-dependent formatting primitives.

Down here in ``tools/`` because the agents, the monitoring rules and the
briefing composer all need the same month names, and every other home would
have made one layer import another that imports it back.

The rule these primitives exist to enforce: *a sentence in Arabic contains no
English*. It is easy to translate the sentence templates and then leak a month
name, a weekday or a metric label through a stdlib call — which is exactly what
happened here: the briefings read as fluent Arabic right up to
``أُنشئ في 09 Aug 2026``. One English word is all it takes for the text to read
as machine output rather than as a product.

The one deliberate exception is digits, which stay Western in both languages;
see :func:`format_timestamp`.
"""

from __future__ import annotations

from datetime import datetime

# Month names spelled out rather than taken from ``strftime('%b')``.
#
# ``strftime`` renders month and day names through the C locale, which is
# process-global: switching it to serve one Arabic briefing changes what every
# other thread in the API formats at the same moment. A table is both safe under
# concurrency and the only way the Arabic name is guaranteed to exist on a
# machine with no Arabic locale installed — which is every default container
# image this deploys to.
MONTHS = {
    "en": ("Jan", "Feb", "Mar", "Apr", "May", "Jun",
           "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"),
    "ar": ("يناير", "فبراير", "مارس", "أبريل", "مايو", "يونيو",
           "يوليو", "أغسطس", "سبتمبر", "أكتوبر", "نوفمبر", "ديسمبر"),
}

# Arabic month names are not abbreviated in ordinary business writing, so the
# long form is the same table; only English distinguishes them.
MONTHS_LONG = {
    "en": ("January", "February", "March", "April", "May", "June",
           "July", "August", "September", "October", "November", "December"),
    "ar": MONTHS["ar"],
}


def format_timestamp(moment: datetime, language: str = "en") -> str:
    """A timestamp with the month name in the reader's language.

    Digits stay Western in both languages, for the same reason
    :func:`monitoring.briefing.format_amount` keeps them: Arabic business
    software, invoices and banking apps across Egypt and the Gulf use them, and
    Arabic-Indic numerals read as a translation artefact rather than as
    localisation.
    """
    months = MONTHS.get(language) or MONTHS["en"]
    return f"{moment.day:02d} {months[moment.month - 1]} {moment.year}, {moment:%H:%M} UTC"


def format_month(moment: datetime, language: str = "en") -> str:
    """A month-and-year label, e.g. ``April 2025`` / ``أبريل 2025``.

    Spelled out rather than abbreviated: this is a period label a reader
    compares against another period label ("April 2025" against "May 2025"), not
    a timestamp, and it is read on its own in headings and chart axes.
    """
    months = MONTHS_LONG.get(language) or MONTHS_LONG["en"]
    return f"{months[moment.month - 1]} {moment.year}"
