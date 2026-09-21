"""Clean Slate: Autonomous Workspace & Downloads Organizer for RON.

Delivers safe, zero-data-loss organization of cluttered user folders:
- Intelligently categorizes files into clean subfolders:
  Documents, Images, Audio, Video, Archives, Installers, Code.
- Prevents file loss with automatic non-destructive collision renaming.
- Safely skips active in-progress downloads (.crdownload, .part, .tmp)
  and Windows desktop shortcuts (.lnk, .url, desktop.ini).
- Flags stale installers (>14 days old) to help reclaim disk space.
- 0% GPU requirement (pure CPU file operations via standard library).
"""

import os
import re
import shutil
import time
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

import bus
import sfx

# Mapping extensions to category subfolder names
CATEGORIES: Dict[str, Tuple[str, ...]] = {
    "Documents": (
        ".pdf", ".docx", ".doc", ".xlsx", ".xls", ".pptx", ".ppt",
        ".txt", ".rtf", ".epub", ".csv", ".tsv", ".odt", ".pages"
    ),
    "Images": (
        ".png", ".jpg", ".jpeg", ".webp", ".gif", ".svg", ".bmp",
        ".ico", ".tiff", ".heic", ".raw", ".psd"
    ),
    "Audio": (
        ".mp3", ".wav", ".aac", ".flac", ".ogg", ".m4a", ".wma"
    ),
    "Video": (
        ".mp4", ".mkv", ".mov", ".avi", ".webm", ".flv", ".wmv"
    ),
    "Archives": (
        ".zip", ".rar", ".7z", ".tar", ".gz", ".bz2", ".xz", ".iso"
    ),
    "Installers": (
        ".exe", ".msi", ".dmg", ".pkg", ".appx"
    ),
    "Code": (
        ".py", ".js", ".ts", ".html", ".css", ".json", ".sql",
        ".xml", ".yaml", ".yml", ".sh", ".bat", ".cpp", ".c",
        ".java", ".rs", ".dart", ".go", ".php"
    ),
}

# Reverse lookup: extension -> category name
EXT_TO_CATEGORY: Dict[str, str] = {}
for cat_name, extensions in CATEGORIES.items():
    for ext in extensions:
        EXT_TO_CATEGORY[ext.lower()] = cat_name

# Subcategories specifically when organizing the Documents folder
DOCUMENTS_SUBCATEGORIES: Dict[str, Tuple[str, ...]] = {
    "PDFs": (".pdf",),
    "Word Documents": (".docx", ".doc", ".odt", ".rtf", ".pages"),
    "Spreadsheets": (".xlsx", ".xls", ".csv", ".tsv"),
    "Presentations": (".pptx", ".ppt"),
    "Notes & Text": (".txt", ".epub", ".md"),
}

DOC_EXT_TO_CATEGORY: Dict[str, str] = {}
for cat_name, extensions in DOCUMENTS_SUBCATEGORIES.items():
    for ext in extensions:
        DOC_EXT_TO_CATEGORY[ext.lower()] = cat_name

# Extensions and files that must NEVER be moved or touched
IGNORE_EXTENSIONS = {
    ".crdownload", ".part", ".tmp", ".download",
    ".lnk", ".url", ".ini",
}

IGNORE_FILES = {
    "desktop.ini", "thumbs.db", ".ds_store", ".git",
}


def resolve_folder(target: str = "downloads") -> Optional[str]:
    """Resolve absolute path for Downloads, Documents, or Desktop, respecting OneDrive redirection."""
    target_clean = (target or "").strip().lower()
    if "document" in target_clean or "doc" in target_clean:
        folder_name = "Documents"
    elif "desktop" in target_clean:
        folder_name = "Desktop"
    else:
        folder_name = "Downloads"

    try:
        import tools
        resolved = tools.resolve_user_folder(folder_name)
        if resolved and os.path.isdir(resolved):
            return resolved
    except Exception:
        pass

    home = os.path.expanduser("~")
    # Check OneDrive path first (Windows 10/11 default for Documents/Desktop)
    candidates = [
        os.path.join(home, "OneDrive", folder_name),
        os.path.join(home, folder_name),
    ]

    for path in candidates:
        if os.path.isdir(path):
            return path

    return os.path.join(home, folder_name)


def get_category_for_file(filename: str, target_name: str = "") -> Optional[str]:
    """Return category name based on file extension and target folder context."""
    _, ext = os.path.splitext(filename)
    if not ext:
        return None
    ext_lower = ext.lower()
    if target_name and "document" in target_name.lower():
        if ext_lower in DOC_EXT_TO_CATEGORY:
            return DOC_EXT_TO_CATEGORY[ext_lower]
    return EXT_TO_CATEGORY.get(ext_lower)


def _safe_destination(dest_dir: str, file_name: str) -> str:
    """Generate collision-free destination file path (e.g. filename (1).pdf)."""
    base, ext = os.path.splitext(file_name)
    candidate = os.path.join(dest_dir, file_name)
    counter = 1

    while os.path.exists(candidate):
        candidate = os.path.join(dest_dir, f"{base} ({counter}){ext}")
        counter += 1

    return candidate


def organize_directory(
    target: str = "downloads",
    dry_run: bool = False,
    lang: str = "en"
) -> Dict[str, Any]:
    """Autonomously organize top-level files in target folder into category subfolders.

    Args:
        target: 'downloads' or 'desktop'
        dry_run: If True, simulate actions without moving files.
        lang: 'en' for English or 'bn' for Bengali spoken response.

    Returns:
        Structured audit dictionary with count of moved files and spoken summary.
    """
    target_path = resolve_folder(target)
    if not target_path or not os.path.isdir(target_path):
        err = f"Target directory not found: {target}"
        return {
            "ok": False,
            "error": err,
            "target": target,
            "spoken": f"I could not find your {target} folder, Sir." if lang != "bn" else f"স্যার, {target} ফোল্ডারটি খুঁজে পাওয়া যায়নি।"
        }

    folder_label = os.path.basename(target_path.rstrip(os.sep)) or target.title()
    target_clean = (target or "").strip().lower()
    is_documents = "document" in target_clean or "document" in folder_label.lower()
    context_target = "documents" if is_documents else folder_label

    # Announce initiation on bus and acoustic feedback
    bus.set_state(bus.EXECUTING, f"CLEAN SLATE: ORGANIZING {folder_label.upper()}")
    bus.activity(f"Clean Slate: Scanning {folder_label}...", "pending")
    sfx.play("servo")

    files_scanned = 0
    files_moved = 0
    bytes_organized = 0
    category_counts: Dict[str, int] = {}
    old_installers: List[Dict[str, Any]] = []

    now = time.time()
    stale_threshold_seconds = 14 * 86400  # 14 days

    try:
        entries = list(os.scandir(target_path))
    except Exception as e:
        return {
            "ok": False,
            "error": str(e),
            "target": target,
            "spoken": f"Failed to access folder: {e}"
        }

    for entry in entries:
        # Never move subdirectories or category folders
        if not entry.is_file():
            continue

        filename = entry.name
        files_scanned += 1

        # Check ignored files / extensions
        if filename.lower() in IGNORE_FILES:
            continue

        _, ext = os.path.splitext(filename)
        ext_lower = ext.lower()

        if ext_lower in IGNORE_EXTENSIONS:
            continue

        # If targeting Desktop, also never touch shortcuts
        if folder_label.lower() == "desktop" and ext_lower in (".lnk", ".url"):
            continue

        category = get_category_for_file(filename, target_name=context_target)
        if not category:
            continue

        file_stat = entry.stat()
        file_size = file_stat.st_size

        # Check for stale installers (>14 days old)
        if category == "Installers":
            age_seconds = now - file_stat.st_mtime
            if age_seconds > stale_threshold_seconds:
                old_installers.append({
                    "name": filename,
                    "size_mb": round(file_size / (1024 * 1024), 2),
                    "days_old": int(age_seconds / 86400),
                })

        dest_category_dir = os.path.join(target_path, category)

        if not dry_run:
            os.makedirs(dest_category_dir, exist_ok=True)
            dest_file_path = _safe_destination(dest_category_dir, filename)
            try:
                shutil.move(entry.path, dest_file_path)
            except Exception as e:
                print(f"[clean_slate] Could not move {filename}: {e}")
                continue

        files_moved += 1
        bytes_organized += file_size
        category_counts[category] = category_counts.get(category, 0) + 1

    total_mb = round(bytes_organized / (1024 * 1024), 1)
    old_installers_mb = round(sum(f["size_mb"] for f in old_installers), 1)

    # Trigger sonar chime on completion
    sfx.play("sonar", volume=0.7)

    # Build spoken confirmation
    if lang == "bn":
        if files_moved == 0:
            spoken = f"স্যার, আপনার {folder_label} ফোল্ডারটি ইতিমধ্যে গোছানো আছে।"
        else:
            spoken = f"স্যার, {folder_label} ফোল্ডার পরিষ্কার করা হয়েছে। মোট {files_moved}টি ফাইল বিভিন্ন ক্যাটাগরিতে সাজানো হয়েছে।"
            if len(old_installers) > 0:
                spoken += f" এছাড়া {len(old_installers)}টি পুরোনো ইন্সটলার ফাইল চিহ্নিত করা হয়েছে।"
    else:
        if files_moved == 0:
            spoken = f"{folder_label} is already clean and organized, Sir."
        else:
            cat_summary = ", ".join([f"{count} {cat}" for cat, count in sorted(category_counts.items(), key=lambda x: -x[1])[:3]])
            spoken = f"{folder_label} organized, Sir. Sorted {files_moved} files ({total_mb} MB) across categories including {cat_summary}."
            if len(old_installers) > 0:
                spoken += f" Found {len(old_installers)} older installer setups occupying {old_installers_mb} MB that can be cleared."

    result = {
        "ok": True,
        "target": folder_label,
        "target_path": target_path,
        "files_scanned": files_scanned,
        "files_moved": files_moved,
        "bytes_organized": bytes_organized,
        "mb_organized": total_mb,
        "categories": category_counts,
        "old_installers_count": len(old_installers),
        "old_installers_mb": old_installers_mb,
        "dry_run": dry_run,
        "spoken": spoken,
    }

    # Publish to HUD bus
    bus.clean_slate(**result)
    bus.activity(f"Clean Slate: {files_moved} files sorted in {folder_label} ({total_mb} MB)", "ok")
    bus.set_state(bus.IDLE, "SYSTEM READY")

    return result
