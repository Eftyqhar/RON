from openai import OpenAI

client = OpenAI(
    api_key="YOUR_API_KEY",
    base_url="https://api.hcnsec.cn"
)

# Try with /v1 prefix in the path
print("Test 1: base_url without /v1")
try:
    response = client.chat.completions.create(
        model="DeepSeek-V4-Flash",
        messages=[{"role": "user", "content": "test"}],
        timeout=10
    )
    print(f"Success: {response.choices[0].message.content[:100]}")
except Exception as e:
    print(f"Error: {e}")

# Test with streaming to see raw response
print("\nTest 2: with streaming")
try:
    response = client.chat.completions.create(
        model="DeepSeek-V4-Flash",
        messages=[{"role": "user", "content": "test"}],
        stream=True,
        timeout=10
    )
    for chunk in response:
        print(f"Chunk: {chunk}")
except Exception as e:
    print(f"Error: {e}")

# Try direct requests to see raw endpoint behavior
print("\nTest 3: testing different model names")
models = ["DeepSeek-V4-Flash", "deepseek-chat", "auto"]
for model in models:
    try:
        response = client.chat.completions.create(
            model=model,
            messages=[{"role": "user", "content": "test"}],
            timeout=10
        )
        print(f"Model '{model}' Success: {response.choices[0].message.content[:50]}")
    except Exception as e:
        print(f"Model '{model}' Error: {e}")
