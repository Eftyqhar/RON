"""Offline check of the clock and the three calendars in `clock.py`.

    python test_clock.py

Freezes `clock.now()` at a known moment, then asserts the conversions, the spoken
phrasing, and the routing table in `main.py` that decides a question is about the
clock at all. Needs no microphone, no API key and no network -- and never touches
the real `history.db`.

`python clock.py --check` covers the same arithmetic from inside the module, over a
century of dates. This suite covers what surrounds it: the phrasing SAPI has to
read, the environment overrides, and the negatives -- the phrasings that must NOT
be routed here, because weather or a tool owns them.
"""

import datetime
import json
import os
import sys
import tempfile

# Before importing anything that pulls in `history`: this suite imports `main`,
# which records a turn at import time. Without this it would append to whatever
# conversations you have actually had with RON.
os.environ["RON_DB"] = os.path.join(
    tempfile.mkdtemp(prefix="ron-clock-test-"), "history.db")

import clock

FAILURES = []


def check(label, condition, detail=""):
    mark = "PASS" if condition else "FAIL"
    print(f"  [{mark}] {label}" + (f"  -- {detail}" if detail and not condition else ""))
    if not condition:
        FAILURES.append(label)


# A Monday afternoon, chosen because all three calendars are independently known
# for it: 24 August 2026 is 9 Bhadro 1433 and 10 Rabi al-Awwal 1448.
FROZEN = datetime.datetime(2026, 8, 24, 16, 32, 5,
                           tzinfo=datetime.timezone(datetime.timedelta(hours=6)))


def freeze(moment=FROZEN):
    """Pin `clock.now()`. Everything spoken reads the clock through it."""
    clock.now = lambda: moment


def main():
    print("RON clock and calendar check\n")
    real_now = clock.now

    # -- 1. the timezone ------------------------------------------------------
    print("1. timezone")
    check("default is UTC+6", clock.tz().utcoffset(None) ==
          datetime.timedelta(hours=6), clock.tz().utcoffset(None))
    check("default is named", clock.tz_name() == "Bangladesh Standard Time",
          clock.tz_name())
    check("now() is timezone-aware", real_now().tzinfo is not None)

    os.environ["RON_TZ_OFFSET"] = "5.5"
    check("an offset override is honoured",
          clock.tz().utcoffset(None) == datetime.timedelta(hours=5, minutes=30),
          clock.tz().utcoffset(None))
    check("an override without a name is not still called Bangladesh time",
          clock.tz_name() == "local time", clock.tz_name())
    os.environ["RON_TZ_NAME"] = "India Standard Time"
    check("a name override is honoured",
          clock.tz_name() == "India Standard Time", clock.tz_name())

    os.environ["RON_TZ_OFFSET"] = "not a number"
    check("an unparseable offset falls back rather than raising",
          clock.tz().utcoffset(None) == datetime.timedelta(hours=6),
          clock.tz().utcoffset(None))
    os.environ["RON_TZ_OFFSET"] = "99"
    check("an absurd offset is clamped to a real one",
          clock.tz().utcoffset(None) == datetime.timedelta(hours=14),
          clock.tz().utcoffset(None))
    del os.environ["RON_TZ_OFFSET"]
    del os.environ["RON_TZ_NAME"]
    check("the default is restored", clock.tz_name() == "Bangladesh Standard Time")

    # -- 2. the Bangla calendar ----------------------------------------------
    # The revised (2019 Bangla Academy) calendar, in which Pohela Boishakh is
    # always 14 April. `clock.py --check` walks a century; these are the anchors.
    print("\n2. Bangla calendar")
    for greg, want, note in (
        ((2019, 4, 14), (1426, 1, 1), "Pohela Boishakh"),
        ((2026, 4, 14), (1433, 1, 1), "Pohela Boishakh"),
        ((2026, 4, 13), (1432, 12, 30), "Chaitro 30, the last day of a year"),
        ((2026, 8, 24), (1433, 5, 9), "a plain mid-Bhadro day"),
        ((2000, 1, 1), (1406, 9, 17), "a January date, so the year is greg-594"),
        ((2020, 2, 29), (1426, 11, 16), "a Gregorian leap day"),
    ):
        bn = clock.to_bangla(datetime.date(*greg))
        got = (bn["year"], bn["month"], bn["day"])
        check(f"{datetime.date(*greg)} -> Bangla {want} ({note})", got == want, got)

    bn = clock.to_bangla(datetime.date(2026, 8, 24))
    check("month names come in both scripts",
          (bn["month_name"], bn["month_bn"]) == ("Bhadro", "ভাদ্র"),
          (bn["month_name"], bn["month_bn"]))
    check("weekday is romanised and Bengali, and they differ",
          bn["weekday"] == "Sombar" and bn["weekday_bn"] == "সোমবার",
          (bn["weekday"], bn["weekday_bn"]))
    check("day of year is counted from Boishakh 1", bn["day_of_year"] == 133,
          bn["day_of_year"])

    # Falgun gains its 30th day when the *following* February is leap, which is
    # the whole mechanism pinning Boishakh 1 to 14 April.
    check("Falgun has 30 days before a leap February",
          clock._bn_month_lengths(1426)[clock._BN_FALGUN] == 30,
          clock._bn_month_lengths(1426)[clock._BN_FALGUN])
    check("Falgun has 29 days otherwise",
          clock._bn_month_lengths(1427)[clock._BN_FALGUN] == 29,
          clock._bn_month_lengths(1427)[clock._BN_FALGUN])
    check("a leap Bangla year is 366 days long",
          sum(clock._bn_month_lengths(1426)) == 366,
          sum(clock._bn_month_lengths(1426)))
    check("an ordinary one is 365", sum(clock._bn_month_lengths(1427)) == 365,
          sum(clock._bn_month_lengths(1427)))
    check("the leap flag is reported on the reading",
          clock.to_bangla(datetime.date(2019, 6, 1))["leap"] is True)

    # Consecutive days must never repeat or skip -- the loop in to_bangla() is
    # the only place a month-length error could hide.
    day, seen, broken = datetime.date(2026, 4, 10), [], []
    for _ in range(380):
        b = clock.to_bangla(day)
        seen.append((b["year"], b["month"], b["day"]))
        day += datetime.timedelta(days=1)
    check("a year of consecutive days is strictly increasing and unique",
          len(set(seen)) == len(seen) == 380, len(set(seen)))
    check("the year rolls over exactly once in 380 days",
          len({y for y, _, _ in seen}) == 2, sorted({y for y, _, _ in seen}))

    check("numerals convert for display", clock.bangla_numerals(1433) == "১৪৩৩",
          clock.bangla_numerals(1433))

    # -- 3. the Hijri calendar -----------------------------------------------
    # Tabular, so it can sit a day either side of the sighted date. These anchors
    # are the tabular values, which is what the module claims to produce.
    print("\n3. Hijri calendar")
    for greg, want, note in (
        ((2000, 1, 1), (1420, 9, 24), "Y2K -- 24 Ramadan 1420"),
        ((2019, 4, 14), (1440, 8, 8), None),
        ((2026, 4, 13), (1447, 10, 25), None),
        ((2026, 4, 14), (1447, 10, 26), "the day after, one day later"),
        ((2026, 8, 24), (1448, 3, 10), None),
        ((2020, 2, 29), (1441, 7, 5), "a Gregorian leap day"),
    ):
        hj = clock.to_hijri(datetime.date(*greg))
        got = (hj["year"], hj["month"], hj["day"])
        label = f"{datetime.date(*greg)} -> Hijri {want}"
        check(label + (f" ({note})" if note else ""), got == want, got)

    check("the month is named", clock.to_hijri(datetime.date(2000, 1, 1))
          ["month_name"] == "Ramadan")
    check("the Julian day number is right for a known epoch",
          clock._jdn(datetime.date(2000, 1, 1)) == 2451545,
          clock._jdn(datetime.date(2000, 1, 1)))
    check("Julian day numbers advance one per day",
          clock._jdn(datetime.date(2026, 8, 24))
          - clock._jdn(datetime.date(2026, 4, 14)) == 132,
          clock._jdn(datetime.date(2026, 8, 24))
          - clock._jdn(datetime.date(2026, 4, 14)))

    # Every month index must be in range across a long run, since month_name
    # indexes a 12-tuple with it.
    out_of_range = []
    day = datetime.date(1990, 1, 1)
    while day < datetime.date(2090, 1, 1):
        hj = clock.to_hijri(day)
        if not (1 <= hj["month"] <= 12 and 1 <= hj["day"] <= 30):
            out_of_range.append(str(day))
        day += datetime.timedelta(days=1)
    check("every day 1990-2089 gives a Hijri month 1-12 and day 1-30",
          not out_of_range, out_of_range[:5])

    # -- 4. phrasing ----------------------------------------------------------
    # SAPI reads text literally, so this is the layer that decides whether the
    # answer sounds like English or like a data dump.
    print("\n4. phrasing")
    NUMBERS = (1, 2, 3, 4, 9, 11, 12, 13, 21, 22, 30)
    check("ordinals", [clock._ordinal(n) for n in NUMBERS]
          == ["1st", "2nd", "3rd", "4th", "9th", "11th", "12th", "13th", "21st",
              "22nd", "30th"],
          [clock._ordinal(n) for n in NUMBERS])
    check("afternoon time reads as a clock, not two numbers",
          clock.time_text(FROZEN) == "4:32 p.m.", clock.time_text(FROZEN))
    check("morning time says a.m.",
          clock.time_text(FROZEN.replace(hour=9, minute=5)) == "9:05 a.m.",
          clock.time_text(FROZEN.replace(hour=9, minute=5)))
    check("minutes keep a leading zero",
          clock.time_text(FROZEN.replace(hour=13, minute=7)) == "1:07 p.m.",
          clock.time_text(FROZEN.replace(hour=13, minute=7)))
    check("noon is spoken, not 12:00",
          clock.time_text(FROZEN.replace(hour=12, minute=0)) == "twelve noon",
          clock.time_text(FROZEN.replace(hour=12, minute=0)))
    check("midnight is spoken, not 0:00",
          clock.time_text(FROZEN.replace(hour=0, minute=0)) == "midnight",
          clock.time_text(FROZEN.replace(hour=0, minute=0)))
    check("half past midnight is not called midnight",
          clock.time_text(FROZEN.replace(hour=0, minute=30)) == "12:30 a.m.",
          clock.time_text(FROZEN.replace(hour=0, minute=30)))
    check("English date reads as a sentence",
          clock.english_date_text(FROZEN) == "Monday, the 24th of August 2026",
          clock.english_date_text(FROZEN))
    check("Bangla date is romanised for the synthesiser",
          clock.bangla_date_text(FROZEN) == "the 9th of Bhadro, 1433",
          clock.bangla_date_text(FROZEN))
    check("Hijri date is romanised too",
          clock.hijri_date_text(FROZEN) == "the 10th of Rabi al-Awwal, 1448",
          clock.hijri_date_text(FROZEN))
    check("no Bengali script reaches a spoken string",
          clock.bangla_date_text(FROZEN).isascii(),
          clock.bangla_date_text(FROZEN))

    # -- 5. spoken answers ----------------------------------------------------
    # The headline requirement: asked for the time, RON gives the time AND the date.
    print("\n5. spoken answers")
    freeze()
    said = clock.time_answer()
    check("a time question answers with the time",
          "4:32 p.m." in said, said)
    check("...and names the zone", "Bangladesh Standard Time" in said, said)
    check("...and answers the date as well, unasked",
          "Monday, the 24th of August 2026" in said, said)
    check("...addressed to Sir", "Sir" in said, said)
    check("English is the default calendar for a time question",
          "Bangla" not in said and "Hijri" not in said, said)

    check("a time question can be answered in the Bangla calendar",
          "the 9th of Bhadro, 1433" in clock.time_answer("bangla"),
          clock.time_answer("bangla"))
    check("...and still gives the clock time",
          "4:32 p.m." in clock.time_answer("bangla"), clock.time_answer("bangla"))

    eng = clock.date_answer()
    check("a date question answers in English by default",
          eng == "Today is Monday, the 24th of August 2026, Sir.", eng)
    check("an unknown calendar name falls back to English",
          clock.answer("date", "klingon") == eng, clock.answer("date", "klingon"))

    bng = clock.date_answer("bangla")
    check("the Bangla date names its calendar",
          bng == "In the Bangla calendar it is the 9th of Bhadro, 1433, Sir.", bng)

    arb = clock.date_answer("arabic")
    check("the Hijri date names its calendar",
          "In the Hijri calendar it is the 10th of Rabi al-Awwal, 1448" in arb, arb)
    check("the Hijri answer admits it is the tabular reckoning",
          "sighted date may differ" in arb, arb)
    check("the caveat is not attached to the English date",
          "sighted" not in eng, eng)
    check("nor to the Bangla date, which is exact", "sighted" not in bng, bng)

    every = clock.date_answer("all")
    check("all three calendars in one answer",
          all(s in every for s in ("Monday, the 24th of August 2026",
                                   "the 9th of Bhadro, 1433",
                                   "the 10th of Rabi al-Awwal, 1448")), every)
    check("the caveat rides along with the Hijri date",
          "sighted date may differ" in every, every)

    check("answer() routes to the date", clock.answer("date", "bangla") == bng)
    check("answer() defaults to the time", clock.answer() == clock.time_answer())

    # -- 6. the snapshot ------------------------------------------------------
    print("\n6. snapshot")
    snap = clock.snapshot()
    for key in ("ok", "iso", "time", "time_spoken", "zone", "zone_short",
                "weekday", "english", "english_short", "bangla", "bangla_short",
                "bangla_parts", "arabic", "arabic_short", "arabic_parts"):
        check(f"snapshot has {key}", key in snap)
    check("snapshot is JSON-serialisable", bool(json.dumps(snap)))
    check("display strings are 24-hour and short",
          (snap["time"], snap["english_short"]) == ("16:32", "24 AUG 2026"),
          (snap["time"], snap["english_short"]))
    check("the Bangla short form uses Bengali numerals and script",
          snap["bangla_short"] == "৯ ভাদ্র ১৪৩৩", snap["bangla_short"])
    check("the Hijri short form is marked AH",
          snap["arabic_short"] == "10 Rabi al-Awwal 1448 AH", snap["arabic_short"])

    # -- 7. resilience --------------------------------------------------------
    # The leaf-module rule: the clock must never be able to take RON down.
    print("\n7. resilience")
    clock.now = lambda: (_ for _ in ()).throw(RuntimeError("clock is on fire"))
    check("a broken clock apologises instead of raising",
          clock.time_answer() == "I could not read the clock, Sir.",
          clock.time_answer())
    check("so does the date", clock.date_answer("all") ==
          "I could not read the date, Sir.", clock.date_answer("all"))
    broken = clock.snapshot()
    check("the snapshot reports the failure rather than raising",
          broken.get("ok") is False and "error" in broken, broken)
    check("the failed snapshot is still JSON-serialisable",
          bool(json.dumps(broken)))
    clock.now = real_now
    check("recovery is complete", clock.snapshot().get("ok") is True)

    # -- 8. routing -----------------------------------------------------------
    # Imported here rather than at the top: main.py seeds its context and stamps
    # the model at import time, and section 1 needs a clean environment first.
    print("\n8. routing")
    import voice

    class DummyVoice:
        def Speak(self, text):
            pass

    voice._tls.voice = DummyVoice()      # nothing audible escapes this suite
    import main as ron

    for said, want in (
        ("what is the current time", ("time", "english")),
        ("what's the time", ("time", "english")),
        ("what time is it", ("time", "english")),
        ("tell me the time", ("time", "english")),
        ("current time", ("time", "english")),
        ("ron what is the time right now", ("time", "english")),
        ("what is today's date", ("date", "english")),
        ("what day is it", ("date", "english")),
        ("date today", ("date", "english")),
        ("what month is it", ("date", "english")),
        ("what year is it", ("date", "english")),
        ("what's the date in bangla", ("date", "bangla")),
        ("what is the bangla date", ("date", "bangla")),
        ("what is the bengali date today", ("date", "bangla")),
        ("what's the arabic date", ("date", "arabic")),
        ("what is the hijri date", ("date", "arabic")),
        ("what is the islamic date today", ("date", "arabic")),
        ("what is the date in all three calendars", ("date", "all")),
        ("give me today's date in every calendar", ("date", "all")),
        # Both nouns: the time answer already carries the date, so time wins.
        ("what is the time and date", ("time", "english")),
        # A named calendar applies to a time question too.
        ("what is the current time in bangla", ("time", "bangla")),
    ):
        got = ron.extract_datetime(said)
        want_dict = {"kind": want[0], "system": want[1]}
        check(f'"{said}" -> {want[0]}/{want[1]}', got == want_dict, got)

    print("\n   negatives -- these must not reach the clock")
    for said, why in (
        ("what's the temperature today", "weather owns 'today'"),
        ("will it rain today", "weather"),
        ("what's the weather tomorrow", "weather"),
        ("what's the forecast for this week", "weather"),
        ("open documents", "a folder"),
        ("visit facebook", "a website"),
        ("play time after time", "a song that happens to be called Time"),
        ("create a pdf about the bangla calendar", "a document"),
        ("set a timer for ten minutes", "not implemented, but never the clock"),
        ("what is the uptime", "telemetry -- and \\btime\\b must not match it"),
        ("what time is the meeting", "a calendar entry, not the clock"),
        ("how much time do i have left", "not a clock question"),
        ("i am at my desk all day", "'all day' is not a date question"),
        ("is that file up to date", "'up to date' is not a date question"),
        ("how are you", "no clock or calendar noun at all"),
        ("", "empty input"),
    ):
        got = ron.extract_datetime(said)
        check(f'"{said}" -> None ({why})', got is None, got)

    print("\n   weather still wins the phrases it owns")
    for said in ("what's the temperature today", "will it rain today",
                 "what's the weather", "what's the forecast for this week"):
        check(f'"{said}" -> weather', ron.extract_weather(said) is not None,
              ron.extract_weather(said))
    print("   ...and does not steal the clock's")
    for said in ("what is the current time", "what is today's date",
                 "what's the bangla date"):
        check(f'"{said}" -> not weather', ron.extract_weather(said) is None,
              ron.extract_weather(said))

    # -- 9. the LLM fallback --------------------------------------------------
    # Same sentence, produced by the same helper, whichever route got here.
    print("\n9. LLM fallback")
    freeze()
    check("a tool call reaches the same answer as the direct route",
          ron.handle_get_datetime({"query": "date", "calendar": "bangla"})
          == ron.datetime_reply({"kind": "date", "system": "bangla"}),
          ron.handle_get_datetime({"query": "date", "calendar": "bangla"}))
    check("an empty tool call defaults to the time",
          "4:32 p.m." in ron.handle_get_datetime({}),
          ron.handle_get_datetime({}))
    check("a junk calendar name falls back to English",
          ron.handle_get_datetime({"query": "date", "calendar": "martian"})
          == "Today is Monday, the 24th of August 2026, Sir.",
          ron.handle_get_datetime({"query": "date", "calendar": "martian"}))
    check("'all' survives the tool call",
          "Hijri" in ron.handle_get_datetime({"query": "date", "calendar": "all"}),
          ron.handle_get_datetime({"query": "date", "calendar": "all"}))
    check("a garbled query is treated as a time question",
          "4:32 p.m." in ron.handle_get_datetime({"query": "clock please"}),
          ron.handle_get_datetime({"query": "clock please"}))
    check("the tool is offered to the model",
          "get_datetime" in ron.SYSTEM_PROMPT)
    check("the model is told not to invent a time itself",
          "you have no clock" in ron.SYSTEM_PROMPT)
    clock.now = real_now

    print()
    if FAILURES:
        print(f"{len(FAILURES)} check(s) failed: {', '.join(FAILURES)}")
        return 1
    print("All checks passed.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
