# Image Generation — Provider & High-Quality Free API Guide

RON's image generator (`imagegen.py`) features a multi-provider engine with built-in support for the world's best **free** image generation APIs:

1. **Google Gemini Imagen 3 (`imagen-3.0-generate-002`)**: SOTA photorealism, crisp detail, and legible in-image text. **100% Free** via Google AI Studio (no credit card required).
2. **Hugging Face Serverless Inference (`black-forest-labs/FLUX.1-schnell`)**: 12-billion parameter open-weights model. **100% Free** via a Hugging Face user access token (no credit card required).
3. **Pollinations GenAI (`gen.pollinations.ai`)**: Modern endpoint with FLUX and SDXL support via free keys from `enter.pollinations.ai`.
4. **Pollinations Zero-Key Fallback**: Instant out-of-the-box generation with zero configuration, zero signup, and no API key.

---

## ⚡ Quick Setup: Enabling Free High-Quality Generation

You can enable higher-quality generation in under 60 seconds with either Google AI Studio or Hugging Face.

### Option A: Google Gemini Imagen 3 (Recommended for Photorealism & Typography)

1. Go to [Google AI Studio](https://aistudio.google.com/) and sign in with any Google account.
2. Click **Get API key** and copy your key (starts with `AIza...`).
3. Add it to `config.json`:
   ```json
   "imagegen": {
     "provider": "auto",
     "api_key": "AIzaSy...",
     "model": "imagen-3.0-generate-002"
   }
   ```
   *Or set it in your environment:*
   ```powershell
   [Environment]::SetEnvironmentVariable("GEMINI_API_KEY", "AIzaSy...", "User")
   ```

RON will automatically detect the key, route requests to **Google Imagen 3**, and generate high-resolution PNGs with fine lighting, anatomy, and text.

---

### Option B: Hugging Face Serverless FLUX.1-schnell (Recommended for Artistic & Stylized Art)

1. Go to [Hugging Face Tokens](https://huggingface.co/settings/tokens) and sign in.
2. Click **Create new token** (Role: `Read`) and copy it (starts with `hf_...`).
3. Add it to `config.json`:
   ```json
   "imagegen": {
     "provider": "huggingface",
     "api_key": "hf_...",
     "model": "black-forest-labs/FLUX.1-schnell"
   }
   ```
   *Or set it in your environment:*
   ```powershell
   [Environment]::SetEnvironmentVariable("HF_TOKEN", "hf_...", "User")
   ```

RON will automatically route requests through Hugging Face's distributed inference router running FLUX.1-schnell.

---

## 🎯 How Provider Selection Works

RON uses an intelligent auto-detection cascade when `"provider": "auto"` is set:

```
                  ┌───────────────────────────────┐
                  │ Does a Gemini key exist?      │
                  │ (GEMINI_API_KEY / AIza... key)│
                  └──────────────┬────────────────┘
                                 │
                   YES ┌─────────┴─────────┐ NO
                       ▼                   ▼
            [Google Imagen 3]    ┌───────────────────────────────┐
                                 │ Does an HF token exist?       │
                                 │ (HF_TOKEN / hf_... key)       │
                                 └──────────────┬────────────────┘
                                                │
                                  YES ┌─────────┴─────────┐ NO
                                      ▼                   ▼
                           [Hugging Face FLUX]   ┌───────────────────────────────┐
                                                 │ Does a Pollinations key exist?│
                                                 │ (POLLINATIONS_API_KEY / pk_..)│
                                                 └──────────────┬────────────────┘
                                                                │
                                                  YES ┌─────────┴─────────┐ NO
                                                      ▼                   ▼
                                            [Pollinations GenAI]  [Zero-Key Fallback]
```

---

## 🧪 Testing Your Configuration

Check which provider and model RON is currently using without generating an image:

```powershell
python imagegen.py --probe
```

Example output:
```json
{
  "prompt": "a diagram of a mobile phone",
  "active_provider": "gemini",
  "has_key": true,
  "model": "imagen-3.0-generate-002",
  "url_template": "https://image.pollinations.ai/prompt/..."
}
```

To test an actual generation:
```powershell
python imagegen.py "a majestic cyberpunk eagle in flight, volumetric neon lighting"
```

The resulting image is automatically saved to your `Documents/Images` folder and opened in your default photo viewer.
