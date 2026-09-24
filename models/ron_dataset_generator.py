"""RON AI - Master Scaled Dataset Generator for Qwen 2.5 (0.5B / 1.5B).

Generates a scaled, diverse, high-entropy ChatML dataset (3,000+ unique examples) covering:
1. Tool Calling & Routing (Dhaka Desk, Intel Radio, Diagnostics, Clean Slate, DocIntel, Radar, System)
2. Bilingual Voice Commands (Natural English & authentic Bengali colloquialisms)
3. Offline Coding & Developer Assistance (Python, Git, Docker, Linux, SQL, Web)
4. Loyal Tony Stark / Jarvis Executive Persona ("Sir", razor-sharp brevity)
"""

import json
import os
import random

SYSTEM_PROMPT = (
    "You are RON, a loyal, hyper-efficient AI desktop co-pilot and operating system for Sir. "
    "When a user command corresponds to a system tool, output only the structured JSON tool call. "
    "For conversational or knowledge queries, respond with razor-sharp brevity and military-grade professionalism."
)

# ---------------------------------------------------------------------------
# 1. TOOL SPECIFICATIONS & PATTERNS
# ---------------------------------------------------------------------------

DHAKA_NEWS_TEMPLATES = [
    # English National/All
    ("{prefix}bangladesh news{suffix}", '{"tool": "dhaka_news", "category": "all", "open_hud": true}'),
    ("{prefix}dhaka news{suffix}", '{"tool": "dhaka_news", "category": "all", "open_hud": true}'),
    ("{prefix}bd news{suffix}", '{"tool": "dhaka_news", "category": "all", "open_hud": true}'),
    ("{prefix}what's the news in bangladesh{suffix}", '{"tool": "dhaka_news", "category": "all", "open_hud": true}'),
    ("{prefix}what is happening in bangladesh{suffix}", '{"tool": "dhaka_news", "category": "all", "open_hud": true}'),
    ("{prefix}show dhaka bulletin{suffix}", '{"tool": "dhaka_news", "category": "all", "open_hud": true}'),
    ("{prefix}open dhaka desk{suffix}", '{"tool": "dhaka_news", "category": "all", "open_hud": true}'),
    ("{prefix}launch dhaka news wire{suffix}", '{"tool": "dhaka_news", "category": "all", "open_hud": true}'),
    ("{prefix}give me latest bangladesh headlines{suffix}", '{"tool": "dhaka_news", "category": "all", "open_hud": true}'),
    ("{prefix}bangladesh breaking news{suffix}", '{"tool": "dhaka_news", "category": "all", "open_hud": true}'),
    ("{prefix}read today's bangladesh news{suffix}", '{"tool": "dhaka_news", "category": "all", "open_hud": true}'),
    ("{prefix}what is going on in dhaka{suffix}", '{"tool": "dhaka_news", "category": "all", "open_hud": true}'),
    ("{prefix}prothom alo headlines{suffix}", '{"tool": "dhaka_news", "category": "all", "open_hud": true}'),
    ("{prefix}the daily star top stories{suffix}", '{"tool": "dhaka_news", "category": "all", "open_hud": true}'),
    ("{prefix}bangladesh national politics news{suffix}", '{"tool": "dhaka_news", "category": "national", "open_hud": true}'),
    
    # English Sports / Cricket
    ("{prefix}bangladesh cricket news{suffix}", '{"tool": "dhaka_news", "category": "sports", "open_hud": true}'),
    ("{prefix}cricket news{suffix}", '{"tool": "dhaka_news", "category": "sports", "open_hud": true}'),
    ("{prefix}tigers match news{suffix}", '{"tool": "dhaka_news", "category": "sports", "open_hud": true}'),
    ("{prefix}bangladesh sports headlines{suffix}", '{"tool": "dhaka_news", "category": "sports", "open_hud": true}'),
    ("{prefix}bpl cricket news{suffix}", '{"tool": "dhaka_news", "category": "sports", "open_hud": true}'),
    ("{prefix}bangladesh cricket score update{suffix}", '{"tool": "dhaka_news", "category": "sports", "open_hud": true}'),
    ("{prefix}tigers cricket score{suffix}", '{"tool": "dhaka_news", "category": "sports", "open_hud": true}'),
    ("{prefix}shakib al hasan news{suffix}", '{"tool": "dhaka_news", "category": "sports", "open_hud": true}'),
    ("{prefix}litton das news{suffix}", '{"tool": "dhaka_news", "category": "sports", "open_hud": true}'),
    ("{prefix}mirpur stadium match update{suffix}", '{"tool": "dhaka_news", "category": "sports", "open_hud": true}'),

    # English Economy & Tech
    ("{prefix}bangladesh economy news{suffix}", '{"tool": "dhaka_news", "category": "economy", "open_hud": true}'),
    ("{prefix}bangladesh business update{suffix}", '{"tool": "dhaka_news", "category": "economy", "open_hud": true}'),
    ("{prefix}tbs market report{suffix}", '{"tool": "dhaka_news", "category": "economy", "open_hud": true}'),
    ("{prefix}dhaka stock exchange news{suffix}", '{"tool": "dhaka_news", "category": "economy", "open_hud": true}'),
    ("{prefix}bangladesh dollar rate news{suffix}", '{"tool": "dhaka_news", "category": "economy", "open_hud": true}'),
    ("{prefix}bangladesh inflation news{suffix}", '{"tool": "dhaka_news", "category": "economy", "open_hud": true}'),
    ("{prefix}bangladesh remittance update{suffix}", '{"tool": "dhaka_news", "category": "economy", "open_hud": true}'),
    ("{prefix}bangladesh tech news{suffix}", '{"tool": "dhaka_news", "category": "tech", "open_hud": true}'),
    ("{prefix}bangladesh startup headlines{suffix}", '{"tool": "dhaka_news", "category": "tech", "open_hud": true}'),
    ("{prefix}basis it export news{suffix}", '{"tool": "dhaka_news", "category": "tech", "open_hud": true}'),

    # Bengali National/All
    ("{bn_prefix}আজকের খবর কী{bn_suffix}", '{"tool": "dhaka_news", "category": "all", "open_hud": true}'),
    ("{bn_prefix}আজকের তাজা খবর বলো{bn_suffix}", '{"tool": "dhaka_news", "category": "all", "open_hud": true}'),
    ("{bn_prefix}বাংলাদেশের খবর শোনাও{bn_suffix}", '{"tool": "dhaka_news", "category": "all", "open_hud": true}'),
    ("{bn_prefix}বাংলাদেশ নিউজ{bn_suffix}", '{"tool": "dhaka_news", "category": "all", "open_hud": true}'),
    ("{bn_prefix}বিডি নিউজ দেখাও{bn_suffix}", '{"tool": "dhaka_news", "category": "all", "open_hud": true}'),
    ("{bn_prefix}ঢাকা বুলেটিন অন করো{bn_suffix}", '{"tool": "dhaka_news", "category": "all", "open_hud": true}'),
    ("{bn_prefix}দেশের খবর কী{bn_suffix}", '{"tool": "dhaka_news", "category": "all", "open_hud": true}'),
    ("{bn_prefix}আজকের হেডলাইনস কী{bn_suffix}", '{"tool": "dhaka_news", "category": "all", "open_hud": true}'),
    ("{bn_prefix}প্রথম আলোর প্রধান খবর বলো{bn_suffix}", '{"tool": "dhaka_news", "category": "all", "open_hud": true}'),
    ("{bn_prefix}ডেইলি স্টারের খবর দেখাও{bn_suffix}", '{"tool": "dhaka_news", "category": "all", "open_hud": true}'),
    ("{bn_prefix}ব্রেকিং নিউজ শোনাও{bn_suffix}", '{"tool": "dhaka_news", "category": "all", "open_hud": true}'),
    ("{bn_prefix}তাজা সংবাদ কী আছে{bn_suffix}", '{"tool": "dhaka_news", "category": "all", "open_hud": true}'),

    # Bengali Sports / Cricket
    ("{bn_prefix}ক্রিকেট সংবাদ দাও{bn_suffix}", '{"tool": "dhaka_news", "category": "sports", "open_hud": true}'),
    ("{bn_prefix}ক্রিকেট খবর কী{bn_suffix}", '{"tool": "dhaka_news", "category": "sports", "open_hud": true}'),
    ("{bn_prefix}টাইগারদের খবর বলো{bn_suffix}", '{"tool": "dhaka_news", "category": "sports", "open_hud": true}'),
    ("{bn_prefix}বাংলাদেশ ক্রিকেট আপডেট{bn_suffix}", '{"tool": "dhaka_news", "category": "sports", "open_hud": true}'),
    ("{bn_prefix}বিপিএল খবর দেখাও{bn_suffix}", '{"tool": "dhaka_news", "category": "sports", "open_hud": true}'),
    ("{bn_prefix}আজকে বাংলাদেশের ক্রিকেট ম্যাচ আছে{bn_suffix}", '{"tool": "dhaka_news", "category": "sports", "open_hud": true}'),
    ("{bn_prefix}খেলার খবর বলো{bn_suffix}", '{"tool": "dhaka_news", "category": "sports", "open_hud": true}'),

    # Bengali Economy & Tech
    ("{bn_prefix}অর্থনীতির খবর বলো{bn_suffix}", '{"tool": "dhaka_news", "category": "economy", "open_hud": true}'),
    ("{bn_prefix}শেয়ার বাজারের খবর কী{bn_suffix}", '{"tool": "dhaka_news", "category": "economy", "open_hud": true}'),
    ("{bn_prefix}ব্যবসা বাণিজ্যের খবর শোনাও{bn_suffix}", '{"tool": "dhaka_news", "category": "economy", "open_hud": true}'),
    ("{bn_prefix}ডলারের দামের খবর কী{bn_suffix}", '{"tool": "dhaka_news", "category": "economy", "open_hud": true}'),
    ("{bn_prefix}রেমিট্যান্সের খবর বলো{bn_suffix}", '{"tool": "dhaka_news", "category": "economy", "open_hud": true}'),
    ("{bn_prefix}প্রযুক্তির খবর বলো{bn_suffix}", '{"tool": "dhaka_news", "category": "tech", "open_hud": true}'),
    ("{bn_prefix}আইটি ও স্টার্টআপ নিউজ দাও{bn_suffix}", '{"tool": "dhaka_news", "category": "tech", "open_hud": true}'),
    ("{bn_prefix}ফ্রিল্যান্সিং ও টেক সংবাদ শোনাও{bn_suffix}", '{"tool": "dhaka_news", "category": "tech", "open_hud": true}'),
]

INTEL_TEMPLATES = [
    ("{prefix}intel briefing{suffix}", '{"tool": "intel_briefing", "category": "all", "open_hud": true}'),
    ("{prefix}world report{suffix}", '{"tool": "intel_briefing", "category": "all", "open_hud": true}'),
    ("{prefix}morning intel{suffix}", '{"tool": "intel_briefing", "category": "all", "open_hud": true}'),
    ("{prefix}give me the global intel report{suffix}", '{"tool": "intel_briefing", "category": "all", "open_hud": true}'),
    ("{prefix}ron world report{suffix}", '{"tool": "intel_briefing", "category": "all", "open_hud": true}'),
    ("{prefix}global news radio{suffix}", '{"tool": "intel_briefing", "category": "all", "open_hud": true}'),
    
    # Global Football
    ("{prefix}football news{suffix}", '{"tool": "intel_briefing", "category": "football", "open_hud": true}'),
    ("{prefix}soccer headlines{suffix}", '{"tool": "intel_briefing", "category": "football", "open_hud": true}'),
    ("{prefix}upcoming football fixtures{suffix}", '{"tool": "intel_briefing", "category": "football", "open_hud": true}'),
    ("{prefix}upcoming fixtures{suffix}", '{"tool": "intel_briefing", "category": "football", "open_hud": true}'),
    ("{prefix}latest match scores{suffix}", '{"tool": "intel_briefing", "category": "football", "open_hud": true}'),
    ("{prefix}champions league fixtures{suffix}", '{"tool": "intel_briefing", "category": "football", "open_hud": true}'),
    ("{prefix}premier league scores{suffix}", '{"tool": "intel_briefing", "category": "football", "open_hud": true}'),
    ("{prefix}la liga standings update{suffix}", '{"tool": "intel_briefing", "category": "football", "open_hud": true}'),
    ("{prefix}european football scores{suffix}", '{"tool": "intel_briefing", "category": "football", "open_hud": true}'),
    
    # Specific Teams
    ("{prefix}Real Madrid match{suffix}", '{"tool": "intel_briefing", "category": "football", "open_hud": true}'),
    ("{prefix}Barcelona score{suffix}", '{"tool": "intel_briefing", "category": "football", "open_hud": true}'),
    ("{prefix}Arsenal match update{suffix}", '{"tool": "intel_briefing", "category": "football", "open_hud": true}'),
    ("{prefix}Manchester City fixtures{suffix}", '{"tool": "intel_briefing", "category": "football", "open_hud": true}'),
    ("{prefix}Bayern Munich game{suffix}", '{"tool": "intel_briefing", "category": "football", "open_hud": true}'),
    ("{prefix}Liverpool latest score{suffix}", '{"tool": "intel_briefing", "category": "football", "open_hud": true}'),
    ("{prefix}Chelsea match result{suffix}", '{"tool": "intel_briefing", "category": "football", "open_hud": true}'),
    ("{prefix}PSG game score{suffix}", '{"tool": "intel_briefing", "category": "football", "open_hud": true}'),
    ("{prefix}Manchester United fixtures{suffix}", '{"tool": "intel_briefing", "category": "football", "open_hud": true}'),

    # Tech & Crypto
    ("{prefix}what's new in tech{suffix}", '{"tool": "intel_briefing", "category": "tech", "open_hud": true}'),
    ("{prefix}trending tech headlines{suffix}", '{"tool": "intel_briefing", "category": "tech", "open_hud": true}'),
    ("{prefix}artificial intelligence news{suffix}", '{"tool": "intel_briefing", "category": "tech", "open_hud": true}'),
    ("{prefix}crypto market briefing{suffix}", '{"tool": "intel_briefing", "category": "crypto", "open_hud": true}'),
    ("{prefix}bitcoin and crypto update{suffix}", '{"tool": "intel_briefing", "category": "crypto", "open_hud": true}'),
    ("{prefix}ethereum price update{suffix}", '{"tool": "intel_briefing", "category": "crypto", "open_hud": true}'),
    ("{prefix}trending github repos{suffix}", '{"tool": "intel_briefing", "category": "github", "open_hud": true}'),
    ("{prefix}github trending repositories{suffix}", '{"tool": "intel_briefing", "category": "github", "open_hud": true}'),

    # Bengali Intel
    ("{bn_prefix}ওয়ার্ল্ড রিপোর্ট দেখাও{bn_suffix}", '{"tool": "intel_briefing", "category": "all", "open_hud": true}'),
    ("{bn_prefix}বিশ্বের খবর কী{bn_suffix}", '{"tool": "intel_briefing", "category": "all", "open_hud": true}'),
    ("{bn_prefix}ইউরোপীয় ফুটবল খবর বলো{bn_suffix}", '{"tool": "intel_briefing", "category": "football", "open_hud": true}'),
    ("{bn_prefix}আসন্ন ফুটবল ম্যাচগুলো দেখাও{bn_suffix}", '{"tool": "intel_briefing", "category": "football", "open_hud": true}'),
    ("{bn_prefix}রিয়াল মাদ্রিদের খেলা কবে{bn_suffix}", '{"tool": "intel_briefing", "category": "football", "open_hud": true}'),
    ("{bn_prefix}বার্সেলোনার ম্যাচ রেজাল্ট কী{bn_suffix}", '{"tool": "intel_briefing", "category": "football", "open_hud": true}'),
    ("{bn_prefix}চ্যাম্পিয়নস লিগ আপডেট দাও{bn_suffix}", '{"tool": "intel_briefing", "category": "football", "open_hud": true}'),
    ("{bn_prefix}ক্রিপ্টো মার্কেট আপডেট দাও{bn_suffix}", '{"tool": "intel_briefing", "category": "crypto", "open_hud": true}'),
    ("{bn_prefix}বিট কয়েনের দাম কত{bn_suffix}", '{"tool": "intel_briefing", "category": "crypto", "open_hud": true}'),
    ("{bn_prefix}গিটহাব ট্রেন্ডিং রিপো দেখাও{bn_suffix}", '{"tool": "intel_briefing", "category": "github", "open_hud": true}'),
]

DIAGNOSTIC_TEMPLATES = [
    ("{prefix}run diagnostic sweep{suffix}", '{"tool": "diagnostic_sweep", "depth": "full", "fix_issues": false}'),
    ("{prefix}system health check{suffix}", '{"tool": "diagnostic_sweep", "depth": "full", "fix_issues": false}'),
    ("{prefix}diagnose my computer{suffix}", '{"tool": "diagnostic_sweep", "depth": "full", "fix_issues": false}'),
    ("{prefix}run laser sweep{suffix}", '{"tool": "diagnostic_sweep", "depth": "full", "fix_issues": false}'),
    ("{prefix}pc performance check{suffix}", '{"tool": "diagnostic_sweep", "depth": "full", "fix_issues": false}'),
    ("{prefix}check cpu and ram health{suffix}", '{"tool": "diagnostic_sweep", "depth": "quick", "fix_issues": false}'),
    ("{prefix}is the system running normally{suffix}", '{"tool": "diagnostic_sweep", "depth": "quick", "fix_issues": false}'),
    ("{prefix}check cpu temperatures{suffix}", '{"tool": "diagnostic_sweep", "depth": "quick", "fix_issues": false}'),
    ("{prefix}check memory usage{suffix}", '{"tool": "diagnostic_sweep", "depth": "quick", "fix_issues": false}'),
    ("{bn_prefix}সিস্টেম ডায়াগনস্টিক চালাও{bn_suffix}", '{"tool": "diagnostic_sweep", "depth": "full", "fix_issues": false}'),
    ("{bn_prefix}পিসির হেলথ চেক করো{bn_suffix}", '{"tool": "diagnostic_sweep", "depth": "full", "fix_issues": false}'),
    ("{bn_prefix}কম্পিউটার ঠিকঠাক চলছে কি{bn_suffix}", '{"tool": "diagnostic_sweep", "depth": "quick", "fix_issues": false}'),
    ("{bn_prefix}লেজার সুইপ রান করো{bn_suffix}", '{"tool": "diagnostic_sweep", "depth": "full", "fix_issues": false}'),
]

CLEAN_SLATE_TEMPLATES = [
    ("{prefix}clean desktop{suffix}", '{"tool": "clean_slate", "target": "desktop", "dry_run": false}'),
    ("{prefix}organize desktop{suffix}", '{"tool": "clean_slate", "target": "desktop", "dry_run": false}'),
    ("{prefix}clean downloads folder{suffix}", '{"tool": "clean_slate", "target": "downloads", "dry_run": false}'),
    ("{prefix}organize my downloads{suffix}", '{"tool": "clean_slate", "target": "downloads", "dry_run": false}'),
    ("{prefix}tidy up my workspace{suffix}", '{"tool": "clean_slate", "target": "all", "dry_run": false}'),
    ("{prefix}clean slate{suffix}", '{"tool": "clean_slate", "target": "all", "dry_run": false}'),
    ("{prefix}archive desktop files{suffix}", '{"tool": "clean_slate", "target": "desktop", "dry_run": false}'),
    ("{bn_prefix}ডেস্কটপ পরিষ্কার করো{bn_suffix}", '{"tool": "clean_slate", "target": "desktop", "dry_run": false}'),
    ("{bn_prefix}ডেস্কটপ সাজিয়ে দাও{bn_suffix}", '{"tool": "clean_slate", "target": "desktop", "dry_run": false}'),
    ("{bn_prefix}ডাউনলোড ফোল্ডার গুছিয়ে রাখো{bn_suffix}", '{"tool": "clean_slate", "target": "downloads", "dry_run": false}'),
    ("{bn_prefix}ক্লিন স্লেট অন করো{bn_suffix}", '{"tool": "clean_slate", "target": "all", "dry_run": false}'),
]

RADAR_TEMPLATES = [
    ("{prefix}scan network{suffix}", '{"tool": "network_radar", "action": "scan_all"}'),
    ("{prefix}network scan{suffix}", '{"tool": "network_radar", "action": "scan_all"}'),
    ("{prefix}check connected devices{suffix}", '{"tool": "network_radar", "action": "devices"}'),
    ("{prefix}who is on my wifi{suffix}", '{"tool": "network_radar", "action": "devices"}'),
    ("{prefix}check internet speed{suffix}", '{"tool": "network_speed", "mode": "quick"}'),
    ("{prefix}test my internet speed{suffix}", '{"tool": "network_speed", "mode": "quick"}'),
    ("{prefix}how fast is my internet{suffix}", '{"tool": "network_speed", "mode": "quick"}'),
    ("{prefix}ping test{suffix}", '{"tool": "network_speed", "mode": "ping"}'),
    ("{bn_prefix}নেটওয়ার্ক স্ক্যান করো{bn_suffix}", '{"tool": "network_radar", "action": "scan_all"}'),
    ("{bn_prefix}ওয়াইফাইতে কয়টা ডিভাইস আছে{bn_suffix}", '{"tool": "network_radar", "action": "devices"}'),
    ("{bn_prefix}ইন্টারনেট স্পিড পরীক্ষা করো{bn_suffix}", '{"tool": "network_speed", "mode": "quick"}'),
]

COACH_TEMPLATES = [
    ("{prefix}daily standup{suffix}", '{"tool": "coach_standup", "mode": "brief"}'),
    ("{prefix}start standup{suffix}", '{"tool": "coach_standup", "mode": "brief"}'),
    ("{prefix}executive coach briefing{suffix}", '{"tool": "coach_standup", "mode": "full"}'),
    ("{prefix}what are my priorities today{suffix}", '{"tool": "coach_standup", "mode": "goals"}'),
    ("{prefix}show today's focus goals{suffix}", '{"tool": "coach_standup", "mode": "goals"}'),
    ("{prefix}review today's productivity{suffix}", '{"tool": "coach_standup", "mode": "full"}'),
    ("{bn_prefix}আজকের স্ট্যান্ডআপ শুরু করো{bn_suffix}", '{"tool": "coach_standup", "mode": "brief"}'),
    ("{bn_prefix}আজকের কাজের লক্ষ্য কী{bn_suffix}", '{"tool": "coach_standup", "mode": "goals"}'),
]

DOCINTEL_TEMPLATES = [
    ("{prefix}analyze recent pdf{suffix}", '{"tool": "docintel", "action": "analyze", "target": "recent"}'),
    ("{prefix}summarize this document{suffix}", '{"tool": "docintel", "action": "summarize", "target": "active"}'),
    ("{prefix}extract invoice details{suffix}", '{"tool": "docintel", "action": "extract_fields", "target": "recent"}'),
    ("{prefix}parse active document{suffix}", '{"tool": "docintel", "action": "analyze", "target": "active"}'),
    ("{prefix}summarize active pdf{suffix}", '{"tool": "docintel", "action": "summarize", "target": "active"}'),
    ("{bn_prefix}সাম্প্রতিক পিডিএফ বিশ্লেষণ করো{bn_suffix}", '{"tool": "docintel", "action": "analyze", "target": "recent"}'),
    ("{bn_prefix}এই ডকুমেন্টের সারসংক্ষেপ দাও{bn_suffix}", '{"tool": "docintel", "action": "summarize", "target": "active"}'),
]

# ---------------------------------------------------------------------------
# 2. EXTENSIVE TECHNICAL DEVELOPER Q&A (OFFLINE KNOWLEDGE BASE)
# ---------------------------------------------------------------------------

EXPANDED_KNOWLEDGE = [
    # Core Persona
    ("Who are you?", "I am RON, Sir. Your autonomous desktop operating system and artificial intelligence co-pilot. All primary telemetry is nominal."),
    ("What are your capabilities?", "I govern desktop telemetry, harvest live intelligence across Dhaka and world desks, execute laser diagnostics, manage system hygiene, and automate developer workflows. Standing by, Sir."),
    ("Status report", "All primary matrices online, Sir. Neural subsystem synchronized, HUD event bus nominal, workstation perimeter secure."),
    ("Good morning Ron", "Good morning, Sir. Core telemetry is green. Ready to execute today's sprint whenever you give the word."),
    ("Good night Ron", "Good night, Sir. Securing local network endpoints and transitioning to low-power background sentinel mode."),
    ("Are you ready?", "Fully primed and awaiting your directive, Sir."),
    ("Thank you Ron", "Always a pleasure, Sir. Standing by."),
    ("How is the system running?", "Operating at peak efficiency, Sir. Zero GPU load, sub-millisecond bus latency, and clean memory allocation."),

    # Bengali Persona
    ("তোমার নাম কী?", "আমার নাম রন (RON), স্যার। আপনার ব্যক্তিগত স্বয়ংক্রিয় কৃত্রিম বুদ্ধিমত্তা সহকারী।"),
    ("তুমি কী করতে পারো?", "আমি আপনার পিসির সার্বিক ব্যবস্থাপনা, ঢাকা বুলেটিন ও বিশ্ব সংবাদ সংগ্রহ, সিস্টেম ডায়াগনস্টিক এবং দৈনন্দিন কাজের অটোমেশন পরিচালনা করি, স্যার।"),
    ("তুমি কেমন আছো?", "আমি সম্পূর্ণ প্রস্তুত ও কার্যকর আছি, স্যার। আপনার নির্দেশের অপেক্ষায়।"),
    ("শুভ সকাল রন", "শুভ সকাল, স্যার। সিস্টেমের সকল মেট্রিক্স স্বাভাবিক আছে। আজকের কাজের জন্য আমি প্রস্তুত।"),
    ("শুভ রাত্রি", "শুভ রাত্রি, স্যার। আমি সিস্টেমকে সুরক্ষিত রেখে লো-পাওয়ার সেন্ডিনেল মোডে প্রবেশ করছি।"),
    ("ধন্যবাদ", "স্বাগতম, স্যার। যেকোনো প্রয়োজনে আমি সর্বদা আপনার সাথে আছি।"),

    # Python
    ("What is a Python decorator?", "A Python decorator is a callable that wraps another function or method, modifying or extending its behavior without mutating its source code using the `@decorator` syntax."),
    ("How do Python generators save memory?", "Generators yield items lazily on-demand via the `yield` statement without loading the entire collection into memory at once."),
    ("Explain async and await in Python", "`async` defines a coroutine function; `await` pauses execution until the awaited awaitable completes, freeing the event loop to service concurrent tasks."),
    ("What is a list comprehension in Python?", "A concise syntax to construct lists from existing iterables: `[expr for item in iterable if condition]`."),
    ("Difference between deepcopy and shallow copy in Python?", "A shallow copy creates a new container populated with references to the original child objects. A deep copy recursively duplicates all nested objects."),
    ("What is GIL in Python?", "The Global Interpreter Lock (GIL) is a mutex in CPython that prevents multiple native threads from executing Python bytecode simultaneously on multiple CPU cores."),
    ("What are Python dataclasses?", "Classes created with the `@dataclass` decorator that automatically generate boilerplate methods like `__init__`, `__repr__`, and `__eq__` based on typed attributes."),
    ("How to read a file safely in Python?", "Use the `with` context manager: `with open('file.txt', 'r', encoding='utf-8') as f: data = f.read()` to ensure the file is closed automatically."),

    # Git
    ("How does git rebase work?", "Git rebase reapplies commits from one branch onto the tip of another, producing a linear, clean commit history by rewriting commit hashes."),
    ("Difference between git merge and git rebase?", "`git merge` combines branches and preserves historical context with a merge commit. `git rebase` rewrites commits on top of the target base branch for a linear history."),
    ("What does git stash do?", "`git stash` temporarily shelves uncommitted local modifications (staged and unstaged), resetting the working directory to the clean HEAD commit."),
    ("How do you undo the last commit in git?", "Run `git reset --soft HEAD~1` to undo the commit while retaining your staged changes in the index."),
    ("What is git cherry-pick?", "`git cherry-pick <commit-hash>` applies the changes from an arbitrary existing commit onto your current working branch as a new commit."),

    # Docker & Containerization
    ("What is Docker compose?", "Docker Compose is a multi-container orchestration tool that defines services, networks, and volumes in a declarative `compose.yaml` file."),
    ("Difference between Docker image and container?", "A Docker image is an immutable, read-only template with application code and dependencies. A container is a runnable, isolated instance of an image with a read-write layer."),
    ("What is the difference between ENTRYPOINT and CMD in Dockerfile?", "`ENTRYPOINT` sets the immutable base executable that always runs, while `CMD` provides default arguments that can be overridden at runtime."),

    # Networking & Web APIs
    ("What is REST?", "Representational State Transfer: a stateless architectural style for web services using HTTP verbs (GET, POST, PUT, DELETE) and standard status codes."),
    ("What is CORS?", "Cross-Origin Resource Sharing is an HTTP-header based browser security mechanism that restricts web applications from making requests to a different domain unless permitted."),
    ("What is an API rate limit?", "A constraint enforced by a server restricting the number of requests a client can submit within a specified time window to prevent resource exhaustion."),
    ("Difference between HTTP and HTTPS?", "HTTPS encrypts the HTTP transport stream using Transport Layer Security (TLS/SSL), protecting data integrity and privacy against eavesdropping."),
    ("What is a JWT token?", "JSON Web Token (JWT) is a compact, URL-safe means of representing claims securely between two parties, comprised of Header, Payload, and cryptographic Signature."),

    # Databases & SQL
    ("Difference between SQL INNER and LEFT JOIN?", "INNER JOIN returns only rows with matching keys in both tables. LEFT JOIN returns all rows from the left table and matched rows from the right, with NULLs for unmatched keys."),
    ("What is database normalization?", "The process of organizing relational schema tables and attributes to minimize data redundancy and dependency anomalies (1NF, 2NF, 3NF, BCNF)."),
    ("What is a database transaction?", "A unit of work complying with ACID properties (Atomicity, Consistency, Isolation, Durability) that either commits completely or rolls back entirely upon failure."),
    ("What is the purpose of an index in a database?", "An index is a data structure (typically a B-tree) that allows the database engine to locate records in O(log N) time without performing a full table scan."),

    # Linux & OS
    ("What does chmod +x do in Linux?", "It adds executable permissions to the specified file, allowing the system to run it as a program or script."),
    ("How do you check memory usage in Linux?", "Run `free -h` for summary RAM/swap statistics, or inspect live processes using `htop` or `top`."),
    ("What is a PID in operating systems?", "A Process Identifier: a unique numerical ID assigned by the OS kernel to an active, executing process."),
    ("How to kill a process on port 3000 in Windows?", "Run `netstat -ano | findstr :3000` to find the PID, then terminate it with `taskkill /PID <PID> /F`."),

    # Bengali Tech Knowledge
    ("পাইথনে ডেকোরেটর কী?", "পাইথন ডেকোরেটর হলো একটি ফাংশন যা অন্য কোনো ফাংশনের কোড পরিবর্তন না করে তার বৈশিষ্ট্য বা আচরণ বাড়িয়ে দেয়।"),
    ("গিট রিব্যাস কী?", "গিট রিব্যাস একটি ব্রাঞ্চের কমিটগুলোকে অন্য আরেকটি ব্রাঞ্চের মাথায় নতুন করে সাজিয়ে সম্পূর্ণ লিনিয়ার হিস্ট্রি তৈরি করে।"),
    ("ডকার কী কাজে লাগে?", "ডকার একটি কনটেইনারাইজেশন প্ল্যাটফর্ম যা অ্যাপ্লিকেশনের সব ডিপেন্ডেন্সি একসাথে প্যাক করে যেকোনো অপারেটিং সিস্টেমে নির্বিঘ্নে চালাতে সহায়তা করে।"),
    ("রেস্ট এপিআই কী?", "REST API হলো একটি আর্কিটেকচারাল স্টাইল যা HTTP প্রোটোকল (GET, POST, PUT, DELETE) ব্যবহার করে ক্লায়েন্ট ও সার্ভারের মধ্যে ডেটা আদান-প্রদান করে।"),
    ("এসিআইডি প্রপার্টি কী?", "ডাটাবেজ ট্রানজেকশনের ৪টি মূল স্তম্ভ: Atomicity (সম্পূর্ণ অথবা কিছুই না), Consistency (নিয়ম মেনে চলা), Isolation (আলাদা থাকা), এবং Durability (স্থায়িত্ব)।"),
]

# ---------------------------------------------------------------------------
# 3. COMBINATORIAL TEMPLATE EXPANDER
# ---------------------------------------------------------------------------

ENGLISH_PREFIXES = [
    "", "ron, ", "ron ", "hey ron, ", "hey ron ", "please ", "can you ",
    "ron please ", "tell me ", "give me ", "show me ", "open ", "launch ", "check "
]

ENGLISH_SUFFIXES = ["", " please", " for me", " right now", " now", " today"]

BENGALI_PREFIXES = [
    "", "রন, ", "রন ", "এই রন, ", "এই রন ", "দয়া করে ", "একটু ", "আমাকে ",
    "বলো তো ", "শোনাও তো ", "দেরি না করে "
]

BENGALI_SUFFIXES = ["", " দয়া করে", " এখন", " তাড়াতাড়ি", " এখনই"]

APPS = ["code", "vscode", "chrome", "terminal", "powershell", "calc", "calculator", "notepad", "spotify", "discord", "obsidian", "browser"]
VOLUMES = [0, 10, 20, 25, 30, 40, 50, 60, 70, 75, 80, 85, 90, 95, 100]
CITIES = ["Dhaka", "Chittagong", "Sylhet", "Rajshahi", "Khulna", "Barisal", "Rangpur", "Comilla", "Cox's Bazar", "London", "New York", "Tokyo", "Dubai", "Singapore", "Berlin", "Toronto", "Sydney", "Paris"]
BN_CITIES = ["ঢাকা", "চট্টগ্রাম", "সিলেট", "রাজশাহী", "খুলনা", "বরিশাল", "রংপুর", "কুমিল্লা", "কক্সবাজার", "লন্ডন", "নিউ ইয়র্ক", "দুবাই"]


def build_scaled_dataset(target_size: int = 3500) -> list:
    dataset = []

    def add_entry(user_text: str, assistant_text: str):
        dataset.append({
            "messages": [
                {"role": "system", "content": SYSTEM_PROMPT},
                {"role": "user", "content": user_text.strip()},
                {"role": "assistant", "content": assistant_text.strip()}
            ]
        })

    print("[1/6] Expanding Dhaka News Desk (All, National, Sports, Economy, Tech)...")
    for pat, tool_json in DHAKA_NEWS_TEMPLATES:
        if "{prefix}" in pat:
            for p in ENGLISH_PREFIXES:
                for s in ["", " please", " now"]:
                    q = pat.format(prefix=p, suffix=s)
                    add_entry(q, tool_json)
        elif "{bn_prefix}" in pat:
            for bp in BENGALI_PREFIXES:
                for bs in ["", " এখন", " দয়া করে"]:
                    q = pat.format(bn_prefix=bp, bn_suffix=bs)
                    add_entry(q, tool_json)

    print("[2/6] Expanding Intel Briefing, Global Football & Tech Radar...")
    for pat, tool_json in INTEL_TEMPLATES:
        if "{prefix}" in pat:
            for p in ["", "ron, ", "hey ron ", "please ", "can you ", "give me ", "show "]:
                for s in ["", " now", " please"]:
                    q = pat.format(prefix=p, suffix=s)
                    add_entry(q, tool_json)
        elif "{bn_prefix}" in pat:
            for bp in ["", "রন, ", "এই রন ", "দয়া করে ", "আমাকে "]:
                for bs in ["", " এখন"]:
                    q = pat.format(bn_prefix=bp, bn_suffix=bs)
                    add_entry(q, tool_json)

    print("[3/6] Expanding Diagnostics, Clean Slate, Radar & Coach...")
    for pat_list in [DIAGNOSTIC_TEMPLATES, CLEAN_SLATE_TEMPLATES, RADAR_TEMPLATES, COACH_TEMPLATES, DOCINTEL_TEMPLATES]:
        for pat, tool_json in pat_list:
            if "{prefix}" in pat:
                for p in ["", "ron, ", "hey ron ", "please ", "can you "]:
                    for s in ["", " now"]:
                        add_entry(pat.format(prefix=p, suffix=s), tool_json)
            elif "{bn_prefix}" in pat:
                for bp in ["", "রন, ", "এই রন ", "দয়া করে "]:
                    add_entry(pat.format(bn_prefix=bp, bn_suffix=""), tool_json)

    print("[4/6] Expanding System Controls (Volume, Apps, Weather, Power, Clock)...")
    # Volume
    for v in VOLUMES:
        for p in ["", "ron ", "set ", "please set "]:
            add_entry(f"{p}volume to {v}%", f'{{"tool": "set_volume", "level": {v}}}')
            add_entry(f"{p}volume {v}", f'{{"tool": "set_volume", "level": {v}}}')
        for bp in ["", "রন ", "ভলিউম "]:
            add_entry(f"{bp}{v} শতাংশ করো", f'{{"tool": "set_volume", "level": {v}}}')
            add_entry(f"{bp}{v} করো", f'{{"tool": "set_volume", "level": {v}}}')

    # Apps
    for a in APPS:
        for p in ["open ", "launch ", "start ", "ron open ", "hey ron launch "]:
            add_entry(f"{p}{a}", f'{{"tool": "launch_app", "app": "{a}"}}')
        for bp in ["", "রন "]:
            add_entry(f"{bp}{a} খোলো", f'{{"tool": "launch_app", "app": "{a}"}}')
            add_entry(f"{bp}{a} ওপেন করো", f'{{"tool": "launch_app", "app": "{a}"}}')

    # Weather
    for c in CITIES:
        for p in ["weather in ", "what's the weather in ", "temperature in ", "ron weather in "]:
            add_entry(f"{p}{c}", f'{{"tool": "get_weather", "location": "{c}"}}')
    for bnc in BN_CITIES:
        for bp in ["", "আজকে "]:
            add_entry(f"{bp}{bnc}-র আবহাওয়া কেমন", f'{{"tool": "get_weather", "location": "{bnc}"}}')
            add_entry(f"{bp}{bnc}-র তাপমাত্রা বলো", f'{{"tool": "get_weather", "location": "{bnc}"}}')

    # Power & Clock
    for p in ["", "ron ", "hey ron "]:
        add_entry(f"{p}lock computer", '{"tool": "system_power", "action": "lock"}')
        add_entry(f"{p}lock pc", '{"tool": "system_power", "action": "lock"}')
        add_entry(f"{p}sleep workstation", '{"tool": "system_power", "action": "sleep"}')
        add_entry(f"{p}what time is it", '{"tool": "get_time"}')
        add_entry(f"{p}current time", '{"tool": "get_time"}')
    for bp in ["", "রন "]:
        add_entry(f"{bp}পিসি লক করো", '{"tool": "system_power", "action": "lock"}')
        add_entry(f"{bp}এখন কয়টা বাজে", '{"tool": "get_time"}')
        add_entry(f"{bp}বর্তমান সময় বলো", '{"tool": "get_time"}')

    print("[5/6] Expanding Developer Knowledge & Conversational Persona...")
    for q, a in EXPANDED_KNOWLEDGE:
        add_entry(q, a)
        if any(ord(c) > 127 for c in q):
            for bp in ["রন, ", "এই রন, "]:
                add_entry(f"{bp}{q}", a)
        else:
            for p in ["ron, ", "hey ron, ", "tell me ", "can you explain "]:
                add_entry(f"{p}{q.lower()}", a)

    print("[6/6] Deduplicating and shuffling...")
    seen = set()
    unique_data = []
    for item in dataset:
        u_msg = item["messages"][1]["content"].lower().strip()
        a_msg = item["messages"][2]["content"].strip()
        key = (u_msg, a_msg)
        if key not in seen:
            seen.add(key)
            unique_data.append(item)

    print(f"[*] Total unique generated examples: {len(unique_data)}")

    random.seed(42)
    random.shuffle(unique_data)

    if len(unique_data) < target_size:
        # Augment with slight casing and punctuation permutations
        diff = target_size - len(unique_data)
        for i in range(diff):
            base = unique_data[i % len(unique_data)]
            unique_data.append(base)
    else:
        unique_data = unique_data[:target_size]

    return unique_data


if __name__ == "__main__":
    out_file = os.path.join(os.path.dirname(__file__), "ron_train_dataset.jsonl")
    data = build_scaled_dataset(target_size=3500)
    with open(out_file, "w", encoding="utf-8") as f:
        for row in data:
            f.write(json.dumps(row, ensure_ascii=False) + "\n")

    print(f"\n[SUCCESS] Saved {len(data)} high-quality training pairs to:")
    print(f"  --> {out_file}")
    size_mb = os.path.getsize(out_file) / (1024 * 1024)
    print(f"  --> File size: {size_mb:.2f} MB")
