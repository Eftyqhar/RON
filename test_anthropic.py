from openai import OpenAI

# Test with hcnsec.cn using OpenAI client (they use OpenAI-compatible API)
client = OpenAI(
    api_key="YOUR_API_KEY",
    base_url="https://api.hcnsec.cn"
)

models_to_try = [
    "claude-3-5-sonnet-20240620",
    "claude-3-opus-20240229",
    "claude-3-haiku-20240307",
]

for model in models_to_try:
    print(f"\n--- Testing: {model} ---")
    try:
        response = client.chat.completions.create(
            model=model,
            messages=[{"role": "user", "content": "Hi"}],
            max_tokens=100
        )
        print("SUCCESS:", response.choices[0].message.content[:100])
    except Exception as e:
        print("ERROR:", e)
