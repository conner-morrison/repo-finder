"""Read a GitHub repository and say what it is made of.

Six things are extracted, each a short list of plain statements:

  requirements    what the project does / is for (features, scope, prerequisites)
  workflow        how it is used and run: setup, steps, scripts, CI/CD
  tech_stack      languages, platforms, libraries
  architecture    structure, components, entry points, data and deployment shape
  know_how        tips, caveats, limitations, configuration knowledge
  smart_approach  techniques worth copying: batching, retries, caching, triggers...

Nothing about people is kept: owners, authors, contributors, maintainers,
licences, sponsors, contact details, social links, emails and @handles are
dropped, both as whole README sections and line by line.

Sources: README (split by heading), repo topics and languages, the file tree,
dependency manifests, GitHub Actions workflows and a few main source files.
"""

import json
import re
import urllib.parse

from finder import gh

MAX_ITEMS = 8
MAX_LEN = 280

# README heading -> category. First match wins, so the more specific come first.
SECTIONS = [
    ("exclude", r"licen[cs]e|author|contributor|contributing|credit|acknowledg|thank|sponsor|donat|support( us)?$|"
                r"contact|maintainer|code of conduct|star history|citation|cite|team|about (me|us|the author)|"
                r"follow|hire|backers|funding|social|community|changelog|release notes|security policy"),
    ("tech_stack", r"tech(nology|nologies)?( stack)?|built with|stack|dependenc|libraries|tools used|powered by"),
    ("architecture", r"architect|design|structure|component|module|folder|directory|layout|data model|schema|"
                     r"diagram|overview of the code|internals|under the hood|how it'?s built"),
    ("smart_approach", r"approach|algorithm|strategy|optimi[sz]|performance|technical detail|implementation|"
                       r"key idea|why (this|it) works|how .* works|principle|mechanism"),
    ("know_how", r"tip|note|best practice|faq|troubleshoot|limitation|caveat|gotcha|known issue|lesson|warning|"
                 r"important|security|rate limit|quota|configur|environment|settings|options"),
    ("workflow", r"usage|how to|getting started|quick ?start|setup|install|deploy|run|steps|workflow|process|"
                 r"pipeline|tutorial|example|guide|walkthrough|commands|scripts"),
    ("requirements", r"feature|requirement|prerequisite|what it does|overview|introduction|about|capabilit|"
                     r"use case|why|goal|scope|description|highlights|what'?s included|summary|purpose"),
]

PEOPLE_LINE = re.compile(
    r"\b(author|authored|maintain(er|ed)|contributor|copyright|©|\(c\)|licen[cs]e|sponsor|donat|patreon|"
    r"ko-fi|buy me a coffee|twitter|linkedin|follow me|made (with|by)|created by|written by|developed by|"
    r"contact me|reach me|hire me|my name|star (this|the) repo|give (it|us) a star)\b", re.I)
FIRST_PERSON = re.compile(r"^(so |and |but )?(i|i'm|i've|i'd|my|me)\b", re.I)
EMAIL = re.compile(r"[\w.+-]+@[\w-]+\.[\w.-]+")
HANDLE = re.compile(r"(?<![\w/])@[A-Za-z0-9][A-Za-z0-9-]{1,38}\b")
PROFILE_URL = re.compile(r"https?://(www\.)?(github\.com|twitter\.com|x\.com|linkedin\.com/in|t\.me)/[A-Za-z0-9_.-]+/?(?=[\s)\]]|$)")

# Things in the code or README that say how the problem was solved.
APPROACHES = [
    (r"batch(ing|es|Update|Get)?\b|bulk", "Works in batches / bulk calls instead of one item at a time"),
    (r"retry|retries|backoff|exponential", "Retries failed calls with backoff"),
    (r"rate.?limit|throttl|quota|Utilities\.sleep|sleep\(", "Respects rate limits and quotas (throttling / pauses)"),
    (r"cach(e|ing)|CacheService|memoi[sz]", "Caches results to avoid repeated work"),
    (r"paginat|nextPageToken|page_token|per_page|next_cursor", "Pages through large result sets"),
    (r"idempoten|upsert|already (exists|processed)|skip (existing|duplicates)", "Idempotent: safe to run again (upserts / skips done work)"),
    (r"\b(md5|sha-?1|sha-?256|hash(ing|es)?|checksum|fingerprint)\b", "Detects duplicates by hashing / fingerprints"),
    (r"incremental|last.?(run|sync|modified)|checkpoint|resume from", "Incremental processing with checkpoints (resumes where it stopped)"),
    (r"webhook", "Event-driven through webhooks"),
    (r"job queue|task queue|celery|bullmq|sidekiq|rabbitmq|\bsqs\b|redis queue", "Queues work for background workers"),
    (r"ScriptApp\.newTrigger|time-driven|cron|schedule[dr]?\b|setInterval", "Runs on a schedule / time-driven triggers"),
    (r"dry.?run|preview mode|simulate", "Has a dry-run mode to preview changes safely"),
    (r"Promise\.all|concurren|parallel|asyncio\.gather|thread ?pool|worker_threads", "Runs independent work concurrently"),
    (r"PropertiesService|\.env\b|dotenv|environment variable|config\.(json|ya?ml)", "Keeps settings and secrets in configuration, not code"),
    (r"oauth|refresh.?token|access.?token", "Handles OAuth tokens (including refresh)"),
    (r"\blogger\b|logging\.|audit (log|trail)|Logger\.log", "Logs what it did for auditing / debugging"),
    (r"map(ping)?\s+(fields|columns)|field.?map|column.?map", "Uses explicit field mappings between systems"),
    (r"\bcsv\b|export(ing)? (to|as)|import(ing)? (from|into)", "Moves data through import / export (CSV, files)"),
    (r"schema validation|\bzod\b|pydantic|\bjoi\b|\bajv\b|jsonschema", "Validates data against a schema before using it"),
    (r"\bmcp\b|model context protocol|tool(s)? for (ai|llm)", "Exposes capabilities as MCP tools for AI assistants"),
]

MANIFESTS = ["package.json", "requirements.txt", "pyproject.toml", "composer.json", "appsscript.json",
             "go.mod", "Gemfile", "Cargo.toml", ".clasp.json", "docker-compose.yml", "docker-compose.yaml"]

MARKERS = [  # file / folder -> what it says about the architecture
    (r"(^|/)Dockerfile$", "Containerised (Dockerfile)"),
    (r"(^|/)docker-compose\.ya?ml$", "Multi-service setup with docker-compose"),
    (r"^\.github/workflows/", "CI/CD with GitHub Actions"),
    (r"(^|/)serverless\.ya?ml$|(^|/)netlify\.toml$|(^|/)vercel\.json$", "Serverless / hosted deployment config"),
    (r"(^|/)appsscript\.json$", "Google Apps Script project (manifest with scopes and services)"),
    (r"(^|/)\.clasp\.json$", "Developed locally and pushed with clasp"),
    (r"(^|/)(migrations?|alembic)/", "Database migrations"),
    (r"(^|/)prisma/schema\.prisma$", "Prisma ORM data model"),
    (r"\.tf$", "Infrastructure as code (Terraform)"),
    (r"(^|/)(tests?|__tests__|spec)/", "Separate test suite"),
    (r"(^|/)(api|routes|controllers)/", "API layer (routes / controllers)"),
    (r"(^|/)(services?|lib|core)/", "Service / core logic layer"),
    (r"(^|/)(models?|entities|schemas?)/", "Explicit data models"),
    (r"(^|/)(components|pages|app)/", "Front-end components / pages"),
    (r"(^|/)(workers?|jobs|tasks|queues?)/", "Background workers / jobs"),
    (r"(^|/)(tools)/", "Tool modules (one file per capability)"),
    (r"(^|/)(scripts|bin)/", "Helper scripts"),
    (r"(^|/)\.env\.example$", "Configuration through environment variables (.env.example)"),
    (r"(^|/)(n8n|workflows?)/.*\.json$", "Automation workflows exported as JSON (n8n / Make)"),
]


def clean(text):
    text = re.sub(r"<!--.*?-->", " ", text, flags=re.S)
    text = re.sub(r"!\[[^\]]*\]\([^)]*\)", " ", text)                    # images / badges
    text = re.sub(r"\[!\[[^\]]*\]\([^)]*\)\]\([^)]*\)", " ", text)
    text = re.sub(r"<img[^>]*>|<[^>]+>", " ", text)                       # html
    text = re.sub(r"\[([^\]]+)\]\((https?://[^)]+)\)", r"\1", text)       # keep link text
    text = PROFILE_URL.sub(" ", text)
    # A repo address names its owner; the repo's own url travels separately.
    text = re.sub(r"(git@github\.com:|https?://(www\.)?github\.com/)[\w.-]+/[\w.-]+[^\s)\]'\"`]*",
                  "<repo url>", text)
    text = re.sub(r"https?://raw\.githubusercontent\.com/[\w.-]+/\S+", "<repo file>", text)
    text = EMAIL.sub(" ", text)
    text = HANDLE.sub(" ", text)
    return text


def statements(body):
    """A section as short statements: its bullet points, or its sentences."""
    body = re.sub(r"```.*?```", lambda m: " " if m.group(0).count("\n") > 6 else
                  " `" + " ".join(m.group(0).strip("`").split()[:12]) + "` ", body, flags=re.S)
    out = []
    for raw in body.splitlines():
        line = raw.strip()
        if not line or line.startswith("|") and set(line) <= set("|-: "):
            continue
        line = re.sub(r"^([-*+]|\d+[.)])\s+", "", line)
        line = re.sub(r"^\|\s*|\s*\|$", "", line).replace(" | ", " — ")
        line = re.sub(r"[*_`#>]+", "", line).strip(" :-—")
        line = " ".join(line.split())
        if (len(line) < 12 or PEOPLE_LINE.search(line) or not re.search(r"[A-Za-z]{3}", line)
                or FIRST_PERSON.match(line) or re.fullmatch(r"\[!\w+\]", line)
                or (" — " in line and len(line.split()) <= 4)            # a table's header row
                or re.match(r"^\W*(download|star|watch|fork)\b", line, re.I)):
            continue
        for sentence in re.split(r"(?<=[.!?])\s+(?=[A-Z])", line) if len(line) > MAX_LEN else [line]:
            if len(sentence) >= 12:
                out.append(sentence[:MAX_LEN].rstrip() + ("…" if len(sentence) > MAX_LEN else ""))
    return out


def readme_sections(readme):
    """(category, statements) for every README section worth keeping."""
    parts, heading, buf = [], "overview", []
    for line in clean(readme).splitlines():
        m = re.match(r"^\s{0,3}#{1,6}\s+(.*)", line)
        if m:
            parts.append((heading, "\n".join(buf)))
            heading, buf = m.group(1).strip(" #"), []
        else:
            buf.append(line)
    parts.append((heading, "\n".join(buf)))
    out = []
    for heading, body in parts:
        h = re.sub(r"[^\w\s']", " ", heading.lower()).strip()
        category = next((c for c, pat in SECTIONS if re.search(pat, h)), "requirements" if h == "overview" else None)
        if category and category != "exclude":
            out.append((category, statements(body)))
    return out


def tree_of(full_name, branch):
    data = gh(f"/repos/{full_name}/git/trees/{branch}", {"recursive": "1"}) or {}
    return [t["path"] for t in data.get("tree", []) if t.get("type") == "blob"][:5000]


def raw_file(full_name, path):
    try:
        # paths can hold spaces or other characters a URL cannot carry as-is
        return gh(f"/repos/{full_name}/contents/{urllib.parse.quote(path)}",
                  accept="application/vnd.github.raw+json") or ""
    except RuntimeError:
        return ""


def deps_from(name, text):
    """Dependency names from a manifest, plus anything it tells about how it is run."""
    deps, notes = [], []
    try:
        if name == "package.json":
            pkg = json.loads(text)
            deps = list((pkg.get("dependencies") or {})) + list((pkg.get("devDependencies") or {}))
            for k, v in list((pkg.get("scripts") or {}).items())[:8]:
                notes.append(("workflow", f"npm run {k}: {v}"[:MAX_LEN]))
        elif name == "composer.json":
            deps = [d for d in (json.loads(text).get("require") or {}) if d != "php"]
        elif name == "appsscript.json":
            man = json.loads(text)
            for s in man.get("oauthScopes") or []:
                notes.append(("architecture", "Needs scope " + s.rsplit("/", 1)[-1]))
            for svc in (man.get("dependencies") or {}).get("enabledAdvancedServices") or []:
                deps.append(f"Advanced service: {svc.get('serviceId')}")
            if man.get("webapp"):
                notes.append(("architecture", "Deployed as an Apps Script web app"))
            if man.get("runtimeVersion"):
                notes.append(("tech_stack", f"Apps Script runtime {man['runtimeVersion']}"))
        elif name == "requirements.txt":
            deps = [re.split(r"[<>=~!\[ ;]", l.strip())[0] for l in text.splitlines()
                    if l.strip() and not l.startswith(("#", "-"))]
        elif name == "pyproject.toml":
            block = re.search(r"dependencies\s*=\s*\[(.*?)\]", text, re.S)
            deps = re.findall(r"[\"']([A-Za-z0-9_.\-]+)", block.group(1)) if block else []
        elif name == "go.mod":
            deps = re.findall(r"^\s*([\w.\-/]+)\s+v[\d.]+", text, re.M)
        elif name in ("Gemfile",):
            deps = re.findall(r"^\s*gem\s+['\"]([^'\"]+)", text, re.M)
        elif name == "Cargo.toml":
            block = re.search(r"\[dependencies\](.*?)(\n\[|$)", text, re.S)
            deps = re.findall(r"^\s*([\w\-]+)\s*=", block.group(1), re.M) if block else []
        elif name.startswith("docker-compose"):
            svcs = re.findall(r"^\s{2}([\w\-]+):\s*$", text, re.M)
            if svcs:
                notes.append(("architecture", "Services: " + ", ".join(svcs[:10])))
        elif name == ".clasp.json":
            notes.append(("workflow", "Edit locally, then `clasp push` to deploy the script"))
    except (ValueError, AttributeError):
        pass
    return [d for d in deps if d][:25], notes


def main_sources(paths, language):
    """A handful of files most likely to hold the real logic."""
    exts = {"JavaScript": (".js", ".mjs", ".gs"), "TypeScript": (".ts",), "Python": (".py",), "PHP": (".php",),
            "Go": (".go",), "Ruby": (".rb",), "Java": (".java",), "C#": (".cs",),
            "Blade": (".php",), "Vue": (".vue", ".js", ".ts")}.get(language, (".js", ".ts", ".py", ".gs", ".php"))
    skip = re.compile(r"(^|/)(node_modules|vendor|dist|build|test|tests|__tests__|\.github|docs?|examples?)/|\.min\.|\.d\.ts$|config")
    cands = [p for p in paths if p.endswith(exts) and not skip.search(p)]
    pri = lambda p: (p.count("/"), not re.search(r"(main|index|app|server|code|core|sync|migrat|service)", p, re.I), len(p))
    return sorted(cands, key=pri)[:5]


def add(bucket, items):
    for it in items:
        it = " ".join(str(it).split())
        if it and it.lower() not in {x.lower() for x in bucket} and len(bucket) < MAX_ITEMS:
            bucket.append(it)


def analyze(repo, readme):
    """`repo` is a GitHub search item; `readme` its README text."""
    full = repo["full_name"]
    out = {k: [] for k in ("requirements", "workflow", "tech_stack", "architecture", "know_how", "smart_approach")}
    if repo.get("description"):
        add(out["requirements"], [clean(repo["description"]).strip()])
    for category, items in readme_sections(readme):
        add(out[category], items)

    langs = gh(f"/repos/{full}/languages") or {}
    total = sum(langs.values()) or 1
    add(out["tech_stack"], [f"{k} ({round(v * 100 / total)}%)" for k, v in sorted(langs.items(), key=lambda kv: -kv[1])[:5]])
    if repo.get("topics"):
        add(out["tech_stack"], ["Topics: " + ", ".join(repo["topics"][:10])])

    paths = tree_of(full, repo.get("default_branch") or "main")
    top = sorted({p.split("/")[0] + ("/" if "/" in p else "") for p in paths
                  if not re.match(r"(licen[cs]e|copying|authors|contributors|code_of_conduct|funding)", p, re.I)})
    if top:
        out["architecture"].insert(0, "Layout: " + ", ".join(top[:14]) + (" …" if len(top) > 14 else ""))
    for pat, what in MARKERS:
        if any(re.search(pat, p) for p in paths):
            add(out["architecture"], [what])

    for name in MANIFESTS:
        path = next((p for p in paths if p == name or p.endswith("/" + name) and p.count("/") == 1), None)
        if not path:
            continue
        deps, notes = deps_from(name, raw_file(full, path))
        if deps:
            add(out["tech_stack"], [f"{name}: " + ", ".join(deps[:15]) + (" …" if len(deps) > 15 else "")])
        for cat, note in notes:
            add(out[cat], [note])

    for wf in [p for p in paths if p.startswith(".github/workflows/")][:3]:
        text = raw_file(full, wf)
        name = re.search(r"^name:\s*(.+)$", text, re.M)
        on = re.search(r"^on:\s*(.+)$", text, re.M)
        add(out["workflow"], [f"CI: {(name.group(1) if name else wf.rsplit('/', 1)[-1]).strip()}"
                              + (f" (on {on.group(1).strip()})" if on else "")])

    code = "\n".join(raw_file(full, p)[:60000] for p in main_sources(paths, repo.get("language")))
    haystack = f"{readme}\n{code}"
    for pat, what in APPROACHES:
        if re.search(pat, haystack, re.I):
            add(out["smart_approach"], [what])
    if code:
        funcs = re.findall(r"(?:function|def|async function)\s+([A-Za-z_]\w{3,})", code)
        if funcs:
            add(out["architecture"], ["Main functions: " + ", ".join(dict.fromkeys(funcs))[:MAX_LEN]])
    return {k: v for k, v in out.items() if v}


def as_text(analysis):
    """The analysis as one readable description."""
    titles = {"requirements": "Project requirements", "workflow": "Workflow", "tech_stack": "Tech stack",
              "architecture": "Architecture", "know_how": "Know-how", "smart_approach": "Smart approach"}
    return "\n\n".join(f"{titles[k]}:\n" + "\n".join(f"- {i}" for i in v) for k, v in analysis.items())
