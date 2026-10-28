"""AI engine: search GitHub, judge similarity, analyse repos and write the report.

Two ways to run the AI part of a job, with the same input and the same result:

  claude   - the Claude API does it (needs an Anthropic API key):
               1. plan     reads the job, writes its summary and GitHub search queries
               2. gather   (code) runs the queries inside the member's period, reads READMEs
               3. judge    scores every candidate 0-1 against the job, with a reason
               4. analyse  reads the 1-2 picks in depth and writes their analysis
               5. report   (code) puts it together
  session  - hand-off: the worker writes the job to ai_tasks/pending/<job>.json and
             an AI assistant (a Claude Code session) does the same five steps with
             the CLI below, then submits a result in the same shape.

Rules both follow: only repos created and last pushed inside the member's
period; nothing about people (owners, authors, contributors, licences,
sponsors, contact details) in any analysis or report.

CLI (for the hand-off):
  python3 ai.py tasks                         pending jobs
  python3 ai.py gather <job> "query" ...      candidates for those queries -> digest file
  python3 ai.py bundle <owner/repo> ...       everything needed to analyse a repo -> file
  python3 ai.py submit <job> result.json      hand the result to the running worker
  python3 ai.py compose-submit <job> res.json hand a missed-mode result to the running worker

Missed mode (rules/missed.md) runs two steps per related job - analyse the JD,
then compose 1-2 past projects - on the same two engines, and replies the same way.
"""

import json
import os
import re
import sys
import time
import urllib.error
import urllib.request
from collections import Counter
from pathlib import Path

ROOT = Path(__file__).resolve().parent
if (ROOT / ".deps").is_dir():          # SDK installed next to the project (uv pip install --target .deps)
    sys.path.insert(0, str(ROOT / ".deps"))

import analyzer  # noqa: E402
import finder    # noqa: E402

MODEL = "claude-opus-5-5"
PRICE_IN, PRICE_OUT = 4.00, 20.00          # $ per 1M tokens, Claude Opus 5.5
TASKS = ROOT / "ai_tasks"
PENDING, DONE = TASKS / "pending", TASKS / "done"
CANDIDATES_MAX = 30
README_DIGEST = 2500
SECTIONS = ["requirements", "workflow", "tech_stack", "architecture", "know_how", "smart_approach"]

NO_PEOPLE = ("Never mention people: no owner, author, maintainer or contributor names, usernames or "
             "handles, no licence, sponsorship, funding, contact details, social links, star or fork counts. "
             "Describe the project, not who made it.")


class AIError(RuntimeError):
    pass


# --- result shape (both engines) -------------------------------------------------------

def _strs(desc):
    return {"type": "array", "items": {"type": "string"}, "description": desc}


ANALYSIS_SCHEMA = {
    "type": "object",
    "properties": {
        "requirements": _strs("What the project does and is for: features, scope, inputs and outputs"),
        "workflow": _strs("How it is set up, run and used, step by step; build and deploy flow"),
        "tech_stack": _strs("Languages, platforms, APIs, libraries and services it uses"),
        "architecture": _strs("Structure: components, modules, data flow, storage, integrations, deployment"),
        "know_how": _strs("Practical knowledge: limits, quotas, caveats, configuration, pitfalls and fixes"),
        "smart_approach": _strs("Clever techniques worth reusing, each with why it helps"),
        "fit": {"type": "string", "description": "How this repo helps deliver the job: what to reuse, what is missing"},
    },
    "required": SECTIONS + ["fit"],
    "additionalProperties": False,
}

PLAN_SCHEMA = {
    "type": "object",
    "properties": {
        "summary": {"type": "string", "description": "What the client needs, in 2-4 sentences"},
        "core": _strs("The essential requirements and technologies a similar project must share"),
        "queries": _strs("8-12 GitHub repository search queries, 2-4 words each, most specific first"),
    },
    "required": ["summary", "core", "queries"],
    "additionalProperties": False,
}

JUDGE_SCHEMA = {
    "type": "object",
    "properties": {
        "scores": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "repo": {"type": "string", "description": "owner/name exactly as given"},
                    "similarity": {"type": "number", "description": "0 to 1"},
                    "real_project": {"type": "boolean",
                                     "description": "false for SEO/affiliate pages, portfolios, lists, profile READMEs"},
                    "reason": {"type": "string", "description": "One or two sentences: what matches and what does not"},
                },
                "required": ["repo", "similarity", "real_project", "reason"],
                "additionalProperties": False,
            },
        }
    },
    "required": ["scores"],
    "additionalProperties": False,
}


def validate(result, threshold):
    """Check and normalise a result from either engine. Raises AIError."""
    if not isinstance(result, dict):
        raise AIError("result must be an object")
    repos = result.get("repos") or []
    if not isinstance(repos, list):
        raise AIError("repos must be a list")
    clean_repos = []
    for r in repos[:2]:
        url = str(r.get("url") or "")
        m = re.match(r"^https://github\.com/([\w.-]+/[\w.-]+)/?$", url)
        if not m:
            raise AIError(f"not a GitHub repo url: {url!r}")
        sim = float(r.get("similarity", -1))
        if not 0 <= sim <= 1:
            raise AIError(f"similarity of {url} must be 0-1")
        if sim < threshold:
            raise AIError(f"{url} scores {sim:.2f}, below the member's {threshold:.2f}")
        analysis = r.get("analysis") or {}
        clean_repos.append({
            "name": m.group(1), "url": url, "score": round(sim, 3),
            "reason": scrub(r.get("reason", "")),
            "created": str(r.get("created") or "")[:10], "last_push": str(r.get("last_push") or "")[:10],
            "language": r.get("language"), "description": scrub(r.get("description") or "")[:300],
            "analysis": {k: [scrub(x) for x in analysis.get(k) or [] if scrub(x)] for k in SECTIONS if analysis.get(k)},
            "fit": scrub(r.get("fit") or analysis.get("fit") or ""),
        })
    return {
        "summary": scrub(result.get("summary", "")),
        "queries": [str(q) for q in result.get("queries") or []][:20],
        "considered": int(result.get("considered") or 0),
        "best_score": float(result.get("best_score") or (clean_repos[0]["score"] if clean_repos else 0)),
        "repos": clean_repos,
        "report": scrub(result.get("report", "")),
        "engine": result.get("engine") or "claude-code-session",
        "usage": result.get("usage"),
    }


def scrub(text):
    """Last line of defence for the no-people rule: no emails, handles or profile/repo urls in prose."""
    return " ".join(analyzer.clean(str(text or "")).split())


def report_text(job, res, threshold, period):
    """The report as readable text: what the job needs, what was found, and why."""
    lines = [f"Job {job['job_id']}: {job['title']}", ""]
    if res.get("summary"):
        lines += ["What the client needs:", res["summary"], ""]
    lines.append(f"Searched GitHub for projects created and last updated within {period[0]} – {period[1] or 'now'}; "
                 f"{res.get('considered', 0)} candidates judged, threshold {threshold:.2f}.")
    if not res["repos"]:
        lines.append(f"No project reached the threshold (best {res.get('best_score', 0):.2f}).")
    for i, r in enumerate(res["repos"], 1):
        lines += ["", f"{i}. {r['url']} — similarity {r['score']:.2f}", f"   Why: {r['reason']}"]
        if r.get("fit"):
            lines.append(f"   Fit for this job: {r['fit']}")
        lines.append(analyzer.as_text(r["analysis"]))
    return "\n".join(lines).strip()


# --- gathering (code, both engines) ----------------------------------------------------

def gather(queries, period, exclude=(), limit=CANDIDATES_MAX):
    """Run the queries (name/description/topics and README scope) inside the period,
    drop what is plainly not a project, and keep the most-hit candidates with
    their READMEs."""
    window = f" created:>={period[0]}-01" + (f" pushed:<={finder.month_end(period[1])}" if period[1] else "")
    exclude = {u.rstrip("/").lower() for u in exclude}
    hits, repos = Counter(), {}
    for q in queries[:12]:
        for scope in ("", " in:name,description,topics,readme"):
            items = (finder.gh("/search/repositories", {"q": f"{q}{scope} fork:false{window}", "per_page": 20}) or {}).get("items", [])
            for rank, r in enumerate(items):
                if (r["html_url"].lower() in exclude or r.get("archived") or not finder.is_project(r)
                        or not finder.in_period(r, tuple(period))):
                    continue
                repos[r["full_name"]] = r
                hits[r["full_name"]] += 20 - rank
    out = []
    for name, _ in hits.most_common(limit):
        r = repos[name]
        out.append({"repo": name, "url": r["html_url"], "description": r.get("description") or "",
                    "topics": r.get("topics") or [], "language": r.get("language"),
                    "created": (r.get("created_at") or "")[:10], "last_push": (r.get("pushed_at") or "")[:10],
                    "readme": analyzer.clean(finder.readme_of(name))[:README_DIGEST], "_raw": r})
    return out


PEOPLE_SECTION = re.compile(r"^#{1,6}\s*(" + analyzer.SECTIONS[0][1] + r")", re.I)
PEOPLE_LINE = re.compile(r"(?im)^.*(\bcopyright\b|©|\(c\) \d{4}|@author\b|\bauthors?\s*:|\bmaintainers?\s*:|"
                         r"\bcreated by\b|\bwritten by\b|\bderived from\b.*\bby\b|\boriginal (source|work) by\b).*$")


def without_people(readme):
    """README minus its people sections (authors, credits, licence, sponsors, contact...)."""
    out, skip = [], False
    for line in readme.splitlines():
        if re.match(r"^\s{0,3}#{1,6}\s", line):
            heading = re.sub(r"[^\w\s']", " ", line.lower()).strip()
            skip = bool(re.search(analyzer.SECTIONS[0][1], heading))
        if not skip:
            out.append(line)
    return "\n".join(out)


def code_safe(text):
    """For source code: drop people lines, emails, handles and owner urls, but keep < and >."""
    text = PEOPLE_LINE.sub("", text)
    text = analyzer.EMAIL.sub(" ", text)
    text = analyzer.HANDLE.sub(" ", text)
    return re.sub(r"(git@github\.com:|https?://(www\.)?github\.com/)[\w.-]+/[\w.-]+[^\s)\]'\"`]*", "<repo url>", text)


def bundle(full_name, raw=None):
    """What an analyst needs to read a repo: metadata, README, layout, manifests,
    CI and the main source files. People-related sections, lines and files are left out."""
    r = raw or finder.gh(f"/repos/{full_name}") or {}
    paths = analyzer.tree_of(full_name, r.get("default_branch") or "main")
    people_file = re.compile(r"licen[cs]e|authors|contributors|funding|code_of_conduct|copying", re.I)
    parts = [f"# {full_name}", f"Description: {r.get('description') or ''}",
             f"Language: {r.get('language')}; topics: {', '.join(r.get('topics') or [])}",
             f"Created {(r.get('created_at') or '')[:10]}, last push {(r.get('pushed_at') or '')[:10]}",
             "", "## Files", "\n".join(p for p in paths[:300] if not people_file.search(p))]
    readme = without_people(finder.readme_of(full_name))
    parts += ["", "## README", PEOPLE_LINE.sub("", analyzer.clean(readme))[:40000]]
    for name in analyzer.MANIFESTS:
        path = next((p for p in paths if p == name or (p.endswith("/" + name) and p.count("/") == 1)), None)
        if path:
            parts += ["", f"## {path}", code_safe(analyzer.raw_file(full_name, path))[:6000]]
    for wf in [p for p in paths if p.startswith(".github/workflows/")][:3]:
        parts += ["", f"## {wf}", code_safe(analyzer.raw_file(full_name, wf))[:3000]]
    vendored = re.compile(r"(showdown|jquery|lodash|moment|bootstrap|\.min\.)", re.I)
    for src in [p for p in analyzer.main_sources(paths, r.get("language")) if not vendored.search(p)]:
        parts += ["", f"## {src}", code_safe(analyzer.raw_file(full_name, src))[:12000]]
    return "\n".join(parts)


# --- Claude API engine -----------------------------------------------------------------

def _client(api_key):
    try:
        import anthropic
    except ImportError:
        raise AIError("the anthropic package is not installed: pip install -r requirements.txt")
    return anthropic.Anthropic(api_key=api_key, max_retries=3, timeout=600)


def _ask(client, usage, system, prompt, schema, effort, max_tokens=16000):
    import anthropic
    try:
        resp = client.beta.messages.create(
            model=MODEL, max_tokens=max_tokens,
            betas=["server-side-fallback-2026-07-01"], fallbacks="default",
            system=system, messages=[{"role": "user", "content": prompt}],
            output_config={"effort": effort, "format": {"type": "json_schema", "schema": schema}},
        )
    except anthropic.AuthenticationError:
        raise AIError("the Anthropic API key was rejected")
    except anthropic.RateLimitError:
        raise AIError("Anthropic rate limit reached; try again shortly")
    except anthropic.APIStatusError as exc:
        raise AIError(f"Claude API error {exc.status_code}: {exc.message}")
    except anthropic.APIConnectionError:
        raise AIError("could not reach the Claude API")
    usage["input"] += resp.usage.input_tokens or 0
    usage["output"] += resp.usage.output_tokens or 0
    if resp.stop_reason == "refusal":
        raise AIError("Claude declined this request")
    if resp.stop_reason == "max_tokens":
        raise AIError("Claude's answer was cut off (max_tokens)")
    text = next((b.text for b in resp.content if b.type == "text"), "")
    return json.loads(text)


SYSTEM = ("You help a freelancer find open-source GitHub projects similar to a client's job, and explain them. "
          "Similar means the same kind of requirement, workflow, tech stack and architecture - not shared words. "
          "Work only from the material you are given; if something is not in it, leave it out. " + NO_PEOPLE)


def run_claude(job, threshold, period, exclude, api_key, log=lambda *_: None):
    client, usage = _client(api_key), {"input": 0, "output": 0}
    job_text = f"Title: {job['title']}\n\nDescription:\n{job['description']}"

    plan = _ask(client, usage, SYSTEM, f"{job_text}\n\nSummarise what the client needs, list the core requirements "
                "and technologies a similar project must share, and write GitHub repository search queries that "
                "would find such projects (product names, platform names, the kind of tool).", PLAN_SCHEMA, "medium")
    log(f"Claude planned {len(plan['queries'])} queries: {'; '.join(plan['queries'])}")

    cands = gather(plan["queries"], period, exclude)
    log(f"Gathered {len(cands)} candidates within {period[0]} – {period[1] or 'now'}")
    if not cands:
        return _finish(job, {"summary": plan["summary"], "queries": plan["queries"], "considered": 0, "repos": []},
                       threshold, period, usage)

    digest = "\n\n".join(f"=== REPO {c['repo']} ===\nDescription: {c['description']}\nLanguage: {c['language']}; topics: "
                         f"{', '.join(c['topics'])}\nREADME (start):\n{c['readme']}" for c in cands)
    judged = _ask(client, usage, SYSTEM, f"{job_text}\n\nCore of the job: {'; '.join(plan['core'])}\n\n"
                  f"Score each repository below for similarity to this job, 0 to 1 (1 = it solves the same problem "
                  f"the same way; 0.7 = same core stack and most requirements; 0.4 = same platform, different "
                  f"problem). Mark SEO pages, portfolios, lists and profile READMEs as not real projects.\n\n{digest}",
                  JUDGE_SCHEMA, "medium")
    by_name = {c["repo"]: c for c in cands}
    scores = sorted((s for s in judged["scores"] if s["repo"] in by_name and s["real_project"]),
                    key=lambda s: -s["similarity"])
    best = scores[0]["similarity"] if scores else 0.0
    picks = [s for s in scores if s["similarity"] >= threshold][:2]
    log(f"Claude judged {len(judged['scores'])}; best {best:.2f}; {len(picks)} at or above {threshold:.2f}")

    repos = []
    for s in picks:
        c = by_name[s["repo"]]
        log(f"Claude analysing {c['repo']}")
        a = _ask(client, usage, SYSTEM, f"{job_text}\n\nAnalyse this repository for the freelancer: its "
                 f"requirements, workflow, tech stack, architecture, know-how and smart approaches, then say how it "
                 f"fits the job. Be specific and concrete.\n\n{bundle(c['repo'], c['_raw'])[:120000]}",
                 ANALYSIS_SCHEMA, "high")
        repos.append({"url": c["url"], "similarity": s["similarity"], "reason": s["reason"], "created": c["created"],
                      "last_push": c["last_push"], "language": c["language"], "description": c["description"],
                      "analysis": a, "fit": a.get("fit")})
    return _finish(job, {"summary": plan["summary"], "queries": plan["queries"], "considered": len(cands),
                         "best_score": best, "repos": repos}, threshold, period, usage)


def _finish(job, raw, threshold, period, usage):
    res = validate({**raw, "engine": f"claude-api ({MODEL})"}, threshold)
    res["usage"] = {**usage, "cost_usd": round(usage["input"] / 1e6 * PRICE_IN + usage["output"] / 1e6 * PRICE_OUT, 4)}
    res["report"] = report_text(job, res, threshold, period)
    return res


# --- hand-off engine -------------------------------------------------------------------

def create_task(job, match, exclude):
    PENDING.mkdir(parents=True, exist_ok=True)
    task = {
        "job_id": job["job_id"], "title": job["title"], "description": job["description"],
        "person": job["person"], "member": match["member"], "channel": job["channel"],
        "threshold": match["threshold"], "period": match["period"], "exclude": sorted(exclude),
        "created_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "rules": "rules/github-search.md",
        "instructions": ("Follow rules/github-search.md. Do what the Claude engine does: summarise the job, search GitHub (python3 ai.py gather), "
                         "judge every candidate 0-1 for similarity of requirements, workflow, tech stack and "
                         "architecture, pick up to two at or above the threshold, analyse each in depth "
                         "(python3 ai.py bundle) into the six sections plus fit, and submit (python3 ai.py submit). "
                         + NO_PEOPLE),
        "result_shape": {"summary": "str", "queries": ["str"], "considered": "int", "best_score": "float",
                         "repos": [{"url": "https://github.com/owner/name", "similarity": "0-1", "reason": "str",
                                    "created": "YYYY-MM-DD", "last_push": "YYYY-MM-DD",
                                    "analysis": {k: ["str"] for k in SECTIONS}, "fit": "str"}]},
    }
    path = PENDING / f"{job['job_id']}.json"
    path.write_text(json.dumps(task, indent=2))
    return path


def close_task(job_id, kind=""):
    src = PENDING / f"{job_id}{kind}.json"
    if src.exists():
        DONE.mkdir(parents=True, exist_ok=True)
        src.replace(DONE / src.name)


# --- missed mode ----------------------------------------------------------------------
# Step 1 analyses the JD (rules/analyzing-step-missed.md), step 2 composes 1-2 past
# projects from that analysis and the member's real work (rules/experience-missed.md).
# The rule files themselves are the instructions, for the Claude API and the session alike.

RULES = ROOT / "rules"
COMPOSE = ".compose"
JD_FIELDS = ["must_have_skills", "tools_technologies", "responsibilities", "seniority_signals", "ats_keywords"]
PROJECT_LISTS = ["tech_stack", "know_how", "highlights"]
PROJECT_TEXTS = ["title", "context", "requirement", "approach", "roadblock", "solution", "result"]

JD_SCHEMA = {
    "type": "object",
    "properties": {
        "must_have_skills": _strs("Skills the client cannot do without"),
        "tools_technologies": _strs("Languages, frameworks, platforms, services and tools"),
        "responsibilities": _strs("What the person will actually do"),
        "seniority_signals": _strs("Words and duties that show the level: lead, own, mentor, architect..."),
        "ats_keywords": _strs("Exact terms an applicant tracking system would scan for, in the client's wording"),
        "real_problem": {"type": "string", "description": "The client's expectation, pain point, key requirement "
                         "or core concept: the real problem to solve, in a few plain sentences"},
    },
    "required": JD_FIELDS + ["real_problem"],
    "additionalProperties": False,
}

EXPERIENCE_SCHEMA = {
    "type": "object",
    "properties": {
        "projects": {
            "type": "array",
            "description": "1 or 2 past projects, first person, past tense",
            "items": {
                "type": "object",
                "properties": {
                    "title": {"type": "string", "description": "One-line project title"},
                    "context": {"type": "string", "description": "What was built, for whom, and why"},
                    "requirement": {"type": "string", "description": "What the client needed, mirroring the job"},
                    "approach": {"type": "string", "description": "How the work was planned and done, key decisions and why"},
                    "tech_stack": _strs("Technologies used, the job's must-haves first"),
                    "know_how": _strs("Practical lessons from doing it: trade-offs, gotchas, patterns that worked"),
                    "roadblock": {"type": "string", "description": "The real difficulty during delivery"},
                    "solution": {"type": "string", "description": "How the roadblock was solved, and why it worked"},
                    "result": {"type": "string", "description": "The outcome for the client"},
                    "highlights": _strs("4 or 6 short, concrete bullets, ending with a measurable result where possible"),
                },
                "required": PROJECT_TEXTS + PROJECT_LISTS,
                "additionalProperties": False,
            },
        }
    },
    "required": ["projects"],
    "additionalProperties": False,
}


def rule(name):
    return (RULES / name).read_text()


# Step 3 (rules/scoring-missed.md): each project is scored against the step 1 analysis.
# Judged parts come from the AI, counted parts are measured here; weights and gates live here too.
SCORE_WEIGHTS = {"real_problem": .30, "must_have_skills": .20, "responsibilities": .15,
                 "tools_technologies": .15, "seniority_signals": .10, "ats_keywords": .10}
JUDGED = ["real_problem", "must_have_skills", "responsibilities", "seniority_signals"]
COUNTED = ["tools_technologies", "ats_keywords"]
MAX_REWRITES = 3
CREDIBILITY_CAP = 0.5

SCORE_SCHEMA = {
    "type": "object",
    "properties": {
        "scores": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "project": {"type": "integer", "description": "1-based project number"},
                    **{k: {"type": "number", "description": "0 to 1"} for k in JUDGED},
                    "credible": {"type": "boolean", "description": "false if it invents specifics the member "
                                 "could not back up (employers, client names, indefensible numbers)"},
                    "feedback": {"type": "string", "description": "What is weak or missing, and how to raise it"},
                },
                "required": ["project"] + JUDGED + ["credible", "feedback"],
                "additionalProperties": False,
            },
        }
    },
    "required": ["scores"],
    "additionalProperties": False,
}


def _clean_analysis(jd):
    if not str(jd.get("real_problem") or "").strip():
        raise AIError("the analysis must name the real problem to solve")
    return {**{k: [scrub(x) for x in jd.get(k) or [] if scrub(x)] for k in JD_FIELDS},
            "real_problem": scrub(jd["real_problem"])}


def _clean_project(p):
    p = p if isinstance(p, dict) else {}
    return {**{k: scrub(p.get(k) or "") for k in PROJECT_TEXTS},
            **{k: [scrub(x) for x in p.get(k) or [] if scrub(x)] for k in PROJECT_LISTS}}


def structure_problems(p):
    out = [f"missing {k}" for k in PROJECT_TEXTS + PROJECT_LISTS if not p[k]]
    if len(p["highlights"]) not in (4, 6):
        out.append(f"{len(p['highlights'])} highlights; it needs 4 or 6")
    return out


def coverage(terms, text):
    """Share of the analysis terms found in the project text (case-insensitive); 1 when there are none."""
    terms = [" ".join(t.lower().split()) for t in terms if t.strip()]
    if not terms:
        return 1.0
    text = " ".join(text.lower().split())
    return sum(t in text for t in terms) / len(terms)


def project_text(p):
    return " ".join([p[k] for k in PROJECT_TEXTS] + [x for k in PROJECT_LISTS for x in p[k]])


def score_project(analysis, p, judged):
    """The step 3 score of one project: the weighted parts, then the gates."""
    judged = judged or {}
    parts = {k: max(0.0, min(1.0, float(judged.get(k) or 0))) for k in JUDGED}
    parts.update({k: coverage(analysis.get(k) or [], project_text(p)) for k in COUNTED})
    total = sum(SCORE_WEIGHTS[k] * v for k, v in parts.items())
    problems = structure_problems(p)
    credible = judged.get("credible", True) is not False
    if problems:
        total = 0.0
    elif not credible:
        total = min(total, CREDIBILITY_CAP)
    feedback = "; ".join(problems + [str(judged.get("feedback") or "")]).strip("; ")
    missing = [t for k in COUNTED for t in analysis.get(k) or [] if coverage([t], project_text(p)) == 0]
    if missing:
        feedback += f" Missing terms: {', '.join(missing)}."
    return {"score": round(total, 3), "parts": {k: round(v, 2) for k, v in parts.items()},
            "credible": credible, "feedback": feedback.strip()}


def score_all(analysis, projects, judged_list):
    by_no = {int(j.get("project") or 0): j for j in judged_list or [] if isinstance(j, dict)}
    return [score_project(analysis, p, by_no.get(i)) for i, p in enumerate(projects, 1)]


def validate_compose(result, threshold, strict=True):
    """Check a missed and scored result from either engine. Raises AIError. With strict,
    every project must pass the structure checks and score at or above the threshold."""
    if not isinstance(result, dict):
        raise AIError("result must be an object")
    analysis = _clean_analysis(result.get("analysis") or {})
    projects = result.get("projects") or []
    if not isinstance(projects, list) or not 1 <= len(projects) <= 2:
        raise AIError("there must be 1 or 2 projects")
    projects = [_clean_project(p) for p in projects]
    scores = score_all(analysis, projects, result.get("scores"))
    if strict:
        for i, p in enumerate(projects, 1):
            if structure_problems(p):
                raise AIError(f"project {i}: {'; '.join(structure_problems(p))}")
        weak = [f"project {i} scores {s['score']:.2f} (parts {s['parts']}): {s['feedback']}"
                for i, s in enumerate(scores, 1) if s["score"] < threshold]
        if weak:
            raise AIError(f"below the member's {threshold:.2f}, rewrite and score again: " + " | ".join(weak))
    return {
        "analysis": analysis, "projects": projects, "scores": scores,
        "below": any(s["score"] < threshold for s in scores),
        "rounds": int(result.get("rounds") or 0),
        "engine": result.get("engine") or "claude-code-session",
        "usage": result.get("usage"),
    }


def compose_text(job, res):
    """The missed reply as readable text."""
    a = res["analysis"]
    lines = [f"Job {job['job_id']}: {job['title']}", "", "The real problem to solve:", a["real_problem"]]
    for k, label in zip(JD_FIELDS, ["Must-have skills", "Tools and technologies", "Responsibilities",
                                    "Seniority signals", "ATS keywords"]):
        if a.get(k):
            lines.append(f"{label}: {', '.join(a[k])}")
    for i, p in enumerate(res["projects"], 1):
        lines += ["", f"Project {i}: {p['title']}", p["context"], "",
                  f"Requirement: {p['requirement']}", f"Approach: {p['approach']}",
                  f"Tech stack: {', '.join(p['tech_stack'])}", "Know-how:"]
        lines += [f"  - {x}" for x in p["know_how"]]
        lines += [f"Roadblock: {p['roadblock']}", f"How it was solved: {p['solution']}", f"Result: {p['result']}", ""]
        lines += [f"• {x}" for x in p["highlights"]]
    return "\n".join(lines).strip()


def own_work_text(repos):
    """The member's real work for this job: the repos GitHub search found, with their analysis."""
    if not repos:
        return "No repos were found for this job, so keep project details general (see the grounding rules)."
    parts = []
    for r in repos:
        parts.append(f"=== {(r.get('name') or r.get('url') or '').rstrip('/').split('/')[-1]} (similarity {r.get('score', 0):.2f}) ===\n"
                     f"{r.get('description') or ''}\nWhy similar: {r.get('reason') or ''}\nFit: {r.get('fit') or ''}\n"
                     + analyzer.as_text(r.get("analysis") or {}))
    return "\n\n".join(parts)


def run_compose(job, repos, threshold, api_key, log=lambda *_: None):
    client, usage = _client(api_key), {"input": 0, "output": 0}
    job_text = f"Title: {job['title']}\n\nDescription:\n{job['description']}"
    system = ("You help a freelancer reply to a client's job. Follow the rules you are given exactly. " + NO_PEOPLE)
    own_work = own_work_text(repos)

    jd = _clean_analysis(_ask(client, usage, system + "\n\n" + rule("analyzing-step-missed.md"),
                              f"{job_text}\n\nAnalyse this job description by the rules.", JD_SCHEMA, "high"))
    log(f"Step 1 done. Real problem: {jd['real_problem'][:160]}")
    context = f"{job_text}\n\n## Step 1 analysis\n{json.dumps(jd, indent=2)}\n\n## The member's real work\n{own_work}"

    exp = _ask(client, usage, system + "\n\n" + rule("experience-missed.md"),
               f"{context}\n\nCompose the experience by the rules. Each project must have exactly 4 or 6 "
               f"highlights.", EXPERIENCE_SCHEMA, "high")
    projects = [_clean_project(p) for p in exp["projects"][:2]]
    log(f"Step 2 done: {len(projects)} project(s)")

    rounds = 0
    while True:
        judged = _ask(client, usage, system + "\n\n" + rule("scoring-missed.md"),
                      f"## Step 1 analysis\n{json.dumps(jd, indent=2)}\n\n## The member's real work\n{own_work}\n\n"
                      f"## Projects\n{json.dumps(projects, indent=2)}\n\nScore every project's AI-judged parts "
                      f"({', '.join(JUDGED)}) by the rules, say whether it is credible, and give feedback.",
                      SCORE_SCHEMA, "high")["scores"]
        scores = score_all(jd, projects, judged)
        weak = [i for i, sc in enumerate(scores) if sc["score"] < threshold]
        log(f"Step 3 round {rounds}: scores " + ", ".join(f"{sc['score']:.2f}" for sc in scores)
            + f" (bar {threshold:.2f})")
        if not weak or rounds >= MAX_REWRITES:
            break
        rounds += 1
        notes = "\n\n".join(f"### Project {i + 1} — score {scores[i]['score']:.2f}, parts {scores[i]['parts']}\n"
                             f"Feedback: {scores[i]['feedback']}\n{json.dumps(projects[i], indent=2)}" for i in weak)
        out = _ask(client, usage, system + "\n\n" + rule("experience-missed.md") + "\n\n" + rule("scoring-missed.md"),
                   f"{context}\n\n## Projects below the bar of {threshold:.2f}\n{notes}\n\nRewrite these "
                   f"{len(weak)} project(s) by the rules, raising their weak parts without breaking the grounding "
                   f"rules. Return them in the same order. Each must have exactly 4 or 6 highlights.",
                   EXPERIENCE_SCHEMA, "high")["projects"]
        for i, p in zip(weak, out):
            projects[i] = _clean_project(p)
        log(f"Step 3: rewrote {len(weak)} project(s) (round {rounds} of {MAX_REWRITES})")

    res = validate_compose({"analysis": jd, "projects": projects, "scores": judged, "rounds": rounds,
                            "engine": f"claude-api ({MODEL})"}, threshold, strict=False)
    res["usage"] = {**usage, "cost_usd": round(usage["input"] / 1e6 * PRICE_IN + usage["output"] / 1e6 * PRICE_OUT, 4)}
    return res


def create_compose_task(job, match, repos):
    PENDING.mkdir(parents=True, exist_ok=True)
    task = {
        "kind": "compose",
        "job_id": job["job_id"], "title": job["title"], "description": job["description"],
        "person": job["person"], "member": match["member"], "channel": job["channel"],
        "threshold": match["threshold"],
        "own_work": own_work_text(repos),
        "created_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "rules": ["rules/missed.md", "rules/analyzing-step-missed.md", "rules/experience-missed.md",
                  "rules/scoring-missed.md"],
        "instructions": ("Follow rules/missed.md. Step 1: analyse the JD by rules/analyzing-step-missed.md. "
                         "Step 2: compose 1-2 projects by rules/experience-missed.md from the job, the step 1 "
                         "output and own_work. Step 3: score each project's judged parts by "
                         "rules/scoring-missed.md and rewrite any below the threshold (the worker counts the "
                         "keyword parts and applies the weights; a submit below the threshold is rejected with "
                         "each project's score and feedback, so rewrite and submit again, up to 3 rewrites). "
                         "Submit with python3 ai.py compose-submit <job> result.json. " + NO_PEOPLE),
        "result_shape": {"analysis": {**{k: ["str"] for k in JD_FIELDS}, "real_problem": "str"},
                         "projects": [{**{k: "str" for k in PROJECT_TEXTS}, "tech_stack": ["str"],
                                       "know_how": ["str"], "highlights": ["str (4 or 6 of them)"]}],
                         "scores": [{"project": "1-based int", **{k: "0-1" for k in JUDGED}, "credible": "bool",
                                     "feedback": "str"}],
                         "rounds": "int: rewrite rounds used"},
    }
    path = PENDING / f"{job['job_id']}{COMPOSE}.json"
    path.write_text(json.dumps(task, indent=2))
    return path


# --- CLI ---------------------------------------------------------------------------------

def _task(job_id):
    path = PENDING / f"{job_id}.json"
    if not path.exists():
        sys.exit(f"no pending task for job {job_id}")
    return json.loads(path.read_text())


def _post(path, payload):
    port = os.environ.get("PORT", "5000")
    req = urllib.request.Request(f"http://127.0.0.1:{port}{path}", data=json.dumps(payload).encode(), method="POST",
                                 headers={"Content-Type": "application/json"})
    try:
        with urllib.request.urlopen(req, timeout=60) as res:
            return json.loads(res.read())
    except urllib.error.HTTPError as err:
        sys.exit(f"rejected: {err.read().decode()}")


def main(argv):
    if not argv or argv[0] not in ("tasks", "gather", "bundle", "submit", "compose-submit"):
        sys.exit(__doc__)
    cmd, args = argv[0], argv[1:]
    out_dir = TASKS / "work"
    out_dir.mkdir(parents=True, exist_ok=True)
    if cmd == "tasks":
        for p in sorted(PENDING.glob("*.json")):
            t = json.loads(p.read_text())
            if t.get("kind") == "compose":
                print(f"{t['job_id']}  [compose]  {t['title']}  (person {t['person']})")
                continue
            print(f"{t['job_id']}  {t['title']}  (person {t['person']}, threshold {t['threshold']}, "
                  f"period {t['period'][0]} – {t['period'][1] or 'now'})")
    elif cmd == "compose-submit":
        job_id, file = args[0], args[1]
        st = _post("/api/ai/compose", {"job_id": job_id, "result": json.loads(Path(file).read_text())})
        c = st["compositions"][job_id]
        print(f"accepted: {c['status']}; reply: {(c.get('reply') or {}).get('status')}")
    elif cmd == "gather":
        t = _task(args[0])
        cands = gather(args[1:], t["period"], t["exclude"])
        path = out_dir / f"{t['job_id']}-candidates.md"
        path.write_text("\n\n".join(f"=== REPO {c['repo']} ===\nurl: {c['url']} | created {c['created']} | last push "
                                    f"{c['last_push']} | {c['language']} | topics: {', '.join(c['topics'])}\n"
                                    f"Description: {c['description']}\nREADME (start):\n{c['readme']}" for c in cands))
        print(f"{len(cands)} candidates -> {path}")
    elif cmd == "bundle":
        for name in args:
            path = out_dir / f"bundle-{name.replace('/', '__')}.md"
            path.write_text(bundle(name))
            print(f"{name} -> {path}")
    elif cmd == "submit":
        job_id, file = args[0], args[1]
        body = json.dumps({"job_id": job_id, "result": json.loads(Path(file).read_text())}).encode()
        port = os.environ.get("PORT", "5000")
        req = urllib.request.Request(f"http://127.0.0.1:{port}/api/ai/result", data=body, method="POST",
                                     headers={"Content-Type": "application/json"})
        try:
            with urllib.request.urlopen(req, timeout=60) as res:
                st = json.loads(res.read())["matches"][job_id]
                print(f"accepted: {st['status']}; reply: {(st.get('reply') or {}).get('status')}")
        except urllib.error.HTTPError as err:
            sys.exit(f"rejected: {err.read().decode()}")


if __name__ == "__main__":
    main(sys.argv[1:])
