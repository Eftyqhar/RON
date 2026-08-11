from openai import OpenAI
import sys
import io

# Fix encoding for Windows console
if sys.platform == 'win32':
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8', errors='replace')

client = OpenAI(
    api_key="YOUR_API_KEY",
    base_url="https://api.hcnsec.cn/v1"
)

print("Testing API connection with different models...")

models_to_try = [
    "DeepSeek-V4-Flash",
    "deepseek-chat",
    "deepseek-reasoner",
    "gpt-4o-mini",
    "gpt-3.5-turbo",
]

for model in models_to_try:
    print(f"\n--- Testing: {model} ---")
    try:
        response = client.chat.completions.create(
            model=model,
            messages=[{"role": "user", "content": "Hi"}],
            timeout=10
        )
        print(f"SUCCESS: {response.choices[0].message.content[:50]}")
    except Exception as e:
        print(f"ERROR: {e}")
