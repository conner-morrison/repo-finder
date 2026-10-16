"""Find GitHub repositories similar to a job posting.

Similarity is a score from 0 to 1 built from three parts, each the share of the
job's terms that a repository also covers:

  tech stack    (weight 0.4)  products, platforms, languages, frameworks
  requirements  (weight 0.4)  the job's most important content words
  architecture  (weight 0.2)  shapes of solution: API, migration, sync, webhook...

A part the job says nothing about is left out and the others share its weight.
A repository is read through its name, description, topics, language and README.

Standard library only. Uses GITHUB_TOKEN, or the token of a logged-in `gh`, when
there is one (30 searches a minute instead of 10).
"""

import base64
import json
import os
import re
import subprocess
import time
import urllib.error
import urllib.parse
import urllib.request
from collections import Counter

API = "https://api.github.com"

# canonical term -> phrases that mean it. Multi-word phrases are matched as phrases.
TECH = {
    "python": ["python"], "javascript": ["javascript", "js", "node", "nodejs", "node.js"],
    "typescript": ["typescript", "ts"], "php": ["php"], "java": ["java"], "go": ["golang"],
    "ruby": ["ruby", "rails"], "csharp": ["c#", "csharp", ".net", "dotnet"], "rust": ["rust"],
    "react": ["react", "reactjs", "next.js", "nextjs"], "vue": ["vue", "vuejs", "nuxt"],
    "angular": ["angular"], "django": ["django"], "flask": ["flask"], "fastapi": ["fastapi"],
    "laravel": ["laravel"], "wordpress": ["wordpress", "woocommerce"], "shopify": ["shopify"],
    "google apps script": ["google apps script", "apps script", "appscript"],
    "google drive": ["google drive", "gdrive", "drive api"], "google sheets": ["google sheets", "spreadsheet", "sheets"],
    "gmail": ["gmail"], "google workspace": ["google workspace", "g suite", "gsuite"],
    "hubspot": ["hubspot"], "salesforce": ["salesforce"], "zoho": ["zoho"], "pipedrive": ["pipedrive"],
    "keap": ["keap", "infusionsoft"], "highlevel": ["highlevel", "gohighlevel", "go high level", "ghl", "leadconnector"],
    "clickfunnels": ["clickfunnels"], "leadpages": ["leadpages"], "swipepages": ["swipepages"],
    "zapier": ["zapier"], "make": ["make.com", "integromat"], "n8n": ["n8n"], "airtable": ["airtable"],
    "notion": ["notion"], "slack": ["slack"], "twilio": ["twilio"], "stripe": ["stripe"], "paypal": ["paypal"],
    "quickbooks": ["quickbooks"], "mailchimp": ["mailchimp"], "activecampaign": ["activecampaign"],
    "openai": ["openai", "gpt", "chatgpt", "llm"], "aws": ["aws", "lambda", "s3"], "gcp": ["gcp", "google cloud"],
    "azure": ["azure"], "docker": ["docker"], "postgres": ["postgres", "postgresql"], "mysql": ["mysql"],
    "mongodb": ["mongodb", "mongo"], "firebase": ["firebase"], "supabase": ["supabase"],
    "selenium": ["selenium", "playwright", "puppeteer"], "pandas": ["pandas"],
}

ARCH = {
    "api": ["api", "rest", "restful", "graphql", "sdk", "endpoint"], "webhook": ["webhook", "webhooks"],
    "migration": ["migration", "migrate", "migrating", "import", "export", "transfer"],
    "sync": ["sync", "synchronize", "synchronization"], "integration": ["integration", "integrate", "connector"],
    "automation": ["automation", "automate", "automated", "workflow", "workflows"],
    "crm": ["crm", "contacts", "pipeline", "deals", "leads"], "etl": ["etl", "data pipeline"],
    "dedupe": ["duplicate", "duplicates", "dedupe", "deduplication", "consolidation", "consolidate", "cleanup", "clean up"],
    "scraper": ["scraper", "scraping", "crawler"], "dashboard": ["dashboard", "reporting", "report"],
    "bot": ["bot", "chatbot", "agent"], "cli": ["cli", "command line"], "cron": ["cron", "scheduled", "trigger"],
    "oauth": ["oauth"], "csv": ["csv"], "email": ["email", "campaign", "campaigns", "newsletter"],
    "payments": ["payment", "payments", "invoice", "invoices", "checkout", "receipt", "receipts"],
    "pages": ["landing page", "landing pages", "funnel", "funnels", "page builder"],
    "mcp": ["mcp", "model context protocol"],
}

STOP = set("""a about above after again all also am an and any are as at be because been before being below
between both but by can could did do does doing down during each few for from further had has have having he
her here hers him his how i if in into is it its itself just me more most my no nor not now of off on once
only or other our ours out over own same she should so some such than that the their them then there these
they this those through to too under until up very was we were what when where which while who whom why will
with would you your yours need needs needed someone looking want wants help helping get getting make making
like able using use used new current currently please must also well work working job project expert
experienced experience developer freelancer etc including include includes based within across ensure first
goal goals all one two three more most may might every per via just really thing things important ones
bring cancel main best good great top ready quick asap long term lot lots kind sure""".split())


def stem(word):
    for suf in ("ations", "ation", "ings", "ing", "ies", "ied", "es", "ed", "s"):
        if len(word) > len(suf) + 3 and word.endswith(suf):
            return word[: -len(suf)] + ("y" if suf in ("ies", "ied") else "")
    return word


def words(text):
    return re.findall(r"[a-z0-9][a-z0-9#+.\-]*[a-z0-9#+]|[a-z0-9]", text.lower())


def norm(text):
    """Text as a padded run of stemmed words, so phrases can be matched by ' x y '."""
    return " " + " ".join(stem(w) for w in words(text)) + " "


def found_terms(lexicon, normed):
    hits = set()
    for term, phrases in lexicon.items():
        if any(f" {' '.join(stem(w) for w in words(p))} " in normed for p in phrases):
            hits.add(term)
    return hits


def job_profile(title, description):
    title = re.sub(r"\((re-?run|repost(ed)?|updated?)\)", "", title, flags=re.I).strip()
    normed, title_normed = norm(f"{title}\n{description}"), norm(title)
    # A term in the title is what the job is; one only in the description may be
    # incidental (a tool to move away from, say), so the title counts three times.
    weigh = lambda lex: {t: 3 if t in found_terms(lex, title_normed) else 1 for t in found_terms(lex, normed)}
    tech, arch = weigh(TECH), weigh(ARCH)
    lexical = {stem(w) for phrases in list(TECH.values()) + list(ARCH.values()) for p in phrases for w in words(p)}
    counts = Counter()
    for w in words(description):
        s = stem(w)
        if s not in STOP and w not in STOP and len(s) > 2 and not s.isdigit() and s not in lexical:
            counts[s] += 1
    for w in words(title):
        s = stem(w)
        if s not in STOP and w not in STOP and len(s) > 2 and not s.isdigit() and s not in lexical:
            counts[s] += 3
    keywords = dict(counts.most_common(12))
    by_weight = lambda d: dict(sorted(d.items(), key=lambda kv: -kv[1]))
    return {"tech": by_weight(tech), "arch": by_weight(arch), "keywords": keywords}


def score_repo(profile, repo, readme):
    text = " ".join([repo.get("name", "").replace("-", " ").replace("_", " "), repo.get("description") or "",
                     " ".join(t.replace("-", " ") for t in repo.get("topics") or []), repo.get("language") or "",
                     readme[:30000]])
    normed = norm(text)
    parts, weights = {}, {"tech": 0.4, "requirements": 0.4, "architecture": 0.2}
    def covered(weights, lexicon):
        hit = found_terms({t: lexicon[t] for t in weights}, normed)
        return sum(w for t, w in weights.items() if t in hit) / sum(weights.values())
    if profile["tech"]:
        parts["tech"] = covered(profile["tech"], TECH)
    if profile["arch"]:
        parts["architecture"] = covered(profile["arch"], ARCH)
    if profile["keywords"]:
        total = sum(profile["keywords"].values())
        parts["requirements"] = sum(w for k, w in profile["keywords"].items() if f" {k} " in normed) / total
    if not parts:
        return 0.0, {}
    used = sum(weights[k] for k in parts)
    score = sum(weights[k] * v for k, v in parts.items()) / used
    if not readme.strip():
        score *= 0.85          # we saw less of it, so trust it less
    return round(score, 3), {k: round(v, 2) for k, v in parts.items()}


# --- GitHub ---------------------------------------------------------------------

_token = None


def token():
    global _token
    if _token is None:
        _token = os.environ.get("GITHUB_TOKEN") or os.environ.get("GH_TOKEN") or ""
        if not _token:
            try:
                _token = subprocess.run(["gh", "auth", "token"], capture_output=True, text=True, timeout=10).stdout.strip()
            except (OSError, subprocess.SubprocessError):
                _token = ""
    return _token


def gh(path, params=None, accept="application/vnd.github+json"):
    url = API + path + ("?" + urllib.parse.urlencode(params) if params else "")
    req = urllib.request.Request(url, headers={"Accept": accept, "User-Agent": "repo-finder",
                                               "X-GitHub-Api-Version": "2022-11-28"})
    if token():
        req.add_header("Authorization", f"Bearer {token()}")
    for attempt in range(3):
        try:
            with urllib.request.urlopen(req, timeout=20) as res:
                body = res.read()
                return body.decode("utf-8", "replace") if "raw" in accept else json.loads(body)
        except urllib.error.HTTPError as err:
            if err.code in (403, 429) and attempt < 2:      # rate limited: wait for the window
                reset = int(err.headers.get("X-RateLimit-Reset") or 0)
                time.sleep(min(max(reset - time.time(), 5), 65))
                continue
            if err.code == 404:
                return None
            raise RuntimeError(f"GitHub {err.code} on {path}: {err.read()[:200].decode('utf-8', 'replace')}")
    return None


def readme_of(full_name):
    try:
        return gh(f"/repos/{full_name}/readme", accept="application/vnd.github.raw+json") or ""
    except RuntimeError:
        return ""


def queries_for(profile):
    """Most specific first: two products together, a product with what the job
    is about, then each product alone, then the plain words."""
    tech = [t for t in profile["tech"] if t not in ("javascript", "typescript", "python", "php", "java")] or list(profile["tech"])
    alias = {t: TECH[t][1] if t == "highlevel" else TECH[t][0] for t in tech}   # 'gohighlevel' finds more
    kw = list(profile["keywords"])[:4]
    arch = [a for a in profile["arch"] if a not in ("api",)][:3]
    out = []
    for i, a in enumerate(tech[:3]):
        for b in tech[i + 1:3]:
            out.append(f"{alias[a]} {alias[b]}")
    for t in tech[:3]:
        for c in (arch[:2] + kw[:1]):
            out.append(f"{alias[t]} {c}")
    out += [alias[t] for t in tech[:2]]
    if len(kw) >= 2:
        out.append(f"{kw[0]} {kw[1]}")
    seen, uniq = set(), []
    for q in out:
        if q not in seen:
            seen.add(q)
            uniq.append(q)
    return uniq[:8]


def month_end(ym):
    y, m = map(int, ym.split("-"))
    nxt = f"{y + (m == 12)}-{(m % 12) + 1:02d}-01"
    return time.strftime("%Y-%m-%d", time.gmtime(time.mktime(time.strptime(nxt, "%Y-%m-%d")) - 86400))


def in_period(repo, period):
    """The whole life of the repo - created to last push - inside the period."""
    start, end = period
    created, pushed = (repo.get("created_at") or "")[:7], (repo.get("pushed_at") or "")[:7]
    return bool(created) and created >= start and (not end or pushed <= end)


def find_similar(title, description, threshold, exclude=(), want=2, period=None, analyze=None, log=lambda *_: None):
    """Up to `want` repos scoring at least `threshold`, best first, plus the best
    score seen so the caller can say how close it came. With `period`
    ("YYYY-MM", "YYYY-MM" or None for now), only repos created and last pushed
    inside it count. With `analyze(repo, readme)`, each pick is analysed."""
    profile = job_profile(title, description)
    log(f"Job terms — tech: {', '.join(profile['tech']) or '—'}; architecture: {', '.join(profile['arch']) or '—'}; "
        f"keywords: {', '.join(profile['keywords']) or '—'}")
    exclude = {u.rstrip("/").lower() for u in exclude}
    hits, repos = Counter(), {}
    window = ""
    if period:
        window = f" created:>={period[0]}-01" + (f" pushed:<={month_end(period[1])}" if period[1] else "")
    for q in queries_for(profile):
        data = gh("/search/repositories", {"q": f"{q} fork:false{window}", "per_page": 10}) or {}
        for rank, r in enumerate(data.get("items", [])):
            if r["html_url"].lower() in exclude or r.get("archived") or (period and not in_period(r, period)):
                continue
            repos[r["full_name"]] = r
            hits[r["full_name"]] += 10 - rank       # earlier and more often = more relevant
        if len(repos) >= 40:
            break
    shortlist = [name for name, _ in hits.most_common(12)]
    log(f"Searched {len(queries_for(profile))} queries, {len(repos)} candidates, reading {len(shortlist)} READMEs")
    scored, readmes = [], {}
    for name in shortlist:
        r = repos[name]
        readmes[name] = readme_of(name)
        score, parts = score_repo(profile, r, readmes[name])
        scored.append({"name": name, "url": r["html_url"], "score": score, "parts": parts,
                       "stars": r.get("stargazers_count", 0), "language": r.get("language"),
                       "created": (r.get("created_at") or "")[:10], "last_push": (r.get("pushed_at") or "")[:10],
                       "description": (r.get("description") or "")[:200]})
    scored.sort(key=lambda x: (x["score"], x["stars"]), reverse=True)
    picked = [s for s in scored if s["score"] >= threshold][:want]
    if analyze:
        for p in picked:
            log(f"Analysing {p['name']}")
            p["analysis"] = analyze(repos[p["name"]], readmes[p["name"]])
    return {"profile": profile, "repos": picked, "best_score": scored[0]["score"] if scored else 0.0,
            "considered": len(scored)}


if __name__ == "__main__":      # python3 finder.py "title" "description" 0.5
    import sys
    res = find_similar(sys.argv[1], sys.argv[2], float(sys.argv[3]) if len(sys.argv) > 3 else 0.5, log=print)
    print(json.dumps(res, indent=2))
