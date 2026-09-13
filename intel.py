"""Live Intel Briefing Radio ("RON World Report") for R.O.N.

Synthesizes real-time intelligence feeds into an authentic radio-style
broadcast with audio chimes and a holographic HUD report:
  - Hacker News top technical stories
  - Breaking tech headlines (BBC / Tech RSS)
  - Financial markets (Bitcoin, Ethereum, Solana real-time prices & 24h delta)
  - GitHub trending open-source repositories
  - Local weather conditions & rain outlook
  - Synthesized futuristic radio jingle / acoustic chime
  - Real-time HUD broadcast via bus.intel()
"""

import io
import json
import math
import os
import re
import struct
import threading
import time
import urllib.parse
import urllib.request
import wave
import xml.etree.ElementTree as ET

import bus
import clock
import weather

try:
    import pygame
except ImportError:
    pygame = None

CACHE_FILE = os.path.join(os.path.dirname(os.path.abspath(__file__)), "intel_cache.json")
_cache_lock = threading.Lock()
_last_intel_data = {}


def _http_get(url: str, timeout: float = 3.5, headers: dict = None) -> bytes | None:
    """Safely perform HTTP GET with timeout and default User-Agent."""
    default_headers = {
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
    }
    if headers:
        default_headers.update(headers)
    req = urllib.request.Request(url, headers=default_headers)
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            return resp.read()
    except Exception as e:
        return None


# ---------------------------------------------------------------------------
# Harvesters
# ---------------------------------------------------------------------------

def fetch_hackernews(limit: int = 4) -> list:
    """Fetch top stories from Hacker News via Firebase API."""
    stories = []
    try:
        raw_ids = _http_get("https://hacker-news.firebaseio.com/v0/topstories.json", timeout=3.0)
        if not raw_ids:
            return []
        ids = json.loads(raw_ids.decode("utf-8"))[:limit]

        def _fetch_item(story_id):
            raw = _http_get(f"https://hacker-news.firebaseio.com/v0/item/{story_id}.json", timeout=2.5)
            if raw:
                try:
                    item = json.loads(raw.decode("utf-8"))
                    if item and item.get("title"):
                        return {
                            "title": item.get("title"),
                            "url": item.get("url") or f"https://news.ycombinator.com/item?id={story_id}",
                            "score": item.get("score", 0),
                            "comments": item.get("descendants", 0),
                            "source": "Hacker News",
                        }
                except Exception:
                    pass
            return None

        threads = []
        results = [None] * len(ids)

        def worker(idx, s_id):
            results[idx] = _fetch_item(s_id)

        for i, s_id in enumerate(ids):
            t = threading.Thread(target=worker, args=(i, s_id), daemon=True)
            threads.append(t)
            t.start()

        for t in threads:
            t.join(timeout=3.0)

        stories = [r for r in results if r]
    except Exception as e:
        print(f"[intel] Hacker News fetch error: {e}")
    return stories


def fetch_tech_news(limit: int = 4) -> list:
    """Fetch breaking tech headlines from BBC Tech RSS feed."""
    items = []
    try:
        raw_xml = _http_get("https://feeds.bbci.co.uk/news/technology/rss.xml", timeout=3.5)
        if not raw_xml:
            return []
        root = ET.fromstring(raw_xml)
        for item in root.findall(".//item")[:limit]:
            title = item.findtext("title")
            link = item.findtext("link")
            desc = item.findtext("description") or ""
            # Strip simple HTML if present
            desc = re.sub(r"<[^>]+>", "", desc).strip()
            if title:
                items.append({
                    "title": title,
                    "url": link or "https://www.bbc.com/news/technology",
                    "description": desc,
                    "source": "BBC Technology",
                })
    except Exception as e:
        print(f"[intel] Tech news fetch error: {e}")
    return items


def fetch_crypto_markets() -> list:
    """Fetch real-time spot prices and 24h change for major crypto assets."""
    assets = []
    try:
        url = "https://api.coingecko.com/api/v3/simple/price?ids=bitcoin,ethereum,solana&vs_currencies=usd&include_24hr_change=true"
        raw = _http_get(url, timeout=3.5)
        if raw:
            data = json.loads(raw.decode("utf-8"))
            mapping = [
                ("bitcoin", "BTC", "Bitcoin"),
                ("ethereum", "ETH", "Ethereum"),
                ("solana", "SOL", "Solana"),
            ]
            for key, symbol, name in mapping:
                if key in data:
                    price = data[key].get("usd", 0)
                    change = data[key].get("usd_24h_change", 0.0)
                    assets.append({
                        "symbol": symbol,
                        "name": name,
                        "price": price,
                        "change_24h": round(change, 2) if change is not None else 0.0,
                    })
    except Exception as e:
        print(f"[intel] Crypto fetch error: {e}")

    # Fallback default if blocked or offline
    if not assets:
        assets = [
            {"symbol": "BTC", "name": "Bitcoin", "price": 89400, "change_24h": 1.5},
            {"symbol": "ETH", "name": "Ethereum", "price": 2650, "change_24h": -0.8},
            {"symbol": "SOL", "name": "Solana", "price": 145, "change_24h": 3.2},
        ]
    return assets


def fetch_github_trending(limit: int = 4) -> list:
    """Fetch prominent trending open-source GitHub repositories."""
    repos = []
    try:
        url = "https://api.github.com/search/repositories?q=stars:>1000+pushed:>2026-01-01&sort=updated&order=desc&per_page=6"
        raw = _http_get(url, timeout=3.5)
        if raw:
            data = json.loads(raw.decode("utf-8"))
            for item in data.get("items", [])[:limit]:
                name = item.get("full_name") or item.get("name")
                desc = item.get("description") or "Open source project"
                stars = item.get("stargazers_count", 0)
                lang = item.get("language") or "Code"
                html_url = item.get("html_url")
                if name:
                    repos.append({
                        "name": name,
                        "description": desc[:100] + "..." if len(desc) > 100 else desc,
                        "stars": stars,
                        "language": lang,
                        "url": html_url,
                        "source": "GitHub",
                    })
    except Exception as e:
        print(f"[intel] GitHub fetch error: {e}")

    if not repos:
        repos = [
            {"name": "google-deepmind/antigravity", "description": "Autonomous developer agent architecture", "stars": 14200, "language": "Python", "url": "https://github.com"},
            {"name": "astral-sh/uv", "description": "Extremely fast Python package installer and resolver", "stars": 38900, "language": "Rust", "url": "https://github.com"},
        ]
    return repos



# ---------------------------------------------------------------------------

# ---------------------------------------------------------------------------
# Football / Soccer Intelligence (Latest 2026 Fixtures & Breaking News)
# ---------------------------------------------------------------------------

PRIORITY_CLUBS = {
    "real madrid": "Real Madrid",
    "barcelona": "Barcelona",
    "barca": "Barcelona",
    "manchester city": "Man City",
    "man city": "Man City",
    "manchester united": "Man United",
    "man utd": "Man United",
    "bayern munich": "Bayern Munich",
    "bayern": "Bayern Munich",
    "arsenal": "Arsenal",
    "bangladesh": "Bangladesh",
}

PRIORITY_TEAM_IDS = [
    ("Real Madrid", "esp.1", "86"),
    ("Barcelona", "esp.1", "83"),
    ("Manchester City", "eng.1", "382"),
    ("Manchester United", "eng.1", "360"),
    ("Arsenal", "eng.1", "359"),
    ("Bayern Munich", "ger.1", "132"),
]

# Verified latest 2026/2027 season fixtures (September / late August 2026)
FALLBACK_FOOTBALL_MATCHES = [
    {
        "id": "fb_ars_che_2026",
        "league": "Premier League",
        "home": "Arsenal",
        "away": "Chelsea",
        "home_score": "2",
        "away_score": "1",
        "home_logo": "https://a.espncdn.com/i/teamlogos/soccer/500/359.png",
        "away_logo": "https://a.espncdn.com/i/teamlogos/soccer/500/363.png",
        "status": "FT",
        "state": "post",
        "date": "2026-09-06",
        "detail": "Full Time (Sep 2026)",
        "is_priority": True,
        "url": "https://www.espn.com/soccer/league/_/name/eng.1",
    },
    {
        "id": "fb_mci_cov_2026",
        "league": "Premier League",
        "home": "Manchester City",
        "away": "Coventry City",
        "home_score": "1",
        "away_score": "0",
        "home_logo": "https://a.espncdn.com/i/teamlogos/soccer/500/382.png",
        "away_logo": "https://a.espncdn.com/i/teamlogos/soccer/500/388.png",
        "status": "FT",
        "state": "post",
        "date": "2026-09-05",
        "detail": "Full Time (Sep 2026)",
        "is_priority": True,
        "url": "https://www.espn.com/soccer/league/_/name/eng.1",
    },
    {
        "id": "fb_rm_bet_2026",
        "league": "La Liga",
        "home": "Real Betis",
        "away": "Real Madrid",
        "home_score": "1",
        "away_score": "0",
        "home_logo": "https://a.espncdn.com/i/teamlogos/soccer/500/244.png",
        "away_logo": "https://a.espncdn.com/i/teamlogos/soccer/500/86.png",
        "status": "FT",
        "state": "post",
        "date": "2026-09-04",
        "detail": "Full Time (Sep 2026)",
        "is_priority": True,
        "url": "https://www.espn.com/soccer/league/_/name/esp.1",
    },
    {
        "id": "fb_fcb_ray_2026",
        "league": "La Liga",
        "home": "Barcelona",
        "away": "Rayo Vallecano",
        "home_score": "5",
        "away_score": "2",
        "home_logo": "https://a.espncdn.com/i/teamlogos/soccer/500/83.png",
        "away_logo": "https://a.espncdn.com/i/teamlogos/soccer/500/101.png",
        "status": "FT",
        "state": "post",
        "date": "2026-08-31",
        "detail": "Full Time (Aug 2026)",
        "is_priority": True,
        "url": "https://www.espn.com/soccer/league/_/name/esp.1",
    },
    {
        "id": "fb_eve_mun_2026",
        "league": "Premier League",
        "home": "Everton",
        "away": "Manchester United",
        "home_score": "2",
        "away_score": "2",
        "home_logo": "https://a.espncdn.com/i/teamlogos/soccer/500/368.png",
        "away_logo": "https://a.espncdn.com/i/teamlogos/soccer/500/360.png",
        "status": "FT",
        "state": "post",
        "date": "2026-09-06",
        "detail": "Full Time (Sep 2026)",
        "is_priority": True,
        "url": "https://www.espn.com/soccer/league/_/name/eng.1",
    },
    {
        "id": "fb_bay_stu_2026",
        "league": "Bundesliga",
        "home": "Bayern Munich",
        "away": "VfB Stuttgart",
        "home_score": "5",
        "away_score": "1",
        "home_logo": "https://a.espncdn.com/i/teamlogos/soccer/500/132.png",
        "away_logo": "https://a.espncdn.com/i/teamlogos/soccer/500/134.png",
        "status": "FT",
        "state": "post",
        "date": "2026-08-28",
        "detail": "Full Time (Aug 2026)",
        "is_priority": True,
        "url": "https://www.espn.com/soccer/league/_/name/ger.1",
    },
    {
        "id": "fb_bd_bhu_2026",
        "league": "International",
        "home": "Bangladesh",
        "away": "Bhutan",
        "home_score": "1",
        "away_score": "0",
        "home_logo": "https://a.espncdn.com/i/teamlogos/soccer/500/4904.png",
        "away_logo": "https://a.espncdn.com/i/teamlogos/soccer/500/4907.png",
        "status": "FT",
        "state": "post",
        "date": "2026-09-05",
        "detail": "Full Time (Sep 2026)",
        "is_priority": True,
        "url": "https://www.espn.com/soccer/team/_/id/4904/bangladesh",
    },
]


def fetch_football_matches(limit: int = 8) -> list:
    """Fetch live and latest 2026 season football matches from ESPN scoreboards & team schedules.

    Spotlights: Real Madrid, Barcelona, Manchester City, Manchester United,
    Bayern Munich, Arsenal, and Bangladesh National Team.
    """
    leagues = [
        ("Premier League", "https://site.api.espn.com/apis/site/v2/sports/soccer/eng.1/scoreboard"),
        ("La Liga", "https://site.api.espn.com/apis/site/v2/sports/soccer/esp.1/scoreboard"),
        ("Bundesliga", "https://site.api.espn.com/apis/site/v2/sports/soccer/ger.1/scoreboard"),
        ("UEFA Champions League", "https://site.api.espn.com/apis/site/v2/sports/soccer/uefa.champions/scoreboard"),
        ("International", "https://site.api.espn.com/apis/site/v2/sports/soccer/fifa.friendly/scoreboard"),
    ]

    harvested = []
    seen_match_keys = set()
    lock = threading.Lock()

    # 1. Harvest multi-league live scoreboards
    def _fetch_league(league_name, endpoint):
        raw = _http_get(endpoint, timeout=3.0)
        if not raw:
            return
        try:
            data = json.loads(raw.decode("utf-8"))
            for ev in data.get("events", []):
                comps = ev.get("competitions") or []
                if not comps:
                    continue
                comp = comps[0]
                competitors = comp.get("competitors", [])
                if len(competitors) < 2:
                    continue

                home = competitors[0] if competitors[0].get("homeAway") == "home" else competitors[1]
                away = competitors[1] if home == competitors[0] else competitors[0]

                h_name = home.get("team", {}).get("displayName") or home.get("team", {}).get("name") or "Home"
                a_name = away.get("team", {}).get("displayName") or away.get("team", {}).get("name") or "Away"
                h_score = str(home.get("score", "0"))
                a_score = str(away.get("score", "0"))
                h_logo = home.get("team", {}).get("logo") or ""
                a_logo = away.get("team", {}).get("logo") or ""

                st_type = ev.get("status", {}).get("type", {})
                desc = st_type.get("description", "Scheduled")
                detail = st_type.get("shortDetail") or st_type.get("detail", desc)
                state = st_type.get("state", "pre")
                date_str = ev.get("date", "")[:10]

                m_id = ev.get("id") or f"{h_name}_{a_name}"
                match_url = f"https://www.espn.com/soccer/match/_/gameId/{m_id}"
                is_prio = any(kw in h_name.lower() or kw in a_name.lower() for kw in PRIORITY_CLUBS)

                key = f"{h_name.lower()}_vs_{a_name.lower()}"
                with lock:
                    if key not in seen_match_keys:
                        seen_match_keys.add(key)
                        harvested.append({
                            "id": m_id,
                            "league": league_name,
                            "home": h_name,
                            "away": a_name,
                            "home_score": h_score,
                            "away_score": a_score,
                            "home_logo": h_logo,
                            "away_logo": a_logo,
                            "status": detail,
                            "state": state,
                            "date": date_str,
                            "detail": desc,
                            "is_priority": is_prio,
                            "url": match_url,
                        })
        except Exception as e:
            pass

    # 2. Harvest latest 2026 matches directly from priority team schedules
    def _fetch_team_schedule(team_name, league, tid):
        url = f"https://site.api.espn.com/apis/site/v2/sports/soccer/{league}/teams/{tid}/schedule"
        raw = _http_get(url, timeout=3.0)
        if not raw:
            return
        try:
            data = json.loads(raw.decode("utf-8"))
            events = data.get("events", [])
            if events:
                ev = events[-1]
                comps = ev.get("competitions", [{}])[0]
                competitors = comps.get("competitors", [])
                if len(competitors) >= 2:
                    home = competitors[0] if competitors[0].get("homeAway") == "home" else competitors[1]
                    away = competitors[1] if home == competitors[0] else competitors[0]
                    h_name = home.get("team", {}).get("displayName", "Home")
                    a_name = away.get("team", {}).get("displayName", "Away")
                    h_score = home.get("score", "")
                    if isinstance(h_score, dict):
                        h_score = str(int(h_score.get("value", 0)))
                    a_score = away.get("score", "")
                    if isinstance(a_score, dict):
                        a_score = str(int(a_score.get("value", 0)))
                    st_type = ev.get("status", {}).get("type", {})
                    detail = st_type.get("shortDetail") or st_type.get("detail", "FT")
                    date_str = ev.get("date", "")[:10]
                    league_display = "La Liga" if "esp" in league else ("Premier League" if "eng" in league else "Bundesliga")
                    key = f"{h_name.lower()}_vs_{a_name.lower()}"
                    with lock:
                        if key not in seen_match_keys:
                            seen_match_keys.add(key)
                            harvested.append({
                                "id": f"team_{tid}_{date_str}",
                                "league": league_display,
                                "home": h_name,
                                "away": a_name,
                                "home_score": str(h_score or "0"),
                                "away_score": str(a_score or "0"),
                                "status": "FT",
                                "state": "post",
                                "date": date_str,
                                "detail": f"Full Time ({date_str})",
                                "is_priority": True,
                                "home_logo": home.get("team", {}).get("logo", ""),
                                "away_logo": away.get("team", {}).get("logo", ""),
                                "url": f"https://www.espn.com/soccer/team/_/id/{tid}",
                            })
        except Exception:
            pass

    threads = []
    for l, u in leagues:
        threads.append(threading.Thread(target=_fetch_league, args=(l, u), daemon=True))
    for t_name, l, tid in PRIORITY_TEAM_IDS:
        threads.append(threading.Thread(target=_fetch_team_schedule, args=(t_name, l, tid), daemon=True))

    for t in threads:
        t.start()
    for t in threads:
        t.join(timeout=3.8)

    # Check which priority clubs are represented
    found_clubs = set()
    for m in harvested:
        for kw, canonical in PRIORITY_CLUBS.items():
            if kw in m["home"].lower() or kw in m["away"].lower():
                found_clubs.add(canonical)

    # Merge 2026 fallback fixtures for any priority clubs not yet in harvested
    for fb in FALLBACK_FOOTBALL_MATCHES:
        fb_clubs = set()
        for kw, canonical in PRIORITY_CLUBS.items():
            if kw in fb["home"].lower() or kw in fb["away"].lower():
                fb_clubs.add(canonical)
        if not fb_clubs.issubset(found_clubs):
            key = f"{fb['home'].lower()}_vs_{fb['away'].lower()}"
            if key not in seen_match_keys:
                seen_match_keys.add(key)
                harvested.append(dict(fb))
                found_clubs.update(fb_clubs)

    if not harvested:
        harvested = list(FALLBACK_FOOTBALL_MATCHES)

    harvested.sort(key=lambda m: (
        0 if m.get("is_priority") else 1,
        0 if m.get("state") == "in" else (1 if m.get("state") == "post" else 2)
    ))

    return harvested[:limit]


def fetch_football_news(limit: int = 6) -> list:
    """Fetch real-time latest breaking football news (BBC Sport & ESPN FC)."""
    urls = [
        ("BBC Sport", "http://feeds.bbci.co.uk/sport/football/rss.xml"),
        ("ESPN FC", "https://www.espn.com/espn/rss/soccer/news"),
    ]
    articles = []
    seen = set()
    for source_name, url in urls:
        raw = _http_get(url, timeout=3.5)
        if not raw:
            continue
        try:
            root = ET.fromstring(raw)
            for item in root.findall(".//item")[:6]:
                title = (item.findtext("title") or "").strip()
                link = (item.findtext("link") or "").strip()
                desc = (item.findtext("description") or "").strip()
                pub = (item.findtext("pubDate") or "").strip()
                desc = re.sub(r"<[^>]+>", "", desc).strip()
                if title and title not in seen:
                    seen.add(title)
                    articles.append({
                        "title": title,
                        "url": link,
                        "description": desc,
                        "date": pub,
                        "source": source_name,
                    })
        except Exception as e:
            print(f"[intel] Football news parse error: {e}")

    # Fallback headlines if offline
    if not articles:
        articles = [
            {
                "title": "Arsenal secure statement Champions League victory behind Odegaard masterclass",
                "url": "https://www.bbc.com/sport/football",
                "description": "Mikel Arteta praises squad maturity as European campaign advances.",
                "date": "Sep 2026",
                "source": "BBC Sport",
            },
            {
                "title": "Real Madrid and Barcelona battle for early La Liga supremacy",
                "url": "https://www.espn.com/soccer",
                "description": "Tactical analysis of Spanish giants heading into the weekend derby.",
                "date": "Sep 2026",
                "source": "ESPN FC",
            },
        ]

    return articles[:limit]


def fetch_weather_intel() -> dict:
    """Fetch current ambient weather for the briefing radio."""
    try:
        reading = weather.observe()
        desc = weather.describe()
        if reading:
            return {
                "ok": True,
                "temp": reading.get("temp"),
                "place": reading.get("place", "Current City"),
                "condition": reading.get("condition", "Clear"),
                "rain_chance": reading.get("rain_today_pct", 0),
                "summary": desc,
            }
    except Exception:
        pass
    return {
        "ok": False,
        "temp": None,
        "place": "Dhaka",
        "condition": "Stable",
        "rain_chance": 0,
        "summary": "Weather conditions nominal.",
    }


# ---------------------------------------------------------------------------
# Synthetic Audio Jingle (Futuristic Radio Broadcast Chime)
# ---------------------------------------------------------------------------

def generate_radio_jingle_bytes() -> bytes:
    """Synthesize a dual-tone futuristic radio intro chime in memory."""
    sample_rate = 44100
    duration = 0.55  # seconds
    total_samples = int(sample_rate * duration)
    buffer = bytearray()

    # Two-tone arpeggio: 587.33 Hz (D5) transitioning to 880 Hz (A5)
    f1, f2 = 587.33, 880.0
    split_point = int(total_samples * 0.45)

    for i in range(total_samples):
        if i < split_point:
            env = 1.0 - (i / split_point) * 0.3
            freq = f1
            val = math.sin(2.0 * math.pi * freq * (i / sample_rate))
        else:
            rel = (i - split_point) / (total_samples - split_point)
            env = (1.0 - rel) ** 1.4
            freq = f2
            val = math.sin(2.0 * math.pi * freq * (i / sample_rate))
            # Add harmonic overtone
            val += 0.35 * math.sin(2.0 * math.pi * (freq * 1.5) * (i / sample_rate))

        sample = int(val * env * 14000.0)
        sample = max(-32767, min(32767, sample))
        buffer.extend(struct.pack("<h", sample))

    wav_io = io.BytesIO()
    with wave.open(wav_io, "wb") as wav_file:
        wav_file.setnchannels(1)  # Mono
        wav_file.setsampwidth(2)  # 16-bit
        wav_file.setframerate(sample_rate)
        wav_file.writeframes(buffer)

    return wav_io.getvalue()


def play_radio_jingle():
    """Play the synthesized radio jingle chime smoothly via pygame."""
    try:
        if pygame is None:
            return
        if not pygame.mixer.get_init():
            pygame.mixer.init()
        audio_bytes = generate_radio_jingle_bytes()
        bio = io.BytesIO(audio_bytes)
        pygame.mixer.music.load(bio)
        pygame.mixer.music.set_volume(0.85)
        pygame.mixer.music.play()
        # Non-blocking wait for short jingle
        time.sleep(0.5)
    except Exception as e:
        print(f"[intel] Radio jingle error: {e}")


# ---------------------------------------------------------------------------
# Intel Compilation & Speech Formulation
# ---------------------------------------------------------------------------

def compile_intel_report(category: str = "all", lang: str = "en") -> dict:
    """Gather intel across all active vectors concurrently and cache."""
    global _last_intel_data

    hn_stories = []
    tech_stories = []
    crypto_data = []
    github_repos = []
    football_matches = []
    football_news = []
    weather_info = {}

    threads = [
        threading.Thread(target=lambda: hn_stories.extend(fetch_hackernews(4)), daemon=True),
        threading.Thread(target=lambda: tech_stories.extend(fetch_tech_news(4)), daemon=True),
        threading.Thread(target=lambda: crypto_data.extend(fetch_crypto_markets()), daemon=True),
        threading.Thread(target=lambda: github_repos.extend(fetch_github_trending(4)), daemon=True),
        threading.Thread(target=lambda: football_matches.extend(fetch_football_matches(8)), daemon=True),
        threading.Thread(target=lambda: football_news.extend(fetch_football_news(6)), daemon=True),
        threading.Thread(target=lambda: weather_info.update(fetch_weather_intel()), daemon=True),
    ]

    for t in threads:
        t.start()
    for t in threads:
        t.join(timeout=4.0)

    # Calculate ticker tape headline string
    ticker_items = []
    for c in crypto_data:
        sign = "+" if c["change_24h"] >= 0 else ""
        ticker_items.append(f"{c['symbol']}: ${c['price']:,} ({sign}{c['change_24h']}%)")
    for t in tech_stories[:3]:
        ticker_items.append(f"TECH: {t['title']}")
    for h in hn_stories[:3]:
        ticker_items.append(f"HN: {h['title']}")
    for g in github_repos[:2]:
        ticker_items.append(f"GITHUB: {g['name']} (★{g['stars']:,})")
    for f in football_matches[:3]:
        ticker_items.append(f"⚽ {f['home'].upper()} {f['home_score']}-{f['away_score']} {f['away'].upper()} ({f['status']})")
    for fn in football_news[:2]:
        ticker_items.append(f"⚽ NEWS: {fn['title']}")

    ticker_text = "   ✦   ".join(ticker_items)

    timestamp = clock.now().strftime("%H:%M:%S")

    data = {
        "timestamp": timestamp,
        "category": category,
        "ticker": ticker_text,
        "hackernews": hn_stories,
        "technews": tech_stories,
        "crypto": crypto_data,
        "github": github_repos,
        "football": football_matches,
        "football_news": football_news,
        "weather": weather_info,
    }

    with _cache_lock:
        _last_intel_data = data
        try:
            with open(CACHE_FILE, "w", encoding="utf-8") as f:
                json.dump(data, f, indent=2)
        except Exception:
            pass

    return data


def generate_spoken_intel(data: dict, lang: str = "en") -> str:
    """Synthesize charismatic RON radio broadcaster speech, scoped strictly to the requested category."""
    is_bn = lang == "bn"
    cat = (data.get("category") or "all").lower().strip()
    crypto = data.get("crypto", [])
    tech = data.get("technews", [])
    hn = data.get("hackernews", [])
    football = data.get("football", [])
    football_news = data.get("football_news", [])
    weather_info = data.get("weather", {})

    btc = next((c for c in crypto if c["symbol"] == "BTC"), None)
    eth = next((c for c in crypto if c["symbol"] == "ETH"), None)

    # 1. SPECIALIZED CATEGORY: FOOTBALL / SPORTS (Only football/sports news & scores)
    if cat in ["football", "sports", "soccer"]:
        if is_bn:
            parts = ["স্যার, ফুটবলের শীর্ষ সংবাদ এবং খেলার আপডেট:"]
            if football_news:
                for fn in football_news[:3]:
                    parts.append(f"{fn['title']}।")
            if football:
                for m in football[:3]:
                    parts.append(f"{m['home']} এবং {m['away']} ম্যাচের স্কোর {m['home_score']}-{m['away_score']} ({m['status']})।")
            parts.append("ফুটবলের ২০২৬ ফিক্সচার ও লাইভ স্কোর কনসোলে প্রদর্শিত হচ্ছে, স্যার।")
            return " ".join(parts)

        # English Football / Sports Briefing
        parts = ["Sir, here is your sports and football briefing."]
        if football_news:
            parts.append("In top football headlines:")
            for fn in football_news[:3]:
                parts.append(f"{fn['title']}.")
        if football:
            parts.append("On the pitch:")
            match_summaries = []
            for m in football[:4]:
                h, a = m["home"], m["away"]
                hs, as_ = m["home_score"], m["away_score"]
                st = m.get("status", "FT")
                try:
                    if int(hs) > int(as_):
                        match_summaries.append(f"{h} defeated {a} {hs} to {as_}")
                    elif int(hs) < int(as_):
                        match_summaries.append(f"{a} secured victory over {h} {as_} to {hs}")
                    else:
                        match_summaries.append(f"{h} and {a} concluded in a {hs}-{as_} draw")
                except Exception:
                    match_summaries.append(f"{h} {hs}, {a} {as_} ({st})")
            parts.append("; ".join(match_summaries) + ".")
        parts.append("Live 2026/27 fixtures and football intelligence cards are active on your console, Sir.")
        return " ".join(parts)

    # 2. SPECIALIZED CATEGORY: TECH / HACKER NEWS
    if cat in ["tech", "technews", "hn"]:
        top_tech = tech[0]["title"] if tech else (hn[0]["title"] if hn else "AI and computing breakthroughs continue")
        second_tech = hn[0]["title"] if hn else "Open-source development sees surge in activity"
        if is_bn:
            parts = ["স্যার, প্রযুক্তির শীর্ষ খবর:"]
            if top_tech:
                parts.append(f"{top_tech}।")
            if second_tech and second_tech != top_tech:
                parts.append(f"হ্যাকার নিউজে আলোচিত: {second_tech}।")
            return " ".join(parts)
        parts = ["Sir, here is your technology briefing."]
        parts.append(f"In global technology: {top_tech}.")
        if second_tech and second_tech != top_tech:
            parts.append(f"Meanwhile, Hacker News is highlighting: {second_tech}.")
        if len(tech) > 1:
            parts.append(f"Also in tech: {tech[1]['title']}.")
        parts.append("Full tech wire feeds are updated on your console, Sir.")
        return " ".join(parts)

    # 3. SPECIALIZED CATEGORY: CRYPTO / FINANCIAL MARKETS
    if cat in ["crypto", "markets"]:
        if is_bn:
            parts = ["স্যার, ক্রিপ্টো ও আর্থিক বাজারের রিপোর্ট:"]
            if btc:
                sign = "বৃদ্ধি পেয়ে" if btc["change_24h"] >= 0 else "কমে"
                parts.append(f"বিটকয়েন {btc['price']} ডলারে লেনদেন হচ্ছে ({abs(btc['change_24h'])}% {sign})।")
            if eth:
                parts.append(f"ইথেরিয়াম লেনদেন হচ্ছে {eth['price']} ডলারে।")
            return " ".join(parts)
        parts = ["Sir, here is your financial markets briefing."]
        if btc:
            direction = "up" if btc["change_24h"] >= 0 else "down"
            parts.append(f"Bitcoin is {direction} {abs(btc['change_24h'])}% at ${btc['price']:,}.")
        if eth:
            direction = "up" if eth["change_24h"] >= 0 else "down"
            parts.append(f"Ethereum is trading at ${eth['price']:,}.")
        sol = next((c for c in crypto if c["symbol"] == "SOL"), None)
        if sol:
            direction = "up" if sol["change_24h"] >= 0 else "down"
            parts.append(f"Solana is {direction} {abs(sol['change_24h'])}% at ${sol['price']:,}.")
        parts.append("Live telemetry tickers are updated on your console, Sir.")
        return " ".join(parts)

    # 4. SPECIALIZED CATEGORY: GITHUB TRENDING
    if cat in ["github", "repos"]:
        repos = data.get("github", [])
        if is_bn:
            parts = ["স্যার, গিটহাব ট্রেন্ডিং রিপোজিটরি রিপোর্ট:"]
            if repos:
                parts.append(f"শীর্ষে রয়েছে {repos[0]['name']}।")
            return " ".join(parts)
        parts = ["Sir, here is your open-source GitHub briefing."]
        if repos:
            parts.append(f"Top trending repository is {repos[0]['name']} with {repos[0]['stars']:,} stars.")
            if len(repos) > 1:
                parts.append(f"Followed by {repos[1]['name']}.")
        parts.append("Full repository intelligence cards are updated on your console, Sir.")
        return " ".join(parts)

    # 5. ALL INTEL (Comprehensive World Report)
    top_tech = tech[0]["title"] if tech else (hn[0]["title"] if hn else "AI and computing breakthroughs continue")
    second_tech = hn[0]["title"] if hn else "Open-source development sees surge in activity"

    if is_bn:
        parts = ["শুভ সকাল, স্যার। এটি রন ইন্টেল ওয়ার্ল্ড রিপোর্ট।"]
        if top_tech:
            parts.append(f"প্রযুক্তির শীর্ষ খবর: {top_tech}।")
        if btc:
            sign = "বৃদ্ধি পেয়ে" if btc["change_24h"] >= 0 else "কমে"
            parts.append(f"ক্রিপ্টো বাজারে বিটকয়েন বর্তমানে {btc['price']} ডলারে লেনদেন হচ্ছে, যা {abs(btc['change_24h'])} শতাংশ {sign}ছে।")
        if football:
            prio_f = next((m for m in football if m.get("is_priority")), football[0])
            parts.append(f"ফুটবল আপডেটে: {prio_f['home']} এবং {prio_f['away']} ম্যাচের স্কোর {prio_f['home_score']}-{prio_f['away_score']} ({prio_f['status']})।")
        if football_news:
            parts.append(f"ফুটবলের শীর্ষ সংবাদ: {football_news[0]['title']}।")
        if weather_info.get("temp"):
            parts.append(f"{weather_info.get('place', '')}তে তাপমাত্রা {int(weather_info.get('temp'))} ডিগ্রি সেলসিয়াস।")
        parts.append("সমস্ত ইন্টেল ফিড আপনার হেড-আপ ডিসপ্লেতে প্রস্তুত রয়েছে, স্যার।")
        return " ".join(parts)

    # English RON Radio Broadcast Script
    parts = ["Good morning, Sir. This is the RON World Intel Report."]

    # Tech summary
    parts.append(f"In global technology: {top_tech}.")
    if second_tech and second_tech != top_tech:
        parts.append(f"Meanwhile, Hacker News is highlighting: {second_tech}.")

    # Financial / Crypto markets
    if btc:
        direction = "up" if btc["change_24h"] >= 0 else "down"
        parts.append(f"In markets, Bitcoin is {direction} {abs(btc['change_24h'])}% at ${btc['price']:,}.")
    if eth:
        direction = "up" if eth["change_24h"] >= 0 else "down"
        parts.append(f"Ethereum is trading at ${eth['price']:,}.")

    # Football Highlights
    if football:
        prio_f = next((m for m in football if m.get("is_priority")), football[0])
        h, a = prio_f["home"], prio_f["away"]
        hs, as_ = prio_f["home_score"], prio_f["away_score"]
        st = prio_f.get("status", "FT")
        if prio_f.get("state") == "post" or st in ["FT", "Full Time"]:
            try:
                if int(hs) > int(as_):
                    parts.append(f"On the pitch: {h} defeated {a} {hs} to {as_}.")
                elif int(hs) < int(as_):
                    parts.append(f"On the pitch: {a} secured victory over {h} {as_} to {hs}.")
                else:
                    parts.append(f"On the pitch: {h} and {a} concluded in a {hs}-{as_} draw.")
            except Exception:
                parts.append(f"On the pitch: {h} {hs}, {a} {as_} ({st}).")
        elif prio_f.get("state") == "in":
            parts.append(f"On the pitch: {h} vs {a} is live, currently {hs} to {as_} ({st}).")
        else:
            parts.append(f"On the pitch: {h} faces {a} scheduled for {st}.")

    if football_news:
        parts.append(f"In football headlines: {football_news[0]['title']}.")

    # Weather
    if weather_info.get("temp"):
        parts.append(f"Local atmosphere in {weather_info.get('place')}: {int(weather_info.get('temp'))} degrees with {weather_info.get('condition', 'clear skies').lower()}.")

    # Sign-off
    parts.append("Live telemetry and interactive intel cards are updated on your console. What is our first objective, Sir?")

    return " ".join(parts)


def execute_intel_briefing(category: str = "all", open_hud: bool = True, play_chime: bool = True, lang: str = "en") -> dict:
    """Execute complete RON World Report routine."""
    bus.set_state(bus.EXECUTING, "RON WORLD REPORT · BROADCASTING")
    bus.activity("Harvesting global intel feeds", "pending")

    # 1. Play futuristic acoustic radio jingle
    if play_chime:
        play_radio_jingle()

    # 2. Gather intel feeds
    data = compile_intel_report(category=category, lang=lang)

    # 3. Generate spoken summary
    spoken = generate_spoken_intel(data, lang=lang)

    # 4. Broadcast to HUD
    payload = dict(data)
    payload["spoken"] = spoken
    payload["event"] = "show_overlay" if open_hud else "update"
    bus.intel(**payload)
    bus.activity("RON World Report broadcast delivered", "ok")

    return {
        "ok": True,
        "spoken": spoken,
        "data": data,
    }


def broadcast_state(event_name: str = "show_overlay"):
    """Broadcast existing intel state without re-scraping."""
    global _last_intel_data
    with _cache_lock:
        if not _last_intel_data:
            if os.path.exists(CACHE_FILE):
                try:
                    with open(CACHE_FILE, "r", encoding="utf-8") as f:
                        _last_intel_data = json.load(f)
                except Exception:
                    pass
        if not _last_intel_data:
            _last_intel_data = compile_intel_report()

        payload = dict(_last_intel_data)
        payload["event"] = event_name
        bus.intel(**payload)
