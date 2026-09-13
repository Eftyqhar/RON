"""AI image generation for RON -- multi-provider engine with free high-quality APIs.

Supports:
1. Google Gemini (gemini-2.5-flash-image / imagen-3.0-generate-002) --
   Google AI Studio / Gemini API.
2. Hugging Face Serverless Inference (black-forest-labs/FLUX.1-schnell) --
   12B parameter open-weights state of the art via HF user token (HF_TOKEN).
3. Pollinations GenAI (gen.pollinations.ai) -- Updated API supporting FLUX and SDXL.
   Free key via enter.pollinations.ai (POLLINATIONS_API_KEY).
4. Pollinations Legacy (image.pollinations.ai) -- Zero-key, zero-signup instant fallback.

Configuration is loaded from config.json under "imagegen" or via environment variables.

Two rules, inherited from `weather.py` and `netspeed.py`:
1. **Nothing here ever raises into the caller.** All failures return a clean message.
2. **Nothing happens at import time.** Network is only touched inside generate_image().
"""

import base64
import json
import os
import re
import sys
import time
import urllib.parse
import urllib.request

# -- Configuration & Constants -----------------------------------------------

DEFAULT_WIDTH = 1024
DEFAULT_HEIGHT = 1024
_HTTP_TIMEOUT = 60.0
_MAX_BODY = 20 * 1024 * 1024        # bounded read: refuse a 20 MB+ response
_UA = "RON-assistant/1.0 (+https://github.com/Eftyqhar/RON)"

# 1. Google Gemini Endpoints & Models
GEMINI_OPENAI_ENDPOINT = "https://generativelanguage.googleapis.com/v1beta/openai/images/generations"
GEMINI_PREDICT_ENDPOINT = "https://generativelanguage.googleapis.com/v1beta/models/{model}:predict"
GEMINI_DEFAULT_MODEL = "gemini-2.5-flash-image"
GEMINI_FALLBACK_MODEL = "gemini-3-pro-image-preview"

# 2. Hugging Face Serverless Inference (FLUX.1-schnell)
HF_ENDPOINT = "https://router.huggingface.co/hf-inference/models/{model}"
HF_DEFAULT_MODEL = "black-forest-labs/FLUX.1-schnell"
HF_FALLBACK_MODEL = "stabilityai/stable-diffusion-xl-base-1.0"

# 3. Pollinations GenAI
POLLINATIONS_GEN_URL = (
    "https://gen.pollinations.ai/image/{prompt}"
    "?width={width}&height={height}&seed={seed}&model={model}&key={key}"
)
POLLINATIONS_GEN_DEFAULT_MODEL = "black-forest-labs/flux.1-schnell"

# 4. Pollinations Legacy (Zero-key fallback)
IMAGE_URL = (
    "https://image.pollinations.ai/prompt/{prompt}"
    "?width={width}&height={height}&seed={seed}&nologo=true&model={model}"
)
DEFAULT_MODEL = "flux"
FALLBACK_MODEL = "sana"

# Module-level state
_current_context = {}
_last_error = {}


# -- Configuration Loader & Provider Auto-Detection --------------------------

def _load_config():
    """Load imagegen settings from config.json if available, stripping comments safely."""
    try:
        cfg_path = os.path.join(os.path.dirname(__file__), "config.json")
        if os.path.exists(cfg_path):
            with open(cfg_path, "r", encoding="utf-8") as f:
                content = f.read()
                # Strip single-line comments // and #
                content = re.sub(r'^\s*//.*$', '', content, flags=re.MULTILINE)
                content = re.sub(r'^\s*#.*$', '', content, flags=re.MULTILINE)
                # Strip inline comments // ...
                content = re.sub(r'//.*$', '', content, flags=re.MULTILINE)
                data = json.loads(content)
                return data.get("imagegen", {})
    except Exception as e:
        print(f"[ImageGen config parse error: {e}]")
    return {}


def get_active_provider(provider_override=None, model_override=None):
    """Resolve the active image provider, API key, and model.

    Returns:
        (provider_name, api_key, model_name)
    """
    cfg = _load_config()
    provider = (provider_override or cfg.get("provider") or "auto").strip().lower()
    config_key = (cfg.get("api_key") or "").strip()
    config_model = (model_override or cfg.get("model") or "").strip()

    # Environment keys
    gemini_key = os.environ.get("GEMINI_API_KEY") or os.environ.get("GOOGLE_API_KEY")
    hf_key = os.environ.get("HF_TOKEN") or os.environ.get("HUGGINGFACE_API_KEY")
    polli_key = os.environ.get("POLLINATIONS_API_KEY")
    openai_key = os.environ.get("OPENAI_API_KEY")

    # If explicit provider requested
    if provider in ("gemini", "google", "imagen"):
        key = config_key or gemini_key or ""
        model = config_model or (GEMINI_DEFAULT_MODEL if not config_model.startswith("imagen-") else config_model)
        return ("gemini", key, model)

    if provider in ("huggingface", "hf", "flux"):
        key = config_key or hf_key or ""
        model = config_model or HF_DEFAULT_MODEL
        return ("huggingface", key, model)

    if provider in ("pollinations_gen", "pollinations-gen", "gen.pollinations.ai"):
        key = config_key or polli_key or ""
        model = config_model or POLLINATIONS_GEN_DEFAULT_MODEL
        return ("pollinations_gen", key, model)

    if provider in ("openai", "dalle", "dall-e"):
        key = config_key or openai_key or ""
        model = config_model or "dall-e-3"
        return ("openai", key, model)

    if provider == "legacy" or provider == "pollinations":
        if config_key or polli_key:
            return ("pollinations_gen", config_key or polli_key, config_model or POLLINATIONS_GEN_DEFAULT_MODEL)
        return ("pollinations", "", config_model or DEFAULT_MODEL)

    # AUTO MODE: select the best available free tier key
    if gemini_key or (config_key and (config_key.startswith("AIza") or config_key.startswith("AQ."))):
        return ("gemini", gemini_key or config_key, config_model or GEMINI_DEFAULT_MODEL)

    if hf_key or (config_key and config_key.startswith("hf_")):
        return ("huggingface", hf_key or config_key, config_model or HF_DEFAULT_MODEL)

    if polli_key or (config_key and (config_key.startswith("pk_") or config_key.startswith("sk_"))):
        return ("pollinations_gen", polli_key or config_key, config_model or POLLINATIONS_GEN_DEFAULT_MODEL)

    if openai_key or (config_key and config_key.startswith("sk-proj-")):
        return ("openai", openai_key or config_key, config_model or "dall-e-3")

    # If user put a key in config.json without a recognizable prefix
    if config_key:
        return ("gemini", config_key, config_model or GEMINI_DEFAULT_MODEL)

    # Zero-key fallback: Pollinations
    return ("pollinations", "", config_model or DEFAULT_MODEL)


# -- Provider Fetch Implementations ------------------------------------------

def _aspect_ratio_str(width, height):
    """Compute the closest standard aspect ratio for Gemini Imagen models."""
    if width == height:
        return "1:1"
    ratio = width / max(1, height)
    if ratio >= 1.5:
        return "16:9"
    if ratio >= 1.2:
        return "4:3"
    if ratio <= 0.6:
        return "9:16"
    if ratio <= 0.85:
        return "3:4"
    return "1:1"


def _fetch_gemini(prompt, api_key, model=GEMINI_DEFAULT_MODEL, width=DEFAULT_WIDTH, height=DEFAULT_HEIGHT):
    """Generate image via Google Gemini Image API."""
    if not api_key:
        _last_error["gemini"] = "No API key provided for Google Gemini."
        return None

    # Option A: OpenAI-compatible endpoint (supported for gemini-2.5-flash-image, gemini-3-pro-image-preview)
    if not model.startswith("imagen-"):
        try:
            url = GEMINI_OPENAI_ENDPOINT
            payload = {
                "model": model,
                "prompt": prompt.strip(),
                "response_format": "b64_json",
                "n": 1,
            }
            body = json.dumps(payload).encode("utf-8")
            req = urllib.request.Request(url, data=body, method="POST")
            req.add_header("Content-Type", "application/json")
            req.add_header("Authorization", f"Bearer {api_key}")
            req.add_header("User-Agent", _UA)
            with urllib.request.urlopen(req, timeout=_HTTP_TIMEOUT) as resp:
                raw = resp.read()
                data = json.loads(raw.decode("utf-8"))
                img_data = data.get("data", [])
                if img_data and "b64_json" in img_data[0]:
                    return base64.b64decode(img_data[0]["b64_json"])
        except urllib.error.HTTPError as e:
            err_msg = e.read().decode("utf-8", errors="ignore")
            if e.code == 429:
                _last_error["gemini"] = (
                    "Quota exceeded (limit is 0 on free tier without billing). "
                    "Google Cloud requires billing linked to the project for Gemini Image generation."
                )
            elif e.code == 404:
                _last_error["gemini"] = f"Model '{model}' not available for this Google key/project."
            else:
                _last_error["gemini"] = f"Gemini API error (HTTP {e.code}): {err_msg[:120]}"
            print(f"[ImageGen Gemini HTTP {e.code}] {err_msg[:200]}")
        except Exception as e:
            _last_error["gemini"] = str(e)
            print(f"[ImageGen Gemini error] {e}")
        return None

    # Option B: Native Imagen Predict endpoint
    try:
        url = GEMINI_PREDICT_ENDPOINT.format(model=model)
        payload = {
            "instances": [{"prompt": prompt.strip()}],
            "parameters": {
                "sampleCount": 1,
                "aspectRatio": _aspect_ratio_str(width, height),
                "outputOptions": {"mimeType": "image/png"}
            }
        }
        body = json.dumps(payload).encode("utf-8")
        req = urllib.request.Request(url, data=body, method="POST")
        req.add_header("Content-Type", "application/json")
        req.add_header("x-goog-api-key", api_key)
        req.add_header("User-Agent", _UA)
        with urllib.request.urlopen(req, timeout=_HTTP_TIMEOUT) as resp:
            raw = resp.read()
            data = json.loads(raw.decode("utf-8"))
            predictions = data.get("predictions", [])
            if predictions and "bytesBase64Encoded" in predictions[0]:
                return base64.b64decode(predictions[0]["bytesBase64Encoded"])
    except urllib.error.HTTPError as e:
        err_msg = e.read().decode("utf-8", errors="ignore")
        if e.code == 404:
            _last_error["gemini"] = f"Model '{model}' is not supported on the standard predict endpoint."
        else:
            _last_error["gemini"] = f"Imagen error (HTTP {e.code}): {err_msg[:120]}"
        print(f"[ImageGen Imagen HTTP {e.code}] {err_msg[:200]}")
    except Exception as e:
        _last_error["gemini"] = str(e)
        print(f"[ImageGen Imagen error] {e}")
    return None


def _fetch_huggingface(prompt, api_key, model=HF_DEFAULT_MODEL, width=DEFAULT_WIDTH, height=DEFAULT_HEIGHT):
    """Generate image via Hugging Face Serverless Inference API (free tier)."""
    if not api_key:
        _last_error["huggingface"] = "No API token provided for Hugging Face (set HF_TOKEN or in config.json)."
        return None
    try:
        url = HF_ENDPOINT.format(model=model)
        payload = {
            "inputs": prompt.strip(),
            "parameters": {"width": width, "height": height}
        }
        body = json.dumps(payload).encode("utf-8")
        req = urllib.request.Request(url, data=body, method="POST")
        req.add_header("Authorization", f"Bearer {api_key}")
        req.add_header("Content-Type", "application/json")
        req.add_header("User-Agent", _UA)
        with urllib.request.urlopen(req, timeout=_HTTP_TIMEOUT) as resp:
            ct = resp.headers.get("Content-Type", "")
            data = resp.read()
            if ct.startswith("image/") or len(data) > 512:
                return data
    except urllib.error.HTTPError as e:
        err_msg = e.read().decode("utf-8", errors="ignore")
        if e.code == 410:
            _last_error["huggingface"] = (
                "Hugging Face has retired free serverless inference for diffusion models (HTTP 410). "
                "Please use Pollinations GenAI (free key at enter.pollinations.ai) for FLUX.1 generation."
            )
        else:
            _last_error["huggingface"] = f"Hugging Face HTTP {e.code}: {err_msg[:120]}"
        print(f"[ImageGen HuggingFace HTTP {e.code}] {err_msg[:200]}")
    except Exception as e:
        _last_error["huggingface"] = str(e)
        print(f"[ImageGen HuggingFace error] {e}")
    return None


def _fetch_pollinations_gen(prompt, api_key, model=POLLINATIONS_GEN_DEFAULT_MODEL, width=DEFAULT_WIDTH, height=DEFAULT_HEIGHT, seed=0):
    """Generate image via modern gen.pollinations.ai endpoint."""
    try:
        safe_prompt = urllib.parse.quote(prompt.strip(), safe="")
        url = POLLINATIONS_GEN_URL.format(
            prompt=safe_prompt, width=width, height=height, seed=seed,
            model=model, key=api_key or ""
        )
        data = _fetch_url(url)
        if data:
            return data
        _last_error["pollinations_gen"] = "Pollinations GenAI request failed or returned empty data."
    except Exception as e:
        _last_error["pollinations_gen"] = str(e)
        print(f"[ImageGen PollinationsGen error] {e}")
    return None


def _fetch_url(url):
    """Download raw bytes from a direct image URL."""
    try:
        req = urllib.request.Request(url)
        req.add_header("User-Agent", _UA)
        with urllib.request.urlopen(req, timeout=_HTTP_TIMEOUT) as resp:
            chunks = []
            remaining = _MAX_BODY
            while remaining > 0:
                chunk = resp.read(min(65536, remaining))
                if not chunk:
                    break
                chunks.append(chunk)
                remaining -= len(chunk)
            data = b"".join(chunks)
            if len(data) > 512:
                return data
    except Exception:
        pass
    return None


# -- HTTP seam ---------------------------------------------------------------
# One place to replace in tests. Everything below swallows its own faults so a
# caller that swaps this out for a stub never sees an exception either.

def _fetch(url, model=DEFAULT_MODEL):
    """Return the raw image bytes, or None on any fault.

    Inspects _current_context to dispatch to the active provider.
    If an explicit provider was configured by the user, failures will NOT
    silently fall back to Pollinations so the user knows the actual cause.
    """
    ctx = _current_context
    prompt = ctx.get("prompt", "")
    width = ctx.get("width", DEFAULT_WIDTH)
    height = ctx.get("height", DEFAULT_HEIGHT)
    seed = ctx.get("seed", 0)
    provider_override = ctx.get("provider")

    cfg = _load_config()
    configured_provider = (provider_override or cfg.get("provider") or "auto").strip().lower()
    is_explicit = configured_provider not in ("auto", "legacy", "")

    provider, api_key, active_model = get_active_provider(provider_override, model)

    # 1. Google Gemini
    if provider == "gemini" and api_key and prompt:
        print(f"[ImageGen] Routing request to Google Gemini ({active_model})...")
        data = _fetch_gemini(prompt, api_key, model=active_model, width=width, height=height)
        if data is None and active_model != GEMINI_FALLBACK_MODEL:
            data = _fetch_gemini(prompt, api_key, model=GEMINI_FALLBACK_MODEL, width=width, height=height)
        if data:
            return data
        if is_explicit:
            # Explicitly requested Gemini: do NOT silently download from Pollinations
            print(f"[ImageGen] Gemini failed: {_last_error.get('gemini')}")
            return None
        print("[ImageGen] Gemini failed; falling back to alternative provider...")

    # 2. Hugging Face Serverless Inference (FLUX.1-schnell)
    if provider == "huggingface" and api_key and prompt:
        print(f"[ImageGen] Routing request to Hugging Face ({active_model})...")
        data = _fetch_huggingface(prompt, api_key, model=active_model, width=width, height=height)
        if data is None and active_model != HF_FALLBACK_MODEL:
            data = _fetch_huggingface(prompt, api_key, model=HF_FALLBACK_MODEL, width=width, height=height)
        if data:
            return data
        if is_explicit:
            print(f"[ImageGen] Hugging Face failed: {_last_error.get('huggingface')}")
            return None
        print("[ImageGen] Hugging Face failed; falling back to alternative provider...")

    # 3. Pollinations GenAI with key
    if provider == "pollinations_gen" and api_key and prompt:
        print(f"[ImageGen] Routing request to Pollinations GenAI ({active_model})...")
        data = _fetch_pollinations_gen(prompt, api_key, model=active_model, width=width, height=height, seed=seed)
        if data:
            return data
        if is_explicit:
            return None

    # 4. Default / Fallback: Pollinations direct URL
    return _fetch_url(url)


# -- File system -------------------------------------------------------------

def _images_dir():
    """Save images to an 'Images' subfolder of Documents, creating it once."""
    folder = os.path.join(_documents_dir(), "Images")
    try:
        os.makedirs(folder, exist_ok=True)
    except Exception:
        pass
    return folder


def _documents_dir():
    """Resolve the real Documents folder, honouring OneDrive redirection.

    Asks Windows directly (SHGetKnownFolderPath), because OneDrive folder
    redirection means ~/Documents often is not the user's Documents at all.
    """
    try:
        import ctypes

        class GUID(ctypes.Structure):
            _fields_ = [("d1", ctypes.c_ulong), ("d2", ctypes.c_ushort),
                        ("d3", ctypes.c_ushort),
                        ("d4", ctypes.c_ubyte * 8)]

        # FOLDERID_Documents {FDD39AD0-238F-46AF-ADB4-6C85480369C7}
        folder_id = GUID(0xFDD39AD0, 0x238F, 0x46AF,
                         (ctypes.c_ubyte * 8)(0xAD, 0xB4, 0x6C, 0x85,
                                              0x48, 0x03, 0x69, 0xC7))
        out = ctypes.c_wchar_p()
        if ctypes.windll.shell32.SHGetKnownFolderPath(
                ctypes.byref(folder_id), 0, None, ctypes.byref(out)) == 0:
            path = out.value
            ctypes.windll.ole32.CoTaskMemFree(out)
            if path and os.path.isdir(path):
                return path
    except Exception:
        pass

    home = os.path.expanduser("~")
    candidates = []
    onedrive = os.environ.get("OneDrive") or os.environ.get("OneDriveConsumer")
    if onedrive:
        candidates.append(os.path.join(onedrive, "Images"))
    candidates += [os.path.join(home, "Images"),
                   os.path.join(home, "OneDrive", "Images")]

    for path in candidates:
        if os.path.isdir(path):
            return path

    os.makedirs(candidates[0], exist_ok=True)
    return candidates[0]


def _safe_name(file_name, fallback="image"):
    """LLM-supplied or user-supplied names are untrusted: basename only."""
    name = os.path.basename(str(file_name or "")).strip()
    for ext in (".png", ".jpg", ".jpeg", ".webp", ".gif", ".bmp"):
        if name.lower().endswith(ext):
            name = name[:-len(ext)]
    name = re.sub(r'[<>:"/\\|?*\x00-\x1f]', "_", name)
    name = re.sub(r"\s+", "_", name).strip(". _")[:60]
    return name or fallback


def _slug(prompt, fallback="image"):
    """A filesystem-safe slug derived from the prompt."""
    name = re.sub(r"[^\w\s-]", "", (prompt or fallback).strip())
    name = re.sub(r"\s+", "_", name).strip("_").lower()[:60]
    return name or fallback


# -- Public API --------------------------------------------------------------

def generate_image(prompt, file_name=None, width=DEFAULT_WIDTH,
                   height=DEFAULT_HEIGHT, model=None, provider=None):
    """Generate an image for `prompt`, save it to the user's Images folder, and
    open it in the default image viewer.

    Returns a spoken-ready message. On any failure (network, disk, empty
    response) returns a clean explanation rather than raising.
    """
    try:
        if not prompt or not re.sub(r"[\s.…\-_*#|]", "", prompt):
            return ("I need a description of what to draw, Sir. Please tell me "
                    "what the image should show.")
        if str(prompt).strip().lower() in {
            "n/a", "na", "none", "null", "todo", "text", "your content here",
            "placeholder", "sample", "example",
        }:
            return ("I need a real description of what to draw, Sir. Please "
                    "tell me what the image should show.")

        active_prov, active_key, resolved_model = get_active_provider(provider, model)
        effective_model = model or resolved_model or DEFAULT_MODEL

        seed = int(time.time()) & 0x7FFFFFFF
        safe_prompt = urllib.parse.quote(prompt.strip(), safe="")
        url = IMAGE_URL.format(prompt=safe_prompt, width=width, height=height,
                               seed=seed, model=effective_model)

        # Clear error tracker and populate context
        _last_error.clear()
        _current_context.clear()
        _current_context.update({
            "prompt": prompt,
            "width": width,
            "height": height,
            "seed": seed,
            "provider": provider,
            "model": model,
        })

        data = _fetch(url, model)
        if data is None:
            # If explicit provider failed with a known error, surface it directly!
            active_prov, _, _ = get_active_provider(provider)
            if active_prov in _last_error:
                return (f"Image generation with {active_prov.title()} could not be completed, Sir: "
                        f"{_last_error[active_prov]}")

            # Retry once with the fallback before giving up.
            if model != FALLBACK_MODEL:
                _current_context["model"] = FALLBACK_MODEL
                data = _fetch(url.replace(f"model={model}",
                                          f"model={FALLBACK_MODEL}"),
                              FALLBACK_MODEL)
            if data is None:
                return ("I could not reach the image generator, Sir. Please "
                        "check your internet connection and try again.")

        if len(data) < 512:
            return ("The image generator returned an empty or unusable file, "
                    "Sir. Please try again with a different description.")

        name = _safe_name(file_name, fallback=_slug(prompt, "image"))
        folder = _images_dir()

        save_path = os.path.join(folder, f"{name}.png")
        counter = 2
        while os.path.exists(save_path):
            save_path = os.path.join(folder, f"{name}-{counter}.png")
            counter += 1

        with open(save_path, "wb") as f:
            f.write(data)

        size = os.path.getsize(save_path)

        try:
            # RON_NO_OPEN lets the test suite run without spawning viewers.
            if hasattr(os, "startfile") and not os.environ.get("RON_NO_OPEN"):
                os.startfile(save_path)
        except Exception as e:
            print(f"[Could not open image: {e}]")

        print(f"[Image] {save_path} ({size} bytes, {width}x{height})")
        return f"Your image of {prompt} is saved and open, Sir."

    except Exception as e:
        return f"Could not create the image: {e}"


if __name__ == "__main__":
    # CLI: `python imagegen.py "a diagram of a mobile phone"`
    prompt = sys.argv[1] if len(sys.argv) > 1 and not sys.argv[1].startswith("--") else "a diagram of a mobile phone"
    probe = "--probe" in sys.argv
    if probe:
        provider, key, model = get_active_provider()
        print(json.dumps({
            "prompt": prompt,
            "active_provider": provider,
            "has_key": bool(key),
            "model": model,
            "url_template": IMAGE_URL,
        }, indent=2))
    else:
        result = generate_image(prompt)
        print(result)
