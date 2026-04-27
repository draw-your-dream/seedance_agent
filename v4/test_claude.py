import os, json, urllib.request, urllib.error
TOKEN = os.environ.get("CLAUDE_JWT_TOKEN", "")
print("TOKEN set?", bool(TOKEN), "len:", len(TOKEN))
URL = "https://api.picaa.ai/model/us.anthropic.claude-sonnet-4-6/converse"
payload = {"messages": [{"role": "user", "content": [{"text": "hi"}]}]}
req = urllib.request.Request(
    URL,
    data=json.dumps(payload).encode("utf-8"),
    headers={
        "Content-Type": "application/json",
        "Authorization": f"Bearer {TOKEN}",
        "aws-endpoint-prefix": "bedrock-runtime",
    },
    method="POST",
)
print("URL:", req.full_url)
print("Headers:", dict(req.headers))
try:
    with urllib.request.urlopen(req, timeout=60) as r:
        print("OK:", r.read().decode("utf-8")[:200])
except urllib.error.HTTPError as e:
    print("ERR", e.code, e.read().decode("utf-8")[:500])
