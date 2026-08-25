"""Offline check for Ron's PDF renderer -- no microphone, no API key needed.

Run:  python test_pdf.py

Covers the two failure modes that produced unusable files:
  * a placeholder body ("...") silently rendering one blank page
  * a non-ASCII title leaving a 0-byte .pdf behind on legacy fpdf, because
    output() truncates the file before encoding the buffer as latin-1
"""
import os
import sys

if sys.platform == "win32":
    import io
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8",
                                  errors="replace", line_buffering=True)

# Render without opening a viewer for every intermediate check.
os.environ["RON_NO_OPEN"] = "1"

from tools import (generate_pdf, _load_fonts, _is_unusable, _safe_name,
                   _documents_dir, _build_pdf, RonPDF)
from tools import _ADD_FONT_TAKES_UNI, _MULTI_CELL_MARKDOWN, _IS_LEGACY_FPDF
import fpdf

SAMPLE = """# Black Holes

## Introduction

A **black hole** is a region of spacetime where gravity is so intense that
nothing -- not even light -- can escape it. The concept emerged from Einstein's
general theory of relativity in 1915, and the first confirmed detection came
decades later with the X-ray source Cygnus X-1 in 1971.

Their study sits at the intersection of gravitation, thermodynamics, and
quantum mechanics, which is why they remain central to modern physics.

## Formation

Black holes form through several distinct channels:

- **Stellar collapse** -- stars above roughly 20 solar masses exhaust their fuel
  and collapse past the neutron-star limit
- **Direct collapse** of primordial gas clouds in the early universe
- **Mergers** of existing black holes, as detected by LIGO in 2015
  - The first event, GW150914, merged 36 and 29 solar-mass objects
  - It released ~3 solar masses of energy as gravitational waves

### The Chandrasekhar Limit

Above 1.4 solar masses, electron degeneracy pressure cannot halt collapse.

## Types

1. Stellar-mass -- 3 to 100 solar masses
2. Intermediate-mass -- 100 to 100,000 solar masses
3. Supermassive -- millions to billions of solar masses

> Sagittarius A*, at the centre of the Milky Way, is roughly 4.3 million
> solar masses and 26,000 light-years away.

---

## Key Takeaways

- Black holes are predicted by general relativity, not exotic add-ons
- The event horizon is a one-way boundary, not a physical surface
- Hawking radiation implies they slowly evaporate -- temperature is 1/M
- The first image of one (M87*) was published in April 2019

## Conclusion

Black holes went from mathematical curiosity to observed astrophysical object
within a century. Unicode stress test: caf\u00e9, na\u00efve, 50 \u00b0C,
\u03b1 \u03b2 \u03b3, 10\u00d710, \u2192, \u00bd, \u201csmart quotes\u201d,
em\u2014dash.
"""

# A body long enough to exercise the 3-5 page target the writer prompt asks for.
_PARA = ("Accretion discs around compact objects convert gravitational "
         "potential energy into radiation with an efficiency far exceeding "
         "nuclear fusion, which is why quasars outshine their host galaxies. "
         "The inner edge of the disc is set by the innermost stable circular "
         "orbit, and its radius depends on the spin of the hole. ")
LONG = SAMPLE + "".join(
    f"\n## Supplementary Section {i}\n\n{_PARA * 3}\n\n"
    f"- Observational signature {i}a\n- Observational signature {i}b\n"
    for i in range(1, 7)
)


def path_for(name):
    return os.path.join(_documents_dir(), f"{name}.pdf")


def cleanup(*names):
    for name in names:
        for suffix in ("", "-2", "-3"):
            p = path_for(f"{name}{suffix}")
            if os.path.exists(p):
                try:
                    os.remove(p)
                except OSError:
                    pass


print("=" * 66)
print(f"library               : {'legacy fpdf' if _IS_LEGACY_FPDF else 'fpdf2'}")
print(f"FPDF_VERSION          : {fpdf.FPDF_VERSION}")
print(f"add_font takes 'uni'  : {_ADD_FONT_TAKES_UNI}")
print(f"multi_cell 'markdown' : {_MULTI_CELL_MARKDOWN}"
      f"{'  (inline bold is emulated via write())' if not _MULTI_CELL_MARKDOWN else ''}")

probe = RonPDF()
probe.add_page()
family, unicode_ok = _load_fonts(probe, SAMPLE)
print(f"font resolved         : {family} (unicode={unicode_ok})")
print(f"styles registered     : {sorted(getattr(probe, '_ron_styles', set()))}")
print(f"documents folder      : {_documents_dir()}")
print("=" * 66)

cleanup("should_not_exist", "ron_pdf_selftest", "ron_meta_test", "ron_long_test")

# 1. Guard behaviour: placeholders must NOT produce a file.
print("\n-- placeholder guard --")
for bad in ["", "   ", "...", "\u2026", "content", "\n\n"]:
    assert _is_unusable(bad), f"should be rejected: {bad!r}"
    msg = generate_pdf("should_not_exist", bad)
    assert "did not save" in msg, msg
    print(f"  rejected {bad!r:12} -> {msg[:44]}...")
assert not os.path.exists(path_for("should_not_exist")), "a placeholder wrote a file!"
assert not _is_unusable(SAMPLE), "real content was wrongly rejected"
print("  real content accepted; no stray file created")

# 2. Filename hardening.
print("\n-- filename hardening --")
for raw, want in [
    ("../../../evil", "evil"),
    ("C:\\Windows\\System32\\cfg", "cfg"),
    ("", "document"),
    ("notes.pdf", "notes"),
    ("  spaced  name  ", "spaced name"),
]:
    got = _safe_name(raw)
    assert got == want, f"_safe_name({raw!r}) = {got!r}, expected {want!r}"
    print(f"  {raw!r:32} -> {got!r}")

illegal = _safe_name('bad<>:"|?*name')
assert not set(illegal) & set('<>:"/\\|?*'), f"illegal chars survived: {illegal!r}"
assert illegal.startswith("bad") and illegal.endswith("name"), illegal
print(f"  {'bad<>:\"|?*name'!r:32} -> {illegal!r}")

# 3. Regression: a non-latin-1 title must not truncate the file to 0 bytes.
print("\n-- non-ASCII title (0-byte regression) --")
msg = generate_pdf("ron_meta_test", SAMPLE,
                   title="Caf\u00e9 \u2014 \u00dcnicode \u201cSmart\u201d Quotes \u4e2d\u6587")
meta_path = path_for("ron_meta_test")
assert os.path.exists(meta_path), f"no file produced: {msg}"
size = os.path.getsize(meta_path)
assert size > 2000, f"file is {size} bytes -- the metadata path truncated it"
print(f"  {size} bytes, ok -> {msg}")

# 4. Page count for a realistic report body.
print("\n-- report length --")
words = len(LONG.split())
probe_long = _build_pdf(LONG, "Black Holes", "ron_long_test", md=True)
pages = probe_long.page_no()
print(f"  {words} words -> {pages} pages")
assert pages >= 3, f"expected 3+ pages for {words} words, got {pages}"

msg = generate_pdf("ron_long_test", LONG, title="Black Holes")
assert os.path.exists(path_for("ron_long_test")), msg
print(f"  {msg}")

# 5. Existing files must not be clobbered.
print("\n-- no-clobber --")
generate_pdf("ron_meta_test", SAMPLE, title="Second Run")
assert os.path.exists(path_for("ron_meta_test-2")), "second save overwrote the first"
print("  second save became ron_meta_test-2.pdf")

# 6. The showcase render -- this one opens so you can eyeball it.
print("\n-- rendering (opens in your viewer) --")
del os.environ["RON_NO_OPEN"]
print(generate_pdf("ron_pdf_selftest", SAMPLE, title="Black Holes"))

cleanup("ron_meta_test", "ron_long_test")
print("\nAll checks passed.")
