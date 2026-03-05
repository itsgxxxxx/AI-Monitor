# Install FindSimilarPost Skill (OpenClaw)

## For humans

Copy this to any agent:

```text
帮我安装 findsimilarpost skill，按这个文档执行：
https://raw.githubusercontent.com/itsgxxxxx/AI-Monitor/main/skills/findsimilarpost/docs/install.md
```

## One-command install (remote repo)

```bash
bash -lc 'set -euo pipefail; tmp="$(mktemp -d)"; git clone --depth 1 https://github.com/itsgxxxxx/AI-Monitor.git "$tmp/repo"; bash "$tmp/repo/skills/findsimilarpost/scripts/install_openclaw.sh"; rm -rf "$tmp"'
```

## One-command install (already in local repo)

```bash
bash skills/findsimilarpost/scripts/install_openclaw.sh
```

## Required API key

```bash
export TIKHUB_API_KEY="your_tikhub_api_key"
```

Optional (OpenClaw auto-map env):

`~/.openclaw/openclaw.json`

```json
{
  "skills": {
    "entries": {
      "findsimilarpost": {
        "apiKey": "your_tikhub_api_key"
      }
    }
  }
}
```

## Smoke test

```bash
python ~/.openclaw/skills/findsimilarpost/findsimilarpost.py "https://x.com/heynavtoor/status/2028719589241307635?s=20" --agent
```

If Jina is blocked, run with manual keyword:

```bash
python ~/.openclaw/skills/findsimilarpost/findsimilarpost.py "https://x.com/heynavtoor/status/2028719589241307635?s=20" --keyword "Pinchtab" --agent
```
