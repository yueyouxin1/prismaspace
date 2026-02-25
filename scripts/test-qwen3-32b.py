from openai import OpenAI
import time

client = OpenAI(api_key="123", base_url="http://101.52.244.200:9000/v1")

# Round 1
print(f"开始耗时测试")
messages = [{"role": "system", "content": "你是一个AI助手。"}, {"role": "user", "content": "在干嘛呢？"}]
start_time = time.time()
stream = client.chat.completions.create(
    model="qwen3-32b",
    messages=messages,
    stream=True
)
end_time = time.time() - start_time
print(f"开始LLM请求耗时：{end_time}")
is_first = False
for chunk in stream:
    if not is_first:
        is_first = True
        end_time = time.time() - start_time
        print(f"首TOKEN耗时：{end_time}")
    print(chunk.choices[0].delta.content or "", end="")