import requests
import json
import sys

# Fix encoding for Windows console
if sys.platform == 'win32':
    sys.stdout = open(1, 'w', encoding='utf-8', errors='replace', closefd=False)

API_KEY = "YOUR_API_KEY"
BASE_URL = "https://api.hcnsec.cn"

print("Testing /v1/chat/completions with DeepSeek-V4-Flash")
try:
    response = requests.post(
        BASE_URL + "/v1/chat/completions",
        headers={
            "Authorization": f"Bearer {API_KEY}",
            "Content-Type": "application/json"
        },
        json={
            "model": "DeepSeek-V4-Flash",
            "messages": [{"role": "user", "content": "test"}],
            "max_tokens": 100
        },
        timeout=10
    )
    print(f"Status: {response.status_code}")
    data = response.json()
    print(f"Response: {json.dumps(data, ensure_ascii=False, indent=2)}")

    if response.status_code == 200:
        content = data.get('choices', [{}])[0].get('message', {}).get('content', '')
        print(f"\nContent: {content}")
except Exception as e:
    import traceback
    print(f"Error: {e}")
    print(traceback.format_exc())
