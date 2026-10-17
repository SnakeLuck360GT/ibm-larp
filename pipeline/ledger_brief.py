"""AI decision brief for the Cross-border ledger, written by an LLM on Groq from the pipeline's own outputs.

Run from the repo root:  .venv/bin/python pipeline/ledger_brief.py
Needs GROQ_API_KEY in the environment or in <repo>/.env (gitignored; key from https://console.groq.com/keys).
Model: openai/gpt-oss-120b by default; GROQ_MODEL overrides it, and if the model is unavailable the next one in
PREFERRED that the account can use is taken.
Writes data/out/ledger_brief.json for the Cross-border ledger tab.

The model only sees FACTS (built below from damage.json, attribution.json, states.json and apims_now.json, rounded
the way the page shows them). Every number in its answer must appear in those facts, or the brief is rejected and
the previous file is kept: the LLM words the brief, it does not compute or invent figures.
"""

import json
import os
import re
import sys
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

import requests

ROOT = Path(__file__).resolve().parents[1]
OUTD = ROOT / "data" / "out"
OUT = OUTD / "ledger_brief.json"
API = "https://api.groq.com/openai/v1"
PREFERRED = ["openai/gpt-oss-120b", "openai/gpt-oss-20b", "llama-3.3-70b-versatile", "llama-3.1-8b-instant"]
KL_TZ = ZoneInfo("Asia/Kuala_Lumpur")
SECTOR = {"tourism_loss": "tourism & hospitality", "agriculture_loss": "agriculture", "transport_loss": "transport & logistics",
          "construction_outdoor_loss": "construction & outdoor work", "productivity_loss": "sick days in other sectors",
          "health": "health care"}

SYSTEM = """You write a short decision brief for Malaysian government officials (NADMA, the Ministry of Health and the
foreign ministry) about the cost of the current haze and where the smoke comes from.
Rules:
- Use ONLY the facts in the user message. Copy numbers exactly as written there; never calculate, round or add numbers.
- Every figure that is an estimate must be called an estimate. Say the smoke-source shares are from the September 2019
  haze (a past episode), not today's.
- Name provinces, never companies or people.
- Plain English, no jargon, no markdown.
Reply with JSON only: {"headline": "<max 14 words>", "summary": "<2 or 3 sentences>",
"actions": ["<decision 1, max 28 words>", "<decision 2>", "<decision 3>"]}"""


def env(name: str) -> str | None:
    v = os.environ.get(name)
    f = ROOT / ".env"
    if not v and f.exists():
        for line in f.read_text().splitlines():
            if line.startswith(name + "="):
                v = line.split("=", 1)[1].strip()
    return v or None


def rm(x: float) -> str:
    return f"RM {x / 1e9:.2f} billion" if x >= 1e9 else f"RM {x / 1e6:.1f} million"


def pct(x: float) -> str:
    return f"{round(x * 100)}%"


def facts() -> dict:
    d = json.loads((OUTD / "damage.json").read_text())
    nat, ep, fc = d["national"], d["episode"], d["forecast_72h"]["scenarios"]
    sectors = sorted(nat["by_category_rm"].items(), key=lambda kv: -kv[1])[:3]
    states = sorted(d["states"], key=lambda s: -s["forecast_72h_scenarios_rm"]["expected"])[:3]
    bench = d["model"].get("benchmark_rm_per_day")
    n_place = sum(1 for a in d["assumptions"] if "DUMMY" in str(a.get("source", "")))  # same rule as the page
    f = {
        "haze episode so far": f"{ep['start']} to {ep['end']}, {ep['days']} days",
        "estimated damage so far": f"{rm(nat['total_damage_rm'])} (90% range {rm(nat['ci90_rm'][0])} to {rm(nat['ci90_rm'][1])})",
        "estimated damage per day": rm(nat["damage_per_day_rm"]),
        "published outside estimate per day": f"RM {bench[0] / 1e6:.0f} to {bench[1] / 1e6:.0f} million" if bench else None,
        "largest losses so far by sector": [f"{SECTOR.get(k, k)}: {rm(v)}" for k, v in sectors],
        "estimated damage next 72 hours": f"best case {rm(fc['best']['total_72h_rm'])}, expected {rm(fc['expected']['total_72h_rm'])}, "
                                          f"worst case {rm(fc['worst']['total_72h_rm'])}",
        "states with the highest expected damage next 72 hours":
            [f"{s['state']}: {rm(s['forecast_72h_scenarios_rm']['expected'])}" for s in states],
        "damage model inputs that are still placeholders": f"{n_place} of {len(d['assumptions'])}",
    }
    att = OUTD / "attribution.json"
    if att.exists():
        a = json.loads(att.read_text())
        f["smoke sources, September 2019 haze (share of matched fire intensity)"] = [
            f"{s['province']}: {pct(s['share'])}" for s in a["sources"][:4]]
    apims = OUTD / "apims_now.json"
    if apims.exists():
        a = json.loads(apims.read_text())
        st = sorted(a["states"].items(), key=lambda kv: -kv[1]["api"])
        f["measured now (DOE Air Pollutant Index, highest station per state)"] = {
            "states unhealthy (API 101 or more)": f"{sum(1 for _, v in st if v['api'] >= 101)} of {len(st)}",
            "highest": [f"{n}: API {v['api']}" for n, v in st[:3]],
        }
    eq = OUTD / "states.json"
    if eq.exists():
        e = json.loads(eq.read_text())
        top = sorted(e["states"], key=lambda s: s["priority"])[:3]
        f["haze health priority (Resource routing, 1 = help first)"] = [f"{s['priority']}. {s['state']}" for s in top]
    return {k: v for k, v in f.items() if v is not None}


NUM = re.compile(r"\d+(?:[.,]\d+)*")
# Money and percentages must match a fact exactly ("RM 2 billion" is not "RM 2.00 billion"); other numbers
# (days, API values, counts) must at least appear somewhere in the facts.
MONEY = re.compile(r"RM\s*\d[\d.,]*(?:\s*(?:to|-|–)\s*\d[\d.,]*)?\s*(?:million|billion)?", re.I)
PCT = re.compile(r"\d+(?:\.\d+)?\s*%")


def norm(t: str) -> str:
    return re.sub(r"[\s,]", "", t).lower()


def unsupported(brief: dict, fact_text: str) -> list[str]:
    text = " ".join([brief.get("headline", ""), brief.get("summary", "")] + list(brief.get("actions", [])))
    ft = norm(fact_text)
    bad = {m.strip() for m in MONEY.findall(text) + PCT.findall(text) if norm(m) not in ft}
    allowed = {norm(n) for n in NUM.findall(fact_text)}
    bad |= {n for n in NUM.findall(text) if norm(n) not in allowed}
    return sorted(bad)


def pick_model(key: str) -> str:
    want = env("GROQ_MODEL")
    if want:
        return want
    r = requests.get(f"{API}/models", headers={"Authorization": f"Bearer {key}"}, timeout=30)
    r.raise_for_status()
    ids = [m["id"] for m in r.json().get("data", []) if m.get("active", True)]
    for p in PREFERRED:
        if p in ids:
            return p
    chat = [i for i in ids if not re.search(r"whisper|guard|tts|playai|embed", i)]
    if not chat:
        raise SystemExit(f"Groq: no chat model available ({ids})")
    return chat[0]


def ask(key: str, model: str, messages: list) -> dict:
    body = {"model": model, "messages": messages, "temperature": 0.2, "max_tokens": 600,
            "response_format": {"type": "json_object"}}
    if "gpt-oss" in model:
        # Reasoning model: its thinking counts toward max_tokens, so leave room; a short brief needs little thinking.
        body.update(max_tokens=3000, reasoning_effort="low")
    r = requests.post(f"{API}/chat/completions", headers={"Authorization": f"Bearer {key}"}, timeout=90, json=body)
    if r.status_code == 400 and "reasoning_effort" in r.text:  # an endpoint that does not take it
        body.pop("reasoning_effort")
        r = requests.post(f"{API}/chat/completions", headers={"Authorization": f"Bearer {key}"}, timeout=90, json=body)
    if r.status_code != 200:
        raise RuntimeError(f"Groq {r.status_code}: {r.text[:300]}")
    choice = r.json()["choices"][0]
    if choice.get("finish_reason") == "length":
        raise RuntimeError("Groq: answer cut off at max_tokens")
    return json.loads(choice["message"]["content"])


def valid_shape(b: dict) -> bool:
    return (isinstance(b.get("headline"), str) and isinstance(b.get("summary"), str)
            and isinstance(b.get("actions"), list) and len(b["actions"]) >= 2
            and all(isinstance(x, str) and x.strip() for x in b["actions"]))


def main() -> None:
    key = env("GROQ_API_KEY")
    if not key:
        raise SystemExit("GROQ_API_KEY not set (environment or .env)")
    f = facts()
    fact_text = json.dumps(f, ensure_ascii=False, indent=1)
    model = pick_model(key)
    messages = [{"role": "system", "content": SYSTEM}, {"role": "user", "content": "Facts:\n" + fact_text}]
    brief, bad = None, []
    for _ in range(2):  # one retry, told which numbers were not in the facts
        brief = ask(key, model, messages)
        bad = unsupported(brief, fact_text) if valid_shape(brief) else ["(wrong JSON shape)"]
        if not bad:
            break
        messages += [{"role": "assistant", "content": json.dumps(brief)},
                     {"role": "user", "content": f"These numbers are not in the facts: {', '.join(bad)}. "
                                                 "Rewrite using only numbers copied from the facts. JSON only."}]
    if bad:
        print(f"ledger_brief: rejected, unsupported numbers {bad}; kept the previous brief", file=sys.stderr)
        sys.exit(1)
    out = {
        "generated_at": datetime.now(KL_TZ).isoformat(timespec="seconds"),
        "provider": "Groq", "model": model,
        "headline": brief["headline"].strip(), "summary": brief["summary"].strip(),
        "actions": [a.strip() for a in brief["actions"]][:3],
        "checked": "every number in this brief appears in the facts below",
        "facts": f,
    }
    tmp = OUT.with_suffix(".json.tmp")
    tmp.write_text(json.dumps(out, indent=1, ensure_ascii=False) + "\n")
    tmp.replace(OUT)
    print(f"ledger_brief.json: {model} · {out['headline']}")


if __name__ == "__main__":
    main()
