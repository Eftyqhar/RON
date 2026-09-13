"""Bangladesh Standard Time, and today's date in three calendars.

    python clock.py                 # every answer at once
    python clock.py --check         # conversion self-test against known dates

A leaf module, like `history.py` and `weather.py`: standard library only, imports
nothing from RON, so there is no cycle with `bus`. It inherits the same two rules:

1. **Never raises into the caller.** A bad environment variable or an impossible
   date returns a plain-spoken answer, never an exception. The clock must not be
   able to take RON down.
2. **Nothing at import time.** The timezone is read lazily, so `RON_TZ_OFFSET` can
   be set after the module is imported -- which is how `ui_server.py` passes its
   flags through.

Three calendars are supported:

* **English** (Gregorian) -- the default, and exact.
* **Bangla** (Bangabda) -- the *revised* Bangladeshi calendar as fixed by the
  Bangla Academy in 2019, in which Pohela Boishakh is always 14 April and Falgun
  gains a day in a Gregorian leap year. Exact. Note that the West Bengal
  calendar differs; this one follows Bangladesh.
* **Arabic** (Hijri) -- the *tabular* Islamic calendar, computed arithmetically.
  This is an approximation by nature: the date observed in Bangladesh is fixed by
  moon sighting through the Islamic Foundation, and can differ from the tabular
  reckoning by a day either way. `date_answer("arabic")` says so out loud rather
  than presenting the figure as settled.

Why a fixed UTC+6 offset rather than `zoneinfo.ZoneInfo("Asia/Dhaka")`: on Windows
`zoneinfo` needs the separate `tzdata` package, and a missing database raises at
lookup time. Bangladesh has observed no daylight saving since 2009, so a constant
offset is both exactly correct and impossible to break.
"""

import calendar
import datetime
import os
import sys

TZ_OFFSET_HOURS = 6.0
TZ_NAME = "Bangladesh Standard Time"
TZ_SHORT = "BST"

# Pohela Boishakh in the revised Bangladeshi calendar: always 14 April.
_BN_NEW_YEAR = (4, 14)
# Bangla year + this = the Gregorian year the Bangla year *begins* in.
_BN_EPOCH_OFFSET = 593

SYSTEMS = ("english", "bangla", "arabic")


def tz():
    """The timezone RON reports in. `RON_TZ_OFFSET` is in hours, e.g. `5.5`."""
    raw = (os.environ.get("RON_TZ_OFFSET") or "").strip()
    hours = TZ_OFFSET_HOURS
    if raw:
        try:
            hours = float(raw)
        except ValueError:
            hours = TZ_OFFSET_HOURS      # unparseable: fall back, do not raise
    hours = max(-14.0, min(14.0, hours))  # the real range of world offsets
    return datetime.timezone(datetime.timedelta(hours=hours))


def tz_name():
    """The spoken name of the zone. Overridden alongside `RON_TZ_OFFSET`."""
    if (os.environ.get("RON_TZ_OFFSET") or "").strip():
        return (os.environ.get("RON_TZ_NAME") or "").strip() or "local time"
    return (os.environ.get("RON_TZ_NAME") or "").strip() or TZ_NAME


def now():
    """The current moment, as an aware datetime in RON's zone."""
    return datetime.datetime.now(tz())


# -- the Bangla calendar -----------------------------------------------------
# Month lengths under the 2019 Bangla Academy revision. The first six months
# have 31 days, the next five 30, and Falgun 29 -- 30 in a leap year, which is
# what keeps Pohela Boishakh pinned to 14 April for good.

_BN_MONTHS = (
    ("Boishakh", "বৈশাখ"), ("Joishtho", "জ্যৈষ্ঠ"), ("Ashar", "আষাঢ়"),
    ("Srabon", "শ্রাবণ"), ("Bhadro", "ভাদ্র"), ("Ashwin", "আশ্বিন"),
    ("Kartik", "কার্তিক"), ("Ogrohayon", "অগ্রহায়ণ"), ("Poush", "পৌষ"),
    ("Magh", "মাঘ"), ("Falgun", "ফাল্গুন"), ("Chaitro", "চৈত্র"),
)
_BN_MONTH_DAYS = (31, 31, 31, 31, 31, 31, 30, 30, 30, 30, 29, 30)
_BN_FALGUN = 10                                    # index of the leap month

# Python's weekday(): Monday is 0.
_BN_WEEKDAYS = (("Sombar", "সোমবার"), ("Mongolbar", "মঙ্গলবার"),
                ("Budhbar", "বুধবার"), ("Brihospotibar", "বৃহস্পতিবার"),
                ("Shukrobar", "শুক্রবার"), ("Shonibar", "শনিবার"),
                ("Robibar", "রবিবার"))

_BN_DIGITS = str.maketrans("0123456789", "০১২৩৪৫৬৭৮৯")


def _bn_month_lengths(bangla_year):
    """This Bangla year's month lengths, with Falgun adjusted for the leap rule.

    Falgun falls in February of the *following* Gregorian year, which is the year
    whose leap day the extra Falgun day exists to absorb.
    """
    lengths = list(_BN_MONTH_DAYS)
    if calendar.isleap(bangla_year + _BN_EPOCH_OFFSET + 1):
        lengths[_BN_FALGUN] += 1
    return lengths


def to_bangla(date):
    """Gregorian date -> the Bangla date, as a dict.

    Returns `{"year", "month", "day", "month_name", "month_bn", "weekday",
    "weekday_bn", "day_of_year", "leap"}`.
    """
    year = date.year
    if (date.month, date.day) >= _BN_NEW_YEAR:
        bangla_year = year - _BN_EPOCH_OFFSET
        start = datetime.date(year, *_BN_NEW_YEAR)
    else:
        bangla_year = year - _BN_EPOCH_OFFSET - 1
        start = datetime.date(year - 1, *_BN_NEW_YEAR)

    index = (datetime.date(date.year, date.month, date.day) - start).days
    lengths = _bn_month_lengths(bangla_year)

    month = 0
    remaining = index
    while month < 11 and remaining >= lengths[month]:
        remaining -= lengths[month]
        month += 1

    return {
        "year": bangla_year,
        "month": month + 1,
        "day": remaining + 1,
        "month_name": _BN_MONTHS[month][0],
        "month_bn": _BN_MONTHS[month][1],
        "weekday": _BN_WEEKDAYS[date.weekday()][0],
        "weekday_bn": _BN_WEEKDAYS[date.weekday()][1],
        "day_of_year": index + 1,
        "leap": lengths[_BN_FALGUN] == 30,
    }


def bangla_numerals(text):
    """'1433' -> '১৪৩৩'. For display; never for anything SAPI has to read."""
    return str(text).translate(_BN_DIGITS)


# -- the Hijri calendar ------------------------------------------------------
# The tabular ("Kuwaiti") civil Islamic calendar, via Julian Day Number. Widely
# used for arithmetic conversion and correct to within a day of the sighted
# date -- see the note in the module docstring.

_HIJRI_MONTHS = (
    "Muharram", "Safar", "Rabi al-Awwal", "Rabi al-Thani",
    "Jumada al-Awwal", "Jumada al-Thani", "Rajab", "Shaban",
    "Ramadan", "Shawwal", "Dhu al-Qadah", "Dhu al-Hijjah",
)


def _jdn(date):
    """Gregorian date -> Julian Day Number. Integer arithmetic, no floats."""
    a = (14 - date.month) // 12
    y = date.year + 4800 - a
    m = date.month + 12 * a - 3
    return (date.day + (153 * m + 2) // 5 + 365 * y
            + y // 4 - y // 100 + y // 400 - 32045)


def to_hijri(date):
    """Gregorian date -> the tabular Hijri date, as a dict.

    Returns `{"year", "month", "day", "month_name"}`.
    """
    l = _jdn(date) - 1948440 + 10632
    n = (l - 1) // 10631
    l = l - 10631 * n + 354
    j = (((10985 - l) // 5316) * ((50 * l) // 17719)
         + (l // 5670) * ((43 * l) // 15238))
    l = (l - ((30 - j) // 15) * ((17719 * j) // 50)
         - (j // 16) * ((15238 * j) // 43) + 29)
    month = (24 * l) // 709
    day = l - (709 * month) // 24
    year = 30 * n + j - 30
    return {
        "year": year,
        "month": month,
        "day": day,
        "month_name": _HIJRI_MONTHS[min(max(month, 1), 12) - 1],
    }


# -- phrasing ----------------------------------------------------------------
# Everything below is written for SAPI, which reads text literally: "4:32 p.m."
# is read correctly, "16:32" is read as two numbers.

def _ordinal(n):
    """1 -> '1st'. Handles the teens, which are all 'th'."""
    n = int(n)
    if 11 <= n % 100 <= 13:
        return f"{n}th"
    return f"{n}{('th', 'st', 'nd', 'rd')[n % 10] if n % 10 < 4 else 'th'}"


def time_text(moment=None):
    """'4:32 p.m.', or 'twelve noon' / 'midnight' on the hour."""
    moment = moment or now()
    if moment.minute == 0 and moment.hour in (0, 12):
        return "twelve noon" if moment.hour == 12 else "midnight"
    return (f"{moment.hour % 12 or 12}:{moment.minute:02d} "
            f"{'a.m.' if moment.hour < 12 else 'p.m.'}")


def english_date_text(moment=None):
    """'Monday, the 24th of August 2026'."""
    moment = moment or now()
    return (f"{moment.strftime('%A')}, the {_ordinal(moment.day)} of "
            f"{moment.strftime('%B')} {moment.year}")


def bangla_date_text(moment=None):
    """'the 9th of Bhadro, 1433'."""
    bn = to_bangla(moment or now())
    return f"the {_ordinal(bn['day'])} of {bn['month_name']}, {bn['year']}"


def hijri_date_text(moment=None):
    """'the 10th of Rabi al-Awwal, 1448'."""
    hj = to_hijri(moment or now())
    return f"the {_ordinal(hj['day'])} of {hj['month_name']}, {hj['year']}"


_HIJRI_CAVEAT = ("That is the tabular reckoning, Sir -- the sighted date may "
                 "differ by a day.")


def _date_clause(system, moment):
    """One calendar's date, phrased for the middle of a sentence."""
    if system == "bangla":
        return f"In the Bangla calendar it is {bangla_date_text(moment)}"
    if system == "arabic":
        return f"In the Hijri calendar it is {hijri_date_text(moment)}"
    return f"Today is {english_date_text(moment)}"


# -- public answers ----------------------------------------------------------

def time_answer(system="english"):
    """The spoken answer to "what is the current time".

    Both halves, because that is what was asked for: the time, then the date.
    """
    try:
        moment = now()
        return (f"The time is {time_text(moment)} {tz_name()}, Sir. "
                f"{_date_clause(system, moment)}."
                + (f" {_HIJRI_CAVEAT}" if system == "arabic" else ""))
    except Exception:
        return "I could not read the clock, Sir."


def date_answer(system="english"):
    """The spoken answer to "what is today's date", in one calendar or all three."""
    try:
        moment = now()
        if system == "all":
            return (f"Today is {english_date_text(moment)}, Sir. "
                    f"In the Bangla calendar that is {bangla_date_text(moment)}, "
                    f"and in the Hijri calendar {hijri_date_text(moment)}. "
                    f"{_HIJRI_CAVEAT}")
        answer = f"{_date_clause(system, moment)}, Sir."
        return answer + (f" {_HIJRI_CAVEAT}" if system == "arabic" else "")
    except Exception:
        return "I could not read the date, Sir."


def answer(kind="time", system="english"):
    """One entry point for both routes into this module."""
    if kind == "date":
        return date_answer(system)
    return time_answer(system if system in SYSTEMS else "english")


def snapshot():
    """Every field at once, JSON-safe. For the HUD and for the tests."""
    try:
        moment = now()
        bn, hj = to_bangla(moment), to_hijri(moment)
        return {
            "ok": True,
            "iso": moment.isoformat(timespec="seconds"),
            "time": moment.strftime("%H:%M"),
            "time_spoken": time_text(moment),
            "zone": tz_name(),
            "zone_short": TZ_SHORT,
            "weekday": moment.strftime("%A"),
            "english": english_date_text(moment),
            "english_short": moment.strftime("%d %b %Y").upper(),
            "bangla": bangla_date_text(moment),
            "bangla_short": (f"{bangla_numerals(bn['day'])} {bn['month_bn']} "
                             f"{bangla_numerals(bn['year'])}"),
            "bangla_parts": bn,
            "arabic": hijri_date_text(moment),
            "arabic_short": f"{hj['day']} {hj['month_name']} {hj['year']} AH",
            "arabic_parts": hj,
        }
    except Exception as e:
        return {"ok": False, "error": f"clock unavailable ({e.__class__.__name__})"}


# -- command line ------------------------------------------------------------

# Dates whose conversions are independently known. `--check` is the whole
# argument that the arithmetic above is right, so it ships with the module
# rather than living only in the test suite.
_KNOWN = (
    # (Gregorian, Bangla y/m/d, Hijri y/m/d, note)
    ((2019, 4, 14), (1426, 1, 1), (1440, 8, 8), "Pohela Boishakh 1426"),
    ((2026, 4, 14), (1433, 1, 1), (1447, 10, 26), "Pohela Boishakh 1433"),
    ((2026, 4, 13), (1432, 12, 30), (1447, 10, 25), "Chaitro 30, last day of 1432"),
    ((2026, 8, 24), (1433, 5, 9), (1448, 3, 10), "a plain mid-Bhadro day"),
    ((2000, 1, 1), (1406, 9, 17), (1420, 9, 24), "Y2K -- 24 Ramadan 1420"),
    ((2020, 2, 29), (1426, 11, 16), (1441, 7, 5), "a Gregorian leap day"),
)


def _check():
    """Verify the conversions against `_KNOWN`. Returns a process exit code."""
    bad = 0
    for greg, bn_want, hj_want, note in _KNOWN:
        d = datetime.date(*greg)
        bn, hj = to_bangla(d), to_hijri(d)
        bn_got = (bn["year"], bn["month"], bn["day"])
        hj_got = (hj["year"], hj["month"], hj["day"])
        for label, got, want in (("bangla", bn_got, bn_want),
                                 ("hijri", hj_got, hj_want)):
            ok = got == want
            bad += not ok
            print(f"  [{'PASS' if ok else 'FAIL'}] {d} {label:6} -> {got}"
                  + ("" if ok else f"  -- expected {want}"))
        print(f"         {note}")

    # Pohela Boishakh must land on 14 April for every year in a long run, which
    # is the property the whole revised calendar exists to guarantee -- and the
    # last day of Chaitro must be the day before, with no gap and no overlap.
    drift = []
    for year in range(1400, 1500):
        greg = year + _BN_EPOCH_OFFSET
        first = to_bangla(datetime.date(greg, 4, 14))
        last = to_bangla(datetime.date(greg, 4, 13))
        lengths = _bn_month_lengths(year - 1)
        if (first["year"], first["month"], first["day"]) != (year, 1, 1):
            drift.append(f"{year} starts {first['month']}/{first['day']}")
        elif (last["year"], last["month"], last["day"]) != (year - 1, 12, lengths[11]):
            drift.append(f"{year - 1} ends {last['month']}/{last['day']}")
    print(f"  [{'PASS' if not drift else 'FAIL'}] Boishakh 1 == 14 April, "
          f"Bangla 1400-1499" + ("" if not drift else f"  -- {drift[:3]}"))
    bad += bool(drift)

    # Every day of a century must round-trip to a valid month and day.
    broken = []
    d, end = datetime.date(1990, 1, 1), datetime.date(2090, 1, 1)
    step = datetime.timedelta(days=1)
    while d < end:
        bn = to_bangla(d)
        if not (1 <= bn["month"] <= 12
                and 1 <= bn["day"] <= _bn_month_lengths(bn["year"])[bn["month"] - 1]):
            broken.append(str(d))
        d += step
    print(f"  [{'PASS' if not broken else 'FAIL'}] every day 1990-2089 converts "
          f"in range" + ("" if not broken else f"  -- {broken[:5]}"))
    bad += bool(broken)

    print("\nAll conversions correct." if not bad else f"\n{bad} check(s) failed.")
    return 1 if bad else 0


def main(argv):
    if argv and argv[0] in ("--check", "-c"):
        return _check()

    snap = snapshot()
    print(f"[{snap.get('zone')}, UTC{tz().utcoffset(None).total_seconds() / 3600:+g}]\n")
    print(time_answer())
    print()
    for system in SYSTEMS:
        print(date_answer(system))
    print()
    print(date_answer("all"))
    print(f"\nHUD: {snap.get('english_short')} · {snap.get('bangla_short')} · "
          f"{snap.get('arabic_short')}")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
