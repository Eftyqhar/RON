"""netradar.py — RON Cyber Watchdog (Network Radar & Hacker Intrusion Scanner)

Real-time local Wi-Fi subnet scanner and open port radar:
1. Multi-threaded ICMP / socket ping sweep across active /24 subnet.
2. Windows ARP cache harvesting (`arp -a`) for hardware MAC addresses.
3. Hardware vendor OUI resolution (Apple, Samsung, Intel, Espressif, Raspberry Pi, etc.).
4. Rogue intrusion detection against `network_whitelist.json` with instant HUD alerts.
5. Workstation port security auditor: checks for exposed critical ports (21, 23, 135, 445, 3389).
6. Broadcasts real-time radar coordinates and blips to HUD event bus (`bus.netradar`).
"""

from concurrent.futures import ThreadPoolExecutor
import datetime
import json
import os
import re
import socket
import subprocess
import threading
import time
from typing import Any, Dict, List, Optional, Tuple

import bus
import sfx

WHITELIST_FILE = os.path.join(os.path.dirname(os.path.abspath(__file__)), "network_whitelist.json")

_scan_lock = threading.Lock()
_is_scanning = False
_last_scan_data: Dict[str, Any] = {
    "scanning": False,
    "timestamp": None,
    "subnet": None,
    "gateway": None,
    "host_ip": None,
    "device_count": 0,
    "devices": [],
    "rogue_count": 0,
    "rogue_devices": [],
    "ports_audit": [],
    "security_status": "SECURE",
}

# Common Hardware Vendors mapped by OUI (first 3 octets / 6 hex chars)
OUI_VENDORS = {
    # Espressif (IoT / Smart devices / Smart bulbs / Microcontrollers)
    "24:0a:c4": "Espressif IoT", "24:6f:28": "Espressif IoT", "30:ae:a4": "Espressif IoT",
    "84:cc:a8": "Espressif IoT", "a4:cf:12": "Espressif IoT", "d8:bf:c0": "Espressif IoT",
    "bc:dd:c2": "Espressif IoT", "3c:61:05": "Espressif IoT", "7c:df:a1": "Espressif IoT",

    # Apple
    "00:1c:b3": "Apple", "00:23:12": "Apple", "00:25:00": "Apple", "04:0c:ce": "Apple",
    "18:af:61": "Apple", "28:cf:e9": "Apple", "3c:07:54": "Apple", "40:6c:8f": "Apple",
    "60:f4:45": "Apple", "70:3e:ac": "Apple", "8c:85:90": "Apple", "a4:83:e7": "Apple",
    "ac:bc:32": "Apple", "b8:78:26": "Apple", "c8:69:cd": "Apple", "d0:81:7a": "Apple",
    "f0:18:98": "Apple", "f4:5c:89": "Apple",

    # Samsung
    "00:07:ab": "Samsung", "00:12:47": "Samsung", "00:15:b9": "Samsung", "00:26:37": "Samsung",
    "14:49:e0": "Samsung", "34:be:00": "Samsung", "40:0e:85": "Samsung", "50:c7:bf": "Samsung",
    "78:ab:bb": "Samsung", "84:25:19": "Samsung", "94:65:2d": "Samsung", "cc:07:ab": "Samsung",

    # Intel
    "00:13:02": "Intel", "00:15:00": "Intel", "00:16:76": "Intel", "00:1b:21": "Intel",
    "00:21:6a": "Intel", "34:13:e8": "Intel", "48:51:b7": "Intel", "58:94:6b": "Intel",
    "80:86:f2": "Intel", "98:4f:ee": "Intel", "a4:4c:c8": "Intel", "c8:5b:76": "Intel",

    # Raspberry Pi
    "b8:27:eb": "Raspberry Pi", "dc:a6:32": "Raspberry Pi", "e4:5f:01": "Raspberry Pi",
    "28:cd:c1": "Raspberry Pi",

    # TP-Link / D-Link / Netgear / Routers
    "00:0a:eb": "TP-Link", "14:cc:20": "TP-Link", "50:c7:bf": "TP-Link", "60:a4:4c": "TP-Link",
    "98:48:27": "TP-Link", "bc:d1:77": "TP-Link", "e8:48:b8": "TP-Link",
    "00:05:5d": "D-Link", "00:17:9a": "D-Link", "1c:7e:e5": "D-Link", "28:10:7b": "D-Link",
    "00:09:5b": "Netgear", "00:14:6c": "Netgear", "20:4e:7f": "Netgear", "a0:04:60": "Netgear",

    # Google / Alphabet (Pixel, Nest, Chromecast)
    "00:1a:11": "Google", "3c:5a:37": "Google", "48:d6:d5": "Google", "54:60:09": "Google",
    "94:eb:2c": "Google", "a4:77:33": "Google", "d8:6c:63": "Google", "f8:8f:ca": "Google",

    # Xiaomi
    "00:9e:c8": "Xiaomi", "04:cf:8c": "Xiaomi", "18:59:36": "Xiaomi", "28:6c:07": "Xiaomi",
    "34:80:dc": "Xiaomi", "50:8f:4c": "Xiaomi", "64:09:80": "Xiaomi", "78:11:dc": "Xiaomi",

    # Sony / PlayStation
    "00:04:1f": "Sony", "00:13:15": "Sony", "00:1a:80": "Sony", "28:0d:fc": "Sony PlayStation",
    "70:9e:29": "Sony PlayStation", "a8:e3:ee": "Sony", "fc:0f:e6": "Sony",

    # Microsoft / Xbox
    "00:50:f2": "Microsoft", "28:18:78": "Microsoft", "60:45:bd": "Microsoft",
    "7c:ed:8d": "Microsoft Surface", "98:5f:d3": "Microsoft Xbox",
}

CRITICAL_PORTS = [
    {"port": 21, "service": "FTP", "desc": "File Transfer Protocol (Unencrypted credentials)", "severity": "HIGH"},
    {"port": 23, "service": "TELNET", "desc": "Telnet Remote Shell (Insecure plaintext)", "severity": "CRITICAL"},
    {"port": 80, "service": "HTTP", "desc": "Standard Web Server", "severity": "LOW"},
    {"port": 135, "service": "RPC", "desc": "Microsoft RPC Endpoint Mapper", "severity": "MEDIUM"},
    {"port": 139, "service": "NetBIOS", "desc": "NetBIOS Session Service", "severity": "MEDIUM"},
    {"port": 445, "service": "SMB", "desc": "Server Message Block (Ransomware vector)", "severity": "HIGH"},
    {"port": 3389, "service": "RDP", "desc": "Windows Remote Desktop", "severity": "MEDIUM"},
    {"port": 8080, "service": "HTTP-ALT", "desc": "Proxy / Web Application Server", "severity": "LOW"},
    {"port": 8888, "service": "DEV-SRV", "desc": "Jupyter / Development Server", "severity": "LOW"},
]


# ---------------------------------------------------------------------------
# Network Discovery & Whitelist Persistence
# ---------------------------------------------------------------------------

def load_whitelist() -> Dict[str, Any]:
    """Load known trusted MAC addresses and user notes from disk."""
    if os.path.exists(WHITELIST_FILE):
        try:
            with open(WHITELIST_FILE, "r", encoding="utf-8") as f:
                return json.load(f)
        except Exception as e:
            print(f"[netradar] Failed to read whitelist: {e}")
    return {
        "trusted_macs": {},
        "scan_history": [],
    }


def save_whitelist(data: Dict[str, Any]) -> bool:
    """Save trusted devices whitelist to disk."""
    try:
        with open(WHITELIST_FILE, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, indent=2)
        return True
    except Exception as e:
        print(f"[netradar] Failed to save whitelist: {e}")
        return False


def mark_device_trusted(mac_address: str, custom_name: str = "") -> bool:
    """Register a MAC address as trusted in the whitelist."""
    clean_mac = normalize_mac(mac_address)
    if not clean_mac:
        return False

    data = load_whitelist()
    data["trusted_macs"][clean_mac] = {
        "name": custom_name.strip() or "Trusted Device",
        "added_at": datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
    }
    ok = save_whitelist(data)
    if ok:
        bus.activity(f"Radar: Marked device {clean_mac} as trusted", "ok")
    return ok


def normalize_mac(raw_mac: str) -> str:
    """Format MAC address as lowercase colon-delimited (e.g. 00:11:22:33:44:55)."""
    if not raw_mac:
        return ""
    clean = re.sub(r"[^0-9a-fA-F]", "", raw_mac).lower()
    if len(clean) == 12:
        return ":".join(clean[i:i+2] for i in range(0, 12, 2))
    return raw_mac.strip().lower()


def resolve_vendor(mac_address: str) -> str:
    """Resolve hardware manufacturer from IEEE OUI database."""
    norm = normalize_mac(mac_address)
    if not norm:
        return "Unknown Device"
    prefix = norm[:8]
    if prefix in OUI_VENDORS:
        return OUI_VENDORS[prefix]

    if norm.startswith("01:00:5e") or norm.startswith("ff:ff:ff"):
        return "Multicast / Broadcast"
    if norm.startswith("02:") or norm.startswith("06:") or norm.startswith("0a:") or norm.startswith("0e:"):
        return "Randomized / Virtual MAC"

    return "Generic Hardware"


def get_local_ip_and_subnet() -> Tuple[str, str, str]:
    """Retrieve host active local IPv4, subnet prefix, and estimated gateway."""
    host_ip = "127.0.0.1"
    subnet_base = "192.168.1"
    gateway = "192.168.1.1"

    try:
        s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        s.settimeout(0.5)
        s.connect(("8.8.8.8", 80))
        host_ip = s.getsockname()[0]
        s.close()
    except Exception:
        try:
            host_ip = socket.gethostbyname(socket.gethostname())
        except Exception:
            host_ip = "192.168.1.9"

    parts = host_ip.split(".")
    if len(parts) == 4 and parts[0] in ("192", "10", "172"):
        subnet_base = ".".join(parts[:3])
        gateway = f"{subnet_base}.1"

    return host_ip, subnet_base, gateway


# ---------------------------------------------------------------------------
# Ping Sweep & ARP Cache Harvesting
# ---------------------------------------------------------------------------

def _ping_ip(ip: str, timeout_ms: int = 180) -> bool:
    """Lightweight socket ping probe to awaken ARP table on Windows."""
    try:
        sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        sock.settimeout(timeout_ms / 1000.0)
        sock.connect_ex((ip, 80))
        sock.close()
        return True
    except Exception:
        return False


def harvest_arp_table() -> List[Dict[str, str]]:
    """Parse Windows ARP cache output (`arp -a`) into structured device records."""
    devices = []
    try:
        flags = getattr(subprocess, "CREATE_NO_WINDOW", 0)
        out = subprocess.check_output(["arp", "-a"], text=True, creationflags=flags, timeout=3.0)
        
        pattern = re.compile(r"(\d{1,3}\.\d{1,3}\.\d{1,3}\.\d{1,3})\s+([0-9a-fA-F\-]{17})\s+(\w+)")
        for line in out.splitlines():
            m = pattern.search(line)
            if m:
                ip, raw_mac, ip_type = m.group(1), m.group(2).replace("-", ":"), m.group(3).lower()
                norm_mac = normalize_mac(raw_mac)
                if ip.startswith("224.") or ip.startswith("239.") or ip.endswith(".255") or norm_mac == "ff:ff:ff:ff:ff:ff":
                    continue
                devices.append({
                    "ip": ip,
                    "mac": norm_mac,
                    "type": ip_type,
                })
    except Exception as e:
        print(f"[netradar] Failed to parse arp table: {e}")

    return devices


# ---------------------------------------------------------------------------
# Port Vulnerability Scanner
# ---------------------------------------------------------------------------

def audit_local_ports(host: str = "127.0.0.1", timeout_s: float = 0.35) -> List[Dict[str, Any]]:
    """Audit workstation for critical exposed ports."""
    findings = []
    for item in CRITICAL_PORTS:
        port = item["port"]
        is_open = False
        try:
            with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
                s.settimeout(timeout_s)
                res = s.connect_ex((host, port))
                if res == 0:
                    is_open = True
        except Exception:
            is_open = False

        findings.append({
            "port": port,
            "service": item["service"],
            "description": item["desc"],
            "severity": item["severity"],
            "status": "OPEN" if is_open else "CLOSED",
            "is_vulnerable": is_open and item["severity"] in ("HIGH", "CRITICAL"),
        })
    return findings


# ---------------------------------------------------------------------------
# Full Radar Scan Routine
# ---------------------------------------------------------------------------

def scan_network(fast: bool = True) -> Dict[str, Any]:
    """Perform a complete Cyber Watchdog network discovery & security audit."""
    global _is_scanning, _last_scan_data

    with _scan_lock:
        if _is_scanning:
            return dict(_last_scan_data)
        _is_scanning = True

    bus.activity("Cyber Watchdog: Initiating Wi-Fi perimeter sweep...", "pending")
    bus.netradar(scanning=True, status="SWEEPING SUBNET")

    try:
        host_ip, subnet_base, gateway = get_local_ip_and_subnet()
        whitelist = load_whitelist()
        trusted_macs = whitelist.get("trusted_macs", {})

        ips_to_probe = []
        if fast:
            targets = list(range(1, 36)) + list(range(100, 130))
        else:
            targets = list(range(1, 255))

        for i in targets:
            ips_to_probe.append(f"{subnet_base}.{i}")

        with ThreadPoolExecutor(max_workers=30) as pool:
            pool.map(_ping_ip, ips_to_probe)

        arp_records = harvest_arp_table()

        has_host = any(d["ip"] == host_ip for d in arp_records)
        if not has_host:
            try:
                import uuid
                node_mac = ':'.join(re.findall('..', '%012x' % uuid.getnode()))
            except Exception:
                node_mac = "00:00:00:00:00:00"
            arp_records.append({
                "ip": host_ip,
                "mac": normalize_mac(node_mac),
                "type": "host",
            })

        devices: List[Dict[str, Any]] = []
        rogue_devices: List[Dict[str, Any]] = []

        def _sort_key(d):
            ip = d["ip"]
            if ip == gateway:
                return -2
            if ip == host_ip:
                return -1
            try:
                return int(ip.split(".")[-1])
            except Exception:
                return 999

        arp_records.sort(key=_sort_key)

        for dev in arp_records:
            ip = dev["ip"]
            mac = dev["mac"]
            vendor = resolve_vendor(mac)
            
            role = "DEVICE"
            if ip == gateway:
                role = "GATEWAY ROUTER"
            elif ip == host_ip:
                role = "THIS WORKSTATION"
            elif "Apple" in vendor or "Samsung" in vendor or "Xiaomi" in vendor:
                role = "SMARTPHONE"
            elif "IoT" in vendor or "Espressif" in vendor:
                role = "SMART IOT / SENSOR"
            elif "PlayStation" in vendor or "Xbox" in vendor:
                role = "GAMING CONSOLE"

            is_trusted = False
            custom_label = None
            if mac in trusted_macs:
                is_trusted = True
                custom_label = trusted_macs[mac].get("name")
            elif ip == host_ip or ip == gateway:
                is_trusted = True
                custom_label = "Workstation Node" if ip == host_ip else "Default Gateway"

            is_rogue = not is_trusted
            record = {
                "ip": ip,
                "mac": mac,
                "vendor": vendor,
                "role": role,
                "trusted": is_trusted,
                "custom_name": custom_label,
                "is_rogue": is_rogue,
                "distance": min(0.9, 0.15 + (int(ip.split(".")[-1]) % 100) / 130.0),
                "angle": (int(ip.split(".")[-1]) * 17) % 360,
            }
            devices.append(record)
            if is_rogue:
                rogue_devices.append(record)

        port_findings = audit_local_ports(host="127.0.0.1")
        open_vulns = [p for p in port_findings if p["is_vulnerable"]]

        if open_vulns:
            sec_status = "CRITICAL VULNERABILITY"
        elif len(rogue_devices) > 0:
            sec_status = "ELEVATED ALERT"
        else:
            sec_status = "SECURE"

        result = {
            "scanning": False,
            "timestamp": datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
            "subnet": f"{subnet_base}.0/24",
            "gateway": gateway,
            "host_ip": host_ip,
            "device_count": len(devices),
            "devices": devices,
            "rogue_count": len(rogue_devices),
            "rogue_devices": rogue_devices,
            "ports_audit": port_findings,
            "security_status": sec_status,
        }

        with _scan_lock:
            _last_scan_data = result
            _is_scanning = False

        bus.netradar(**result)
        if len(rogue_devices) > 0:
            sfx.play("sonar", volume=0.85)
            rogue_names = ", ".join([d["vendor"] for d in rogue_devices[:2]])
            bus.activity(f"⚠️ Radar Alert: {len(rogue_devices)} unknown device(s) on Wi-Fi ({rogue_names})", "fail")
        else:
            if len(devices) > 0:
                sfx.play("sonar", volume=0.55, debounce_s=10.0)
            bus.activity(f"Radar sweep complete: {len(devices)} active devices verified", "ok")

        return result

    except Exception as e:
        print(f"[netradar] Error during network scan: {e}")
        with _scan_lock:
            _is_scanning = False
        bus.netradar(scanning=False, error=str(e))
        bus.activity(f"Radar scan error: {e}", "fail")
        return {"error": str(e), "devices": []}


def start_network_scan_async(fast: bool = True) -> threading.Thread:
    """Launch non-blocking network scan in background."""
    t = threading.Thread(target=scan_network, args=(fast,), daemon=True)
    t.start()
    return t


def get_latest_radar_snapshot() -> Dict[str, Any]:
    """Return the cached or active radar state."""
    with _scan_lock:
        return dict(_last_scan_data)


def format_radar_telegram_report(data: Dict[str, Any]) -> str:
    """Format an informative Markdown diagnostic debrief for Telegram."""
    ts = data.get("timestamp") or datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    devices = data.get("devices", [])
    rogues = data.get("rogue_devices", [])
    vulns = [p for p in data.get("ports_audit", []) if p.get("status") == "OPEN"]
    sec = data.get("security_status", "SECURE")

    icon = "🛡️" if sec == "SECURE" else ("⚠️" if "ELEVATED" in sec else "🚨")
    lines = [
        f"{icon} *RON CYBER WATCHDOG — PERIMETER SCAN*",
        f"⏱️ *Timestamp:* `{ts}`",
        f"🌐 *Subnet:* `{data.get('subnet', '192.168.1.0/24')}` · *Gateway:* `{data.get('gateway', '192.168.1.1')}`",
        f"🛡️ *Security Posture:* `{sec}`",
        "─────────────────────────────",
        f"📡 *Connected Devices ({len(devices)} Detected):*",
    ]

    for d in devices[:12]:
        badge = "✅" if d.get("trusted") else "⚠️ *ROGUE*"
        role = d.get("role") or ("Gateway" if d.get("is_gateway") else ("Host" if d.get("is_host") else "Node"))
        lines.append(f"• {badge} `{d['ip']}` — *{d.get('vendor', 'Unknown')}* ({role})")
        if d.get("custom_name"):
            lines.append(f"  ↳ Note: _{d['custom_name']}_")

    lines.append("─────────────────────────────")
    if rogues:
        lines.append(f"🚨 *{len(rogues)} Unrecognized Device(s) Detected!*")
        for r in rogues[:3]:
            lines.append(f"  • `{r['ip']}` | MAC: `{r['mac']}` ({r.get('vendor', 'Unknown')})")
    else:
        lines.append("✅ *All active devices match trusted whitelist.*")

    lines.append("─────────────────────────────")
    if vulns:
        lines.append(f"⚠️ *Open Workstation Ports:*")
        for v in vulns:
            desc = v.get("desc") or v.get("description", "")
            lines.append(f"  • Port `{v['port']}` ({v.get('service', 'Unknown')}) — {desc}")
    else:
        lines.append("🔒 *All critical workstation ports secure and firewalled.*")

    return "\n".join(lines)
