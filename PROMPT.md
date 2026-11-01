# Build prompt: repo-finder

> **How to use.** Start Claude Code (model **Opus 5.5**, effort **Medium**) in an empty folder and paste everything below the line as your first message.
> The prompt pins every fixed piece word for word: rules, lexicons, regexes, JSON schemas, Claude prompts, HTML, CSS and UI strings. It also describes the logic down to constants and edge cases.
> Code written from a spec won't match the original byte for byte, but its behaviour, data shapes, endpoints and UI will. If you need byte-identical files, copy the repository instead.

---

You are building **repo-finder** from scratch in the current (empty) directory. Build exactly what is specified here: no extra features, files, tests or dependencies, and no renamed identifiers or changed wording. Wherever a block is marked **verbatim**, copy it character for character. Where the logic is described in prose, implement it exactly as described, including the constants. Work through the phases in order and run each phase's checks before you move on.

## 0. What it is

repo-finder is a local worker for a relay workspace (a chat-like job board). The worker:

1. registers itself with a relay server and waits to be approved and added to a channel;
2. long-polls the relay for messages; each message can be a **job** posting (`{job, title, person, description}`);
3. for every job whose `person` is a **registered member**, works in one of two **work modes**:
   - **GitHub search**: finds 1–2 similar GitHub repos created and last pushed inside the member's period, scores them 0–1 against the job, analyses them, and replies to the job on its channel;
   - **Missed**: analyses the JD, writes 1–2 first-person past projects grounded on those repos, scores them against the analysis, rewrites any below the member's threshold (up to 3 times), and replies;
4. runs the AI part with one of four **engines**: Claude API, a Claude Code session hand-off (files in `ai_tasks/`), Automatic (the session in debug mode, the API otherwise), or Rules only (no AI);
5. serves a local web UI at `http://127.0.0.1:5000` with Workspace, Report and Profile tabs;
6. keeps all state in `state.json` so a restart resumes where it stopped.

Hard constraints:
- **Python 3.8+ standard library only.** The one optional dependency is the `anthropic` SDK, imported lazily and only by the Claude API engine. If a `.deps/` folder exists next to `ai.py`, put it at the front of `sys.path`.
- Never include anything about people in analyses or replies: no owners, authors, contributors, maintainers, licences, sponsors, emails, @handles or profile URLs. The GitHub account name inside a repo URL is the only exception.
- The frontend is plain HTML, CSS and JS, with no build step and no libraries.

## 1. File layout

```
app.py              worker + local HTTP server + debug reloader
ai.py               AI engine (Claude API + session hand-off + CLI) and Missed mode
finder.py           rules-only GitHub search and word-matching similarity score
analyzer.py         rules-only repo analysis (README sections, tree, manifests, CI, sources)
requirements.txt
.gitignore
README.md
rules/github-search.md
rules/missed.md
rules/analyzing-step-missed.md
rules/experience-missed.md
rules/scoring-missed.md
static/index.html
static/style.css
static/app.js
static/favicon.svg
```

Created at runtime (git-ignored): `state.json`, `state.tmp`, `__pycache__/`, `.deps/`, `ai_tasks/{pending,done,work}/`, `ai_tasks/session_heartbeat`.

## Phase 1: Static files (verbatim)

Create these files with exactly this content.

`requirements.txt`
~~~~text
anthropic>=1.15
~~~~

`.gitignore`
~~~~text
state.json
state.tmp
__pycache__/
.deps/
ai_tasks/
~~~~

`static/favicon.svg`
~~~~xml
<!-- Magnifier glyph from Lucide "search" (https://lucide.dev), ISC License, (c) Lucide Contributors -->
<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 32 32">
  <rect width="32" height="32" rx="8" fill="#3451d1"/>
  <g transform="translate(4 4)" fill="none" stroke="#fff" stroke-width="2.6" stroke-linecap="round" stroke-linejoin="round">
    <circle cx="11" cy="11" r="7"/>
    <path d="m21 21-4.8-4.8"/>
  </g>
</svg>
~~~~

`rules/github-search.md`
~~~~markdown
# GitHub search rules

Work mode: **GitHub search**. These rules apply to every related job while this mode is on.

## 1. Which jobs are related

- A job is related when its `person` matches a registered member's name.
- Only the person decides this. The job's post date does not matter.
- A member without a similarity value is skipped until one is set.

## 2. The member's period

- The member's From–To months are the rule for repos, not for jobs.
- A repo qualifies only if it was **created** and **last pushed** inside that window: created on or after the first day of From, last push on or before the last day of To.
- Repos outside the window are never picked, however similar they are.

## 3. Finding candidates

- Summarise the job first: what is built, the main workflow, the tech stack, the architecture.
- Search GitHub with many specific queries (about 12 per round) covering the product, the stack and the key techniques. Search names, descriptions, topics and READMEs.
- Run a second, more targeted round when the first one is thin.
- Drop spam, portfolios, profile repos, awesome-lists, course notes and tutorials, and repos with almost no code.
- A repo another worker already picked may be picked again.

## 4. Scoring similarity (0–1)

Judge requirements, workflow, tech stack and architecture together:

| Score | Meaning |
|---|---|
| 1.0 | Same problem solved the same way |
| ~0.7 | Same core stack and most of the requirements |
| ~0.5 | Covers one side of the job |
| ~0.4 | Same platform, different problem |

- Pick **1 or 2** repos at or above the member's similarity value.
- If two repos are picked, they should cover different parts of the job.
- Prefer production-grade code when the job asks for real experience; skip demo-grade repos then.
- If nothing reaches the value, reply with no repos rather than lowering the bar.

## 5. Analysis for each pick

Read the README and the main source files. Write these sections:

- **requirements** — what the project has to do
- **workflow** — how it works, step by step
- **tech_stack** — languages, frameworks, services
- **architecture** — how the code is organised
- **know_how** — practical lessons visible in the code
- **smart_approach** — how to apply it to this job

Also write a one-line **reason** (why it matches, and what it does not cover) and a one-line **fit**.

## 6. Never include people

- Never include authors, owners, contributors, maintainers, sponsors, emails or any other person information.
- Never include the client's or the company's name from the job post.
- The GitHub account name inside the repo URL is the only exception.
- Check the reply for names before it is sent.

## 7. Reply

- Send the reply to the server for the job's `job_id`, with each repo's URL attached to its description.
- Do not send the same repos twice for the same job.
~~~~

`rules/missed.md`
~~~~markdown
# Missed rules

Work mode: **Missed**. These rules apply to every related job while this mode is on.

## 1. Which jobs are related

- Same as GitHub search: a job is related when its `person` matches a registered member's name.
- The member's **similarity** value is the bar every missed project must reach in step 3. A member without one is skipped until it is set.
- The period only shapes which repos count as the member's real work.

## 2. The pipeline

Every related job goes through these steps in order. Each step's output is the next step's input.

1. **Analyze the JD**: follow [analyzing-step-missed.md](analyzing-step-missed.md).
   - Input: the job's title and description.
   - Output: must-have skills, tools and technologies, responsibilities, seniority signals, ATS keywords, and the real problem to solve.
2. **Compose the experience**: follow [experience-missed.md](experience-missed.md).
   - Input: the job, the step 1 output, and the member's real work (the repos the GitHub search step found for this job, if any).
   - Output: 1 or 2 projects, each with project, requirement, approach, tech stack, know-how, roadblock, how it was solved, result and an even number (4 or 6) of highlight bullets.
3. **Score and rewrite**: follow [scoring-missed.md](scoring-missed.md).
   - Input: the step 1 analysis and the step 2 projects.
   - Every project is scored 0–1 against the analysis. Any project below the member's similarity value is rewritten and scored again, up to 3 rewrites.
   - Output: the passing projects, with each one's score, part scores and feedback.
4. **Reply to the job**: post the result to the job's channel (`POST {server}/publish`) as a reply carrying the `job_id`, the same way GitHub search mode replies.
   - The reply holds the step 1 analysis, the step 3 projects with their scores, and the whole thing as readable text.
   - A job whose projects are still below the bar after 3 rewrites isn't posted. It's marked *below score*.

## 3. Engines

- **Claude API**: Claude runs steps 1–3, with each rule file as its instructions. The worker counts the keyword parts, applies the weights and gates, runs the rewrite loop and posts the reply.
- **Claude Code session**: the worker writes the job to `ai_tasks/pending/<job>.compose.json`. The session runs steps 1–3 by the rule files and hands back the result, with its judged scores, using `python3 ai.py compose-submit <job> result.json`. The worker scores it again. A result below the bar is rejected with each project's score and feedback, so the session rewrites and submits again.
- **Rules only**: can't compose. Missed needs an AI engine.

## 4. Checks before replying

- Step 1 must name the real problem to solve.
- Step 2 must have 1 or 2 projects, each with every section filled and 4 or 6 highlight bullets.
- Step 3: every project must score at or above the member's similarity value.
- A job is missed once. Use *Compose again* on the job card to redo it.
~~~~

`rules/analyzing-step-missed.md`
~~~~markdown
# Missed step 1: Analyzing the job description

Work mode: **Missed**. This is the first step of every missed job. Finish it before writing anything.

## 1. What to pull out

- **Must-have skills**: the abilities the client cannot do without.
- **Tools and technologies**: languages, frameworks, platforms, services, versions.
- **Responsibilities**: what the person will actually do day to day.
- **Seniority signals**: words such as "lead", "own", "mentor", "architect", "drive", "from scratch", "end to end".
- **ATS keywords**: the exact terms an applicant tracking system will scan for. Keep the client's own spelling and wording.

## 2. Don't trust the order or the labels

- The real priority is often not where the JD puts it. A key point can sit at the end, in a side remark, or in a single sentence.
- Labels such as "requirement" and "nice to have" do not settle what matters. A "nice to have" can be the real reason for the hire, and a listed "requirement" can be boilerplate.
- Read the whole JD before ranking anything.

## 3. Think from the client's side

This is the most important part of the step.

- Ask: why is the client hiring now? What is broken, slow, missing or risky for them?
- Find the real problem to solve. It can show up as:
  - the client's **expectation** (what success looks like to them)
  - their **pain point** (what hurts today)
  - the **key requirement** (the one thing the hire must deliver)
  - the **core concept** (the central idea the product or work turns on)
- Look for clues: repeated words, emphasis, specific failures they mention, deadlines, the state of the existing system, who the person reports to.

## 4. Output of this step

- Must-have skills
- Tools and technologies
- Responsibilities
- Seniority signals
- ATS keywords
- **The real problem to solve**: the client's expectation, pain point, key requirement or core concept, in a few plain sentences. Everything missed later is built on this.
~~~~

`rules/experience-missed.md`
~~~~markdown
# Missed step 2: Experience

Work mode: **Missed**. This step runs after [analyzing-step-missed](analyzing-step-missed.md). It writes past project experience that matches the job.

## 1. Goal

- Write **1 or 2** past projects, told in the first person, that solved the same kind of problem the job describes.
- Each project must answer the **real problem to solve** found in the analyzing step: the client's expectation, pain point, key requirement or core concept. That problem drives the story, not the order of the JD.
- If two projects are written, they should cover different sides of the job. One might match the core product and the other the hardest technical part.

## 2. Ground every project in real work

- Build each project on the member's real work: the repos picked in the GitHub search step, plus anything the member has told us.
- Don't make up employers, client names, team sizes or numbers that the member can't back up in an interview.
- When a detail isn't known, describe it in general terms ("a mid-size logistics client") instead of inventing a specific one.
- Numbers in results should be realistic and defensible. Round, modest figures are better than impressive ones that can't be explained.

## 3. Structure of each project

Write the sections in this order:

1. **Project**: a one-line title and the context: what was built, for whom, and why.
2. **Requirement**: what the client needed. Mirror the job's requirements and use the JD's own keywords where they fit naturally.
3. **Approach**: how the work was planned and carried out: architecture, phases, key decisions and why they were made.
4. **Tech stack**: languages, frameworks, services and tools. Put the JD's must-have technologies first.
5. **Know-how**: the practical lessons that only come from doing it, such as trade-offs, gotchas and the patterns that worked.
6. **Roadblock**: one or two real difficulties during delivery. Pick ones close to the client's pain point.
7. **How it was solved**: what was tried, what worked, and why.
8. **Result**: the outcome for the client in plain words, such as speed, cost, reliability or adoption.
9. **Highlights**: closing bullets with an **even number** of items (4 or 6). Each bullet is short, concrete and, where possible, ends with a measurable result.

## 4. Style

- First person, past tense, plain and specific. No buzzword piles.
- Show seniority through actions that match the JD's signals: "led", "owned", "mentored", "designed from scratch".
- Work the ATS keywords in naturally. Don't list them for their own sake.
- Keep each project readable in about a minute.
~~~~

`rules/scoring-missed.md`
~~~~markdown
# Missed step 3: Scoring the experience

Work mode: **Missed**. This step runs after [experience-missed](experience-missed.md). It scores each missed project against the output of [analyzing-step-missed](analyzing-step-missed.md). A project that scores too low is rewritten until it passes.

## 1. The bar

- The bar is the person's profile value: the **similarity** set for the member in *Profile → Registered members* (0–1).
- **Every** project must score at or above the bar. One project passing doesn't carry the other.
- A member without a similarity value is skipped until one is set.

## 2. What each project is scored on

Each project gets a score from 0 to 1 for each part below. The final score is the weighted sum.

| Part | Weight | 1.0 means | How it's judged |
|---|---|---|---|
| Real problem | 30 % | The project clearly solved the client's real problem: their expectation, pain point, key requirement or core concept | AI judgement |
| Must-have skills | 20 % | Every must-have skill is shown in action, not just named | AI judgement |
| Responsibilities | 15 % | The project covers what the person will actually do in the job | AI judgement |
| Tools and technologies | 15 % | The JD's tools and technologies appear in the project | Counted: share of the terms found in the project text |
| Seniority signals | 10 % | The project shows the level the JD asks for ("led", "owned", "mentored"...) through real actions | AI judgement |
| ATS keywords | 10 % | The JD's ATS keywords appear in the project, in the client's wording | Counted: share of the terms found in the project text |

- **Counted** parts are measured, not judged: the share of the analysis terms found in the project text (case-insensitive). With no terms in the analysis, the part scores 1.
- **AI judgement** parts are scored by reading the project against the analysis:

| Score | Meaning |
|---|---|
| 1.0 | Fully and specifically covered |
| ~0.7 | Mostly covered, with small gaps |
| ~0.4 | Touched on, but vague or only in passing |
| 0 | Missing |

## 3. Gates

- **Structure**: a project missing any section, or without 4 or 6 highlights, scores **0**.
- **Credibility**: a project with invented specifics the member can't back up, against the grounding rules in [experience-missed](experience-missed.md) (made-up employers, client names or numbers that can't be defended), is **capped at 0.5**.

## 4. Rewrite until it passes

1. Score every project.
2. If every project is at or above the bar, the step is done. Go to the reply.
3. Otherwise, rewrite **only the projects below the bar**:
   - Use the feedback from scoring: which parts were weak and what was missing.
   - Raise the weak parts. Address the real problem more directly, show the missing must-haves in action, and work in the missing tools and ATS keywords naturally.
   - Never raise a score by breaking the grounding rules. Keyword stuffing and invented facts are not allowed.
   - Keep the passing projects as they are.
4. Score again, and repeat.
5. Stop after **3 rewrite rounds**. If a project is still below the bar, don't post the reply. Mark the job *below score* with the best attempt kept, so it can be checked and missed again by hand.

## 5. Output of this step

- For each project: the final score, the score of each part, and short feedback.
- The number of rewrite rounds used.
~~~~

`README.md`
~~~~markdown
# repo-finder

A local worker for a relay workspace. It registers with the relay, receives job
postings, and for every job that belongs to a registered member it finds
similar GitHub repositories, analyses them, and replies to the job with what
it found.

Python 3.8+ standard library only — nothing to install.

## Run

    python3 app.py

Open http://127.0.0.1:5000. Debug mode is on by default: verbose logs, the last
raw server response on the Report tab, and auto-restart when a source file
changes. `DEBUG=0 python3 app.py` turns it off; `PORT=` / `HOST=` change the address.

GitHub is searched with `GITHUB_TOKEN` (or `GH_TOKEN`), or the token of a
logged-in `gh` CLI if there is one; without a token it still works, at
GitHub's lower anonymous rate limit.

## Tabs

- **Workspace** — incoming jobs as cards (title, person, job id, *View JD*), and
  under each related job the similar repos found, their scores and analysis.
- **Report** — counters, activity log and, in debug mode, the last server response.
- **Profile** — server registration, worker profile, identity, and
  **registered members**. Every panel can be collapsed.

## Registration

1. *Register to server* → confirm → `POST {server}/enrol` with the worker's
   name. Status: **waiting**.
2. Someone approves it in the relay console. The worker learns this by
   long-polling `GET {server}/enrol/{ticket}?wait=25` (the relay can't call a
   local app) and receives its **enrolled id**, sent from then on as
   `Authorization: Bearer <id>`. Status: **no channel** (warning).
3. When the relay adds the worker to a channel (a `joined` notice on
   `GET {server}/messages`), status becomes **active** and jobs start arriving.

## Members and matching

A member has a name, a period (*from* / *to* month, or still current) and a
**similarity** threshold from 0 to 1.

A job is **related** to a member when the job's `person` is that member; the
member's period is the rule for which repos may answer it. A repost of a job without a
person takes it from another post with the same job id.

For each related job the worker:

1. searches GitHub with terms drawn from the job (products, platforms,
   requirements, architecture), keeping only repos **created and last pushed
   inside the member's period**;
2. scores each candidate 0–1 against the job — tech stack 40 %, requirements
   40 %, architecture 20 % — from its name, description, topics, language and
   README ([finder.py](finder.py));
3. keeps the best one or two at or above the member's threshold;
4. analyses each into *project requirements, workflow, tech stack,
   architecture, know-how* and *smart approach* from its README, file tree,
   manifests, CI workflows and main source files ([analyzer.py](analyzer.py)).
   Nothing about people is kept: owners, authors, contributors, licences,
   sponsors, contact details and handles are removed;
5. replies on the job's channel (`POST {server}/publish`) with the `job_id`,
   and each repo's url, similarity, dates and analysis.

## Missed mode

*Profile → Work mode → Missed* switches from finding repos to missed a
reply ([rules/missed.md](rules/missed.md)). For each related job:

1. **Analyse the JD** by [rules/analyzing-step-missed.md](rules/analyzing-step-missed.md):
   must-have skills, tools, responsibilities, seniority signals, ATS keywords and
   the real problem to solve;
2. **Compose the experience** by [rules/experience-missed.md](rules/experience-missed.md):
   1–2 past projects grounded on the repos GitHub search found for the job, each
   with 4 or 6 highlight bullets;
3. **Score and rewrite** by [rules/scoring-missed.md](rules/scoring-missed.md):
   each project is scored 0–1 against the analysis (real problem 30 %, must-haves
   20 %, responsibilities 15 %, tools 15 %, seniority 10 %, ATS keywords 10 %;
   tools and keywords are counted, the rest judged by the AI). Any project below
   the member's similarity value is rewritten and scored again, up to 3 times;
   if it's still below, the job is marked *below score* and not posted;
4. **Reply** on the job's channel (`POST {server}/publish`, event
   `missed_experience`) with the `job_id`, the analysis, the projects and the
   readable text.

It runs on the Claude API or a Claude Code session (`python3 ai.py compose-submit
<job> result.json`); *Rules only* can't compose. A job is missed once —
*Compose again* on its card redoes it.

## AI engine

*Profile → AI engine* chooses who does the search, judging, analysis and
report ([ai.py](ai.py)):

- **Claude API** (`claude-opus-5-5`): Claude reads the job and writes the
  search queries; the worker runs them on GitHub inside the member's period;
  Claude scores every candidate 0–1 with a reason (and rejects SEO pages and
  portfolios), analyses the picks from their README, manifests, CI and source,
  and the worker assembles the report. Needs an Anthropic API key — paste it in
  the panel or set `ANTHROPIC_API_KEY`. Roughly $1 per related job.
- **Automatic** (default): in debug mode the Claude Code session running the
  project is its AI; with `DEBUG=0`, the Claude API.
- **Claude Code session**: the worker writes each related job to
  `ai_tasks/pending/<job>.json`; an assistant session does the same steps with
  `python3 ai.py gather|bundle|submit` and posts the result back.
- **Rules only**: the original keyword search and word-matching score; free.

Install the SDK with `pip install -r requirements.txt`, or where pip is
missing: `uv pip install --python python3 --target .deps -r requirements.txt`
(the app picks up `.deps/` automatically).

## State

Everything — registration, enrolled id, members, received jobs, results, log —
is kept in `state.json` (git-ignored: it holds the worker's bearer id), so a
restart resumes where it left off.
~~~~

## Phase 2: `finder.py` (rules-only search and score)

Module docstring: explain that similarity is a 0–1 score built from three parts, each the share of the job's terms that a repo also covers. The parts are tech stack (0.4: products, platforms, languages, frameworks), requirements (0.4: the job's most important content words) and architecture (0.2: shapes of solution such as API, migration, sync, webhook). A part the job says nothing about is left out, and the others share its weight. A repo is read through its name, description, topics, language and README. Standard library only; it uses `GITHUB_TOKEN`, or the token of a logged-in `gh` (30 searches a minute instead of 10).

Imports: `base64, json, os, re, subprocess, time, urllib.error, urllib.parse, urllib.request`, `Counter`.

**Verbatim: constants and lexicons**
~~~~python
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
bring cancel main best good great top ready quick asap long term lot lots kind sure account accounts
service services servic create creating creat engaging engag link links product products brand branded
http https www com upwork jobs job details apply hire hiring budget hourly fixed price""".split())
~~~~

**Verbatim: text helpers**
~~~~python
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


URLISH = re.compile(r"https?://\S+|www\.\S+|[\w.+-]+@[\w-]+\.[\w.-]+")
~~~~

`job_profile(title, description)` returns `{"tech": {...}, "arch": {...}, "keywords": {...}}`:
- Remove `\((re-?run|repost(ed)?|updated?)\)` (case-insensitive) from the title and strip it. Replace `URLISH` matches in the title and description (`description or ""`) with a space.
- `normed = norm(f"{title}\n{description}")`, `title_normed = norm(title)`.
- For TECH and ARCH, take every term found in `normed` and give it weight **3** if it is also found in `title_normed`, else **1**.
- Keywords: `lexical` = the set of `stem(w)` for every word of every phrase in TECH and ARCH. A word `w` with stem `s` counts when `s not in STOP and w not in STOP and len(s) > 2 and s.isalpha() and s not in lexical`. Add **+1** for each such word in the description and **+3** for each in the title. Keep `dict(counts.most_common(12))`.
- Return tech and arch sorted by weight, descending (dicts).

`score_repo(profile, repo, readme)` returns `(score, parts)`:
- Text is the repo name with `-` and `_` turned into spaces, plus description, plus topics (with `-` turned into spaces), plus language, plus `readme[:30000]`, joined by spaces, then `norm()`.
- Weights: `tech 0.4, requirements 0.4, architecture 0.2`.
- `coverage(terms, hit)`: core = terms with weight ≥ 3 and rest = the others; `share(ts)` = the fraction hit. With both core and rest: `0.85*share(core) + 0.15*share(rest)`; otherwise `share(core or rest)`.
- tech uses `found_terms({t: TECH[t]}, normed)`, architecture likewise with ARCH, and requirements hits when `f" {k} " in normed`. A part is computed only if the profile has terms for it.
- With no parts, return `(0.0, {})`. Otherwise `score = Σ weight·part / Σ used weights`. If tech is present, `score *= 0.5 + 0.5*tech`. If the README is blank, `score *= 0.85`. Return `round(score, 3)` and the parts rounded to 2 places.

GitHub:
- `token()` is cached in a module global: `GITHUB_TOKEN`, else `GH_TOKEN`, else the stdout of `gh auth token` (timeout 10, catching `OSError`/`SubprocessError`), else `""`.
- `gh(path, params=None, accept="application/vnd.github+json")` calls `API + path (+ "?" + urlencode(params))` with headers `Accept`, `User-Agent: repo-finder`, `X-GitHub-Api-Version: 2022-11-28`, plus `Authorization: Bearer <token>` when there is one. It makes 3 attempts with a timeout of 20. It returns the decoded text when `"raw" in accept`, otherwise the parsed JSON. On HTTP 403/429 with attempt < 2, it sleeps `min(max(X-RateLimit-Reset − now, 5), 65)` and retries. On 500/502/503/504 with attempt < 2, it sleeps `3*(attempt+1)` and retries. On 404 it returns `None`. Otherwise it raises `RuntimeError(f"GitHub {code} on {path}: {body[:200]}")`. After the loop it returns `None`.
- `readme_of(full_name)` calls `gh(f"/repos/{full_name}/readme", accept="application/vnd.github.raw+json") or ""` and returns `""` on `RuntimeError`.

`queries_for(profile)`, most specific first, at most 12:
- `tech` = profile tech terms minus `javascript, typescript, python, php, java`, falling back to all tech terms if that leaves none. `alias[t] = TECH[t][1] if t == "highlevel" else TECH[t][0]`. `kw` = the first 4 keywords. `arch` = the first 3 arch terms other than `"api"`.
- For each pair `(a, b)` of `tech[:3]` in order (`b` after `a`): add `"a b"` (aliases), and if there is any arch term, also `"a b arch[0]"`.
- If `"highlevel"` is in `tech[:3]`: add `"ghl " + (arch[0] or "api")`.
- For each `t` in `tech[:3]` and each `c` in `arch[:2] + kw[:1]`: add `"t c"`.
- Add the aliases of `tech[:2]`. If `len(kw) >= 2`, add `"kw0 kw1"`.
- Dedupe, keeping order, and return `[:12]`.

`month_end("YYYY-MM")` returns the last day of that month as `YYYY-MM-DD`. `in_period(repo, (start, end))` is true when `created_at[:7]` is non-empty and ≥ start, and either end is empty or `pushed_at[:7]` ≤ end.

**Verbatim: project filters**
~~~~python
NOT_A_PROJECT = re.compile(r"portfolio|resume|\bcv\b|personal[-_ ]?(site|page|website)|about[-_ ]?me|"
                           r"\.github\.io$|^dotfiles$|awesome[-_]", re.I)


SPAM = re.compile(r"\breview\b|ranked|\btop \d+|free trial|save up|discount|coupon|promo|affiliate|"
                  r"best .{0,30}\b20\d\d\b|\bvs\.? |alternatives?\b.*\b20\d\d|unlimited .{0,20}(tools|funnels)|"
                  r"all-in-one platform", re.I)
CODE_LANGS_EXCLUDED = {None, "HTML", "CSS", "Markdown", "TeX", "Roff"}
~~~~
`is_project(repo)`: split `full_name` into owner and name, with `text = f"{name} {description or ''}"`. It returns true when the name is not equal to the owner (case-insensitive), `NOT_A_PROJECT` doesn't match the name, `SPAM` doesn't match the text, the language is not in `CODE_LANGS_EXCLUDED` and `size > 20`.

`find_similar(title, description, threshold, exclude=(), want=2, period=None, analyze=None, log=lambda *_: None)`:
- Build the profile and log `Job terms — tech: …; architecture: …; keywords: …` (use `—` when a part is empty).
- Normalise `exclude` to lowercased URLs without a trailing slash. Window: `f" created:>={start}-01"` plus `f" pushed:<={month_end(end)}"` when there is an end.
- For each query: for each scope in `("", " in:name,description,topics,readme")`, search `/search/repositories` with `q=f"{q}{scope} fork:false{window}"` and `per_page=20`, collecting `items[:20]`. Walk the combined list with `rank %= 20`. Skip repos that are excluded, archived, fail `is_project`, or fall outside the period. Otherwise store the repo and add `20 - rank` to its hits. Stop the query loop once 90 or more repos are collected.
- The shortlist is the 36 most common hits. Log `Searched N queries, M candidates, reading K READMEs`. Read each README and score it, giving entries `{name, url, score, parts, stars, language, created[:10], last_push[:10], description[:200]}`.
- Sort by `(score, stars)` descending. Picks are those with `score >= threshold`, up to `want`. If `analyze` is given, set `p["analysis"] = analyze(raw_repo, readme)` for each pick and log `Analysing …`.
- Return `{"profile", "repos": picks, "best_score": top score or 0.0, "considered": len(scored)}`.
- `__main__`: `python3 finder.py "title" "description" 0.5` prints the result as indented JSON.

Check: `python3 -c "import finder; print(finder.job_profile('HubSpot to Salesforce migration','Move contacts and deals via API'))"` runs and finds hubspot/salesforce (weight 3) plus migration/crm.

## Phase 3: `analyzer.py` (rules-only repo analysis)

Docstring: it extracts six things, each a short list of plain statements: requirements, workflow, tech_stack, architecture, know_how and smart_approach. Nothing about people is kept. Sources are the README (split by heading), topics, languages, the file tree, dependency manifests, GitHub Actions workflows and a few main source files. Imports: `json, re, urllib.parse`, and `from finder import gh`.

**Verbatim: constants and patterns**
~~~~python
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
~~~~

**Verbatim: cleaning and statements**
~~~~python
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
~~~~

`readme_sections(readme)`: split `clean(readme)` into lines. A heading matches `^\s{0,3}#{1,6}\s+(.*)`, and its text is stripped of ` #`. The first section is called `"overview"`. For each `(heading, body)`: `h = re.sub(r"[^\w\s']", " ", heading.lower()).strip()`. The category is the first `SECTIONS` pattern that `re.search`es `h`, or `"requirements"` when `h == "overview"`, or else None. Keep `(category, statements(body))` when the category is set and is not `"exclude"`.

`tree_of(full_name, branch)` gets `/repos/{full}/git/trees/{branch}` with `recursive=1` and returns the paths of blobs, up to 5000. `raw_file(full_name, path)` gets `/repos/{full}/contents/{urllib.parse.quote(path)}` with the raw accept header, returning `""` on RuntimeError or None.

`deps_from(name, text)` returns `(deps[:25] without empties, notes)`, where notes are `(category, text)`. It catches `ValueError`/`AttributeError`.
- `package.json`: deps are the keys of dependencies plus devDependencies. For the first 8 scripts, add the note `("workflow", f"npm run {k}: {v}"[:MAX_LEN])`.
- `composer.json`: the `require` keys except `php`.
- `appsscript.json`: for each oauthScope, `("architecture", "Needs scope " + s.rsplit("/",1)[-1])`. Each `dependencies.enabledAdvancedServices` entry adds the dep `f"Advanced service: {serviceId}"`. A `webapp` adds `("architecture","Deployed as an Apps Script web app")`. A `runtimeVersion` adds `("tech_stack", f"Apps Script runtime {v}")`.
- `requirements.txt`: for each non-empty line not starting with `#` or `-`, take `re.split(r"[<>=~!\[ ;]", line.strip())[0]`.
- `pyproject.toml`: the names quoted inside `dependencies\s*=\s*\[(.*?)\]` (DOTALL), using `[\"']([A-Za-z0-9_.\-]+)`.
- `go.mod`: `^\s*([\w.\-/]+)\s+v[\d.]+` (multiline). `Gemfile`: `^\s*gem\s+['\"]([^'\"]+)`. `Cargo.toml`: the keys `^\s*([\w\-]+)\s*=` inside the `[dependencies]` block.
- `docker-compose*`: the services `^\s{2}([\w\-]+):\s*$`, giving the note `("architecture", "Services: " + first 10)`.
- `.clasp.json`: the note ``("workflow", "Edit locally, then `clasp push` to deploy the script")``.

**Verbatim: main sources**
~~~~python
def main_sources(paths, language):
    """A handful of files most likely to hold the real logic."""
    exts = {"JavaScript": (".js", ".mjs", ".gs"), "TypeScript": (".ts",), "Python": (".py",), "PHP": (".php",),
            "Go": (".go",), "Ruby": (".rb",), "Java": (".java",), "C#": (".cs",),
            "Blade": (".php",), "Vue": (".vue", ".js", ".ts")}.get(language, (".js", ".ts", ".py", ".gs", ".php"))
    skip = re.compile(r"(^|/)(node_modules|vendor|dist|build|test|tests|__tests__|\.github|docs?|examples?)/|\.min\.|\.d\.ts$|config")
    cands = [p for p in paths if p.endswith(exts) and not skip.search(p)]
    pri = lambda p: (p.count("/"), not re.search(r"(main|index|app|server|code|core|sync|migrat|service)", p, re.I), len(p))
    return sorted(cands, key=pri)[:5]
~~~~

`add(bucket, items)`: whitespace-collapse each item, then append it if it is non-empty, not already present (case-insensitive) and the bucket has fewer than `MAX_ITEMS` items.

`analyze(repo, readme)`, where `repo` is a GitHub search item:
1. `out` gets the six keys with empty lists. The cleaned description goes to requirements. Each README section's statements go into their category.
2. Languages come from `/repos/{full}/languages`: the top 5 as `"{Lang} ({pct}%)"` go to tech_stack, then `"Topics: " + first 10 topics` if there are any.
3. Tree paths come from `default_branch or "main"`. The top-level entries (the first path segment, plus `/` if it's a folder, sorted, excluding names matching `(licen[cs]e|copying|authors|contributors|code_of_conduct|funding)`) are **inserted at index 0** of architecture as `"Layout: " + first 14 joined by ", "`, with `" …"` appended if there are more. Then each `MARKERS` pattern that matches any path adds its text.
4. For each manifest, find the path that equals the name or ends with `/name` at depth 1. Deps add `f"{name}: " + first 15 + (" …" if more)` to tech_stack, and notes go into their categories.
5. For the first 3 `.github/workflows/` files, add `"CI: {name: value or file name}" + " (on {on: value})"` to workflow.
6. `code` = the main sources joined, each `[:60000]`. Each `APPROACHES` pattern that matches `readme + code` (case-insensitive) adds its text to smart_approach. If there is code, the function names `(?:function|def|async function)\s+([A-Za-z_]\w{3,})` (deduped, in order) add `"Main functions: " + ", ".join(...)[:MAX_LEN]` to architecture.
7. Return only the non-empty keys.

`as_text(analysis)`: for each key, a title (`Project requirements, Workflow, Tech stack, Architecture, Know-how, Smart approach`) + `":\n"` + `"- item"` lines. Sections are joined by blank lines.

## Phase 4: `ai.py` (AI engine, hand-off CLI, Missed mode)

The docstring must contain this CLI usage (it is printed by `sys.exit(__doc__)` on bad usage):
```
CLI (for the hand-off):
  python3 ai.py tasks                         pending jobs
  python3 ai.py gather <job> "query" ...      candidates for those queries -> digest file
  python3 ai.py bundle <owner/repo> ...       everything needed to analyse a repo -> file
  python3 ai.py submit <job> result.json      hand the result to the running worker
  python3 ai.py compose-submit <job> res.json hand a missed-mode result to the running worker
```
Above that, describe the two engines: the `claude` steps 1 plan, 2 gather (code), 3 judge, 4 analyse and 5 report (code), and `session`, the hand-off through `ai_tasks/pending/<job>.json`. Below it, say that Missed mode (rules/missed.md) runs on the same engines.

Start with `ROOT = Path(__file__).resolve().parent`. If `ROOT/.deps` is a directory, `sys.path.insert(0, str(ROOT/".deps"))`. Then import analyzer and finder.

**Verbatim: constants**
~~~~python
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
~~~~

`class AIError(RuntimeError)`.

**Verbatim: result schemas**
~~~~python
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
~~~~

`scrub(text)` = `" ".join(analyzer.clean(str(text or "")).split())`.

`validate(result, threshold)` normalises a GitHub-search result from either engine and raises `AIError`:
- The result must be a dict, and `repos` (default `[]`) must be a list. Only the first 2 are used.
- Each URL must match `^https://github\.com/([\w.-]+/[\w.-]+)/?$`, otherwise `not a GitHub repo url: {url!r}`. `similarity` (default −1) must be 0–1 (`similarity of {url} must be 0-1`) and ≥ threshold (`{url} scores {sim:.2f}, below the member's {threshold:.2f}`).
- Each clean repo is `{name: group(1), url, score: round(sim,3), reason: scrub, created[:10], last_push[:10], language, description: scrub[:300], analysis: {k: [scrub(x) for non-empty scrubbed x] for k in SECTIONS if present}, fit: scrub(r.fit or analysis.fit)}`.
- Return `{summary: scrub, queries: str list [:20], considered: int, best_score: float(given or first repo score or 0), repos, report: scrub, engine: given or "claude-code-session", usage}`.

`report_text(job, res, threshold, period)`: the lines are `Job {id}: {title}` and a blank line. If there is a summary: `What the client needs:`, the summary, and a blank line. Then `Searched GitHub for projects created and last updated within {from} – {to or 'now'}; {considered} candidates judged, threshold {t:.2f}.` With no repos: `No project reached the threshold (best {best:.2f}).` For each repo: a blank line, `{i}. {url} — similarity {score:.2f}`, `   Why: {reason}`, optionally `   Fit for this job: {fit}`, then `analyzer.as_text(analysis)`. Join with `\n` and strip.

`gather(queries, period, exclude=(), limit=CANDIDATES_MAX)`: the same window and two scopes as `find_similar`, for `queries[:12]`, with `per_page=20` and filters for exclude, archived, `is_project` and `in_period`. Hits get `20 - rank`. Return the `limit` most common as `{repo, url, description, topics, language, created[:10], last_push[:10], readme: analyzer.clean(readme_of)[:README_DIGEST], "_raw": r}`.

**Verbatim: people filters**
~~~~python
PEOPLE_SECTION = re.compile(r"^#{1,6}\s*(" + analyzer.SECTIONS[0][1] + r")", re.I)
PEOPLE_LINE = re.compile(r"(?im)^.*(\bcopyright\b|©|\(c\) \d{4}|@author\b|\bauthors?\s*:|\bmaintainers?\s*:|"
                         r"\bcreated by\b|\bwritten by\b|\bderived from\b.*\bby\b|\boriginal (source|work) by\b).*$")
~~~~
- `without_people(readme)` drops every README section whose heading (`^\s{0,3}#{1,6}\s`, lowercased, punctuation except `'` turned into spaces) matches `analyzer.SECTIONS[0][1]`, until the next heading.
- `code_safe(text)`: run `PEOPLE_LINE.sub("")`, `analyzer.EMAIL.sub(" ")` and `analyzer.HANDLE.sub(" ")`, then replace `(git@github\.com:|https?://(www\.)?github\.com/)[\w.-]+/[\w.-]+[^\s)\]'\"`]*` with `<repo url>`.
- `bundle(full_name, raw=None)`: `r = raw or gh(/repos/{full}) or {}`, and paths = `tree_of(default_branch or "main")`. Leave out files matching `licen[cs]e|authors|contributors|funding|code_of_conduct|copying` (case-insensitive). The parts are: `# {full}`, `Description: …`, `Language: …; topics: …`, `Created {c}, last push {p}`, blank, `## Files` plus the first 300 paths, blank, `## README` plus `PEOPLE_LINE.sub("", analyzer.clean(without_people(readme)))[:40000]`. Then each manifest found (same rule as the analyzer) as `## path` + `code_safe(raw)[:6000]`, the first 3 workflows `[:3000]`, and the main sources that don't match `(showdown|jquery|lodash|moment|bootstrap|\.min\.)` `[:12000]`. Join with `\n`.

**Claude API engine**
- `_client(api_key)` imports `anthropic` lazily (on ImportError: `the anthropic package is not installed: pip install -r requirements.txt`) and returns `anthropic.Anthropic(api_key=api_key, max_retries=3, timeout=600)`.
- `_ask(client, usage, system, prompt, schema, effort, max_tokens=16000)` calls `client.beta.messages.create(model=MODEL, max_tokens=max_tokens, betas=["server-side-fallback-2026-07-01"], fallbacks="default", system=system, messages=[{"role":"user","content":prompt}], output_config={"effort": effort, "format": {"type":"json_schema","schema": schema}})`. Errors map as follows: AuthenticationError gives `the Anthropic API key was rejected`, RateLimitError gives `Anthropic rate limit reached; try again shortly`, APIStatusError gives `Claude API error {status}: {message}`, and APIConnectionError gives `could not reach the Claude API`. Add the input and output tokens to `usage`. `stop_reason == "refusal"` gives `Claude declined this request`, and `"max_tokens"` gives `Claude's answer was cut off (max_tokens)`. Return `json.loads` of the first text block.
- `SYSTEM = ("You help a freelancer find open-source GitHub projects similar to a client's job, and explain them. Similar means the same kind of requirement, workflow, tech stack and architecture - not shared words. Work only from the material you are given; if something is not in it, leave it out. " + NO_PEOPLE)`.
- `run_claude(job, threshold, period, exclude, api_key, log)`, where `job_text = f"Title: {title}\n\nDescription:\n{description}"`:
  1. plan (PLAN_SCHEMA, effort `medium`): `f"{job_text}\n\nSummarise what the client needs, list the core requirements and technologies a similar project must share, and write GitHub repository search queries that would find such projects (product names, platform names, the kind of tool)."` Log `Claude planned N queries: q1; q2…`.
  2. `gather(plan.queries, period, exclude)`, then log `Gathered N candidates within {from} – {to or 'now'}`. With none, finish with `considered 0, repos []`.
  3. judge (JUDGE_SCHEMA, `medium`). The digest per candidate is `=== REPO {repo} ===\nDescription: …\nLanguage: …; topics: …\nREADME (start):\n{readme}`, joined by blank lines. The prompt is `f"{job_text}\n\nCore of the job: {'; '.join(core)}\n\nScore each repository below for similarity to this job, 0 to 1 (1 = it solves the same problem the same way; 0.7 = same core stack and most requirements; 0.4 = same platform, different problem). Mark SEO pages, portfolios, lists and profile READMEs as not real projects.\n\n{digest}"`. Keep scores for known repos where `real_project` is true and sort them by similarity, descending. The picks are the first 2 at or above the threshold. Log `Claude judged N; best X; K at or above T`.
  4. analyse each pick (ANALYSIS_SCHEMA, effort `high`): `f"{job_text}\n\nAnalyse this repository for the freelancer: its requirements, workflow, tech stack, architecture, know-how and smart approaches, then say how it fits the job. Be specific and concrete.\n\n{bundle(repo, raw)[:120000]}"`. Log `Claude analysing {repo}`.
  5. `_finish`: `validate({...raw, engine: f"claude-api ({MODEL})"})`, then `usage = {input, output, cost_usd: round(in/1e6*PRICE_IN + out/1e6*PRICE_OUT, 4)}` and `report = report_text(...)`.

**Session hand-off**
- `create_task(job, match, exclude)` writes `PENDING/{job_id}.json` (indent 2, creating the folders) with `job_id, title, description, person, member, channel, threshold, period, exclude (sorted), created_at (UTC Z), rules: "rules/github-search.md"`, plus `instructions` = `"Follow rules/github-search.md. Do what the Claude engine does: summarise the job, search GitHub (python3 ai.py gather), judge every candidate 0-1 for similarity of requirements, workflow, tech stack and architecture, pick up to two at or above the threshold, analyse each in depth (python3 ai.py bundle) into the six sections plus fit, and submit (python3 ai.py submit). " + NO_PEOPLE` and `result_shape` (a sample of the validate input with `"str"`/`"0-1"`/`"YYYY-MM-DD"` placeholders and the six analysis keys). It returns the path.
- `close_task(job_id, kind="")` moves `PENDING/{job_id}{kind}.json` to `DONE/` if it exists.

**Missed mode: verbatim constants and schemas**
~~~~python
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
~~~~

- `_clean_analysis(jd)`: if `real_problem` is blank, raise `the analysis must name the real problem to solve`. Otherwise return each JD_FIELDS list scrubbed (empties dropped) plus the scrubbed `real_problem`.
- `_clean_project(p)` (non-dicts are treated as `{}`): the texts are scrubbed and the lists are scrubbed with empties dropped.
- `structure_problems(p)`: `missing {k}` for each empty field, plus `{n} highlights; it needs 4 or 6` unless there are 4 or 6.
- `coverage(terms, text)`: whitespace-normalised, lowercase substring share. It returns 1.0 when there are no terms.
- `project_text(p)` = all the texts plus all the list items, joined by spaces.
- `score_project(analysis, p, judged)`: the judged parts are clamped to 0–1 (missing counts as 0). The counted parts are `coverage(analysis[k], project_text)`. `total` = Σ SCORE_WEIGHTS·part. Structure problems set total = 0. `credible is False` caps it at CREDIBILITY_CAP. `feedback` = problems + the judged feedback, joined by `"; "` and stripped of `"; "`. If any counted terms are missing entirely, append ` Missing terms: a, b.` Return `{score: round(total,3), parts: rounded to 2, credible, feedback}`.
- `score_all(analysis, projects, judged_list)` matches judged entries by their 1-based `project` number.
- `validate_compose(result, threshold, strict=True)`: the result must be a dict, the analysis is cleaned, and there must be 1–2 projects (`there must be 1 or 2 projects`). Clean them and score them. With strict: a structure problem raises `project {i}: …`. Any score below the threshold raises `below the member's {t:.2f}, rewrite and score again: ` + ` | `-joined `project {i} scores {s:.2f} (parts {parts}): {feedback}`. Return `{analysis, projects, scores, below: any < threshold, rounds: int, engine: given or "claude-code-session", usage}`.
- `compose_text(job, res)` builds these lines: `Job {id}: {title}`, a blank line, `The real problem to solve:`, the real problem. Then for each non-empty JD field: `{Label}: a, b` with the labels Must-have skills, Tools and technologies, Responsibilities, Seniority signals and ATS keywords. For each project: a blank line, `Project {i}: {title}`, the context, a blank line, `Requirement: …`, `Approach: …`, `Tech stack: a, b`, `Know-how:` with `  - x` lines, `Roadblock: …`, `How it was solved: …`, `Result: …`, a blank line, then `• highlight` lines. Strip the result.
- `own_work_text(repos)`: with no repos, `"No repos were found for this job, so keep project details general (see the grounding rules)."`. Otherwise, per repo: `=== {last path segment of name or url} (similarity {score:.2f}) ===\n{description}\nWhy similar: {reason}\nFit: {fit}\n` + `as_text(analysis)`, joined by blank lines.
- `run_compose(job, repos, threshold, api_key, log)`: `system = "You help a freelancer reply to a client's job. Follow the rules you are given exactly. " + NO_PEOPLE`. Each step's system prompt is `system + "\n\n" + rule(file)`, and every call uses effort `high`.
  1. JD_SCHEMA with `analyzing-step-missed.md`: `f"{job_text}\n\nAnalyse this job description by the rules."`, then `_clean_analysis`. Log `Step 1 done. Real problem: {first 160 chars}`.
  2. `context = f"{job_text}\n\n## Step 1 analysis\n{json.dumps(jd, indent=2)}\n\n## The member's real work\n{own_work}"`. EXPERIENCE_SCHEMA with `experience-missed.md`: `f"{context}\n\nCompose the experience by the rules. Each project must have exactly 4 or 6 highlights."`. Keep the first 2 projects, cleaned. Log `Step 2 done: N project(s)`.
  3. Loop, starting with `rounds = 0`. Score with SCORE_SCHEMA and `scoring-missed.md`: `f"## Step 1 analysis\n{jd json}\n\n## The member's real work\n{own_work}\n\n## Projects\n{projects json}\n\nScore every project's AI-judged parts ({', '.join(JUDGED)}) by the rules, say whether it is credible, and give feedback."` Then `score_all`. Log `Step 3 round {rounds}: scores a, b (bar T)`. Stop if nothing is weak or `rounds >= MAX_REWRITES`. Otherwise `rounds += 1` and rewrite the weak ones with EXPERIENCE_SCHEMA, using the system prompt `system + experience rule + scoring rule`. The notes per weak project are `### Project {i+1} — score {s:.2f}, parts {parts}\nFeedback: {fb}\n{project json}`. The prompt is `f"{context}\n\n## Projects below the bar of {t:.2f}\n{notes}\n\nRewrite these {n} project(s) by the rules, raising their weak parts without breaking the grounding rules. Return them in the same order. Each must have exactly 4 or 6 highlights."`. Replace the weak projects in order. Log `Step 3: rewrote N project(s) (round r of 3)`.
  4. `validate_compose({analysis, projects, scores: last judged, rounds, engine: f"claude-api ({MODEL})"}, threshold, strict=False)`, then add usage with cost_usd.
- `create_compose_task(job, match, repos)` writes `PENDING/{job_id}.compose.json` with `kind: "compose"`, `job_id, title, description, person, member, channel, threshold, own_work: own_work_text(repos), created_at` and `rules` (the 4 missed rule paths). Its `instructions` = `"Follow rules/missed.md. Step 1: analyse the JD by rules/analyzing-step-missed.md. Step 2: compose 1-2 projects by rules/experience-missed.md from the job, the step 1 output and own_work. Step 3: score each project's judged parts by rules/scoring-missed.md and rewrite any below the threshold (the worker counts the keyword parts and applies the weights; a submit below the threshold is rejected with each project's score and feedback, so rewrite and submit again, up to 3 rewrites). Submit with python3 ai.py compose-submit <job> result.json. " + NO_PEOPLE`. Its `result_shape` is a sample of the analysis, projects, scores and rounds with placeholder strings, highlights given as `"str (4 or 6 of them)"`.

**CLI** `main(argv)`: if the command is not one of `tasks, gather, bundle, submit, compose-submit`, call `sys.exit(__doc__)`. Ensure `ai_tasks/work/` exists.
- `tasks`: for each `PENDING/*.json`, sorted: compose tasks print `{job_id}  [compose]  {title}  (person {person})`, and others print `{job_id}  {title}  (person P, threshold T, period F – {to or 'now'})`.
- `gather <job> q…`: load `PENDING/{job}.json` (otherwise exit `no pending task for job X`), run gather, and write `work/{job}-candidates.md`, with blocks of `=== REPO r ===\nurl: u | created c | last push p | lang | topics: …\nDescription: …\nREADME (start):\n…`. Print `N candidates -> path`.
- `bundle owner/repo…` writes `work/bundle-{owner__repo}.md` and prints `name -> path`.
- `submit <job> file` POSTs `{job_id, result}` to `http://127.0.0.1:{PORT or 5000}/api/ai/result` (timeout 60) and prints `accepted: {status}; reply: {reply.status}`. On an HTTPError, exit `rejected: {body}`.
- `compose-submit <job> file` does the same against `/api/ai/compose`, reading `compositions[job]`.

## Phase 5: `app.py` (worker, HTTP server, reloader)

Docstring: describe the lifecycle `unregistered -> waiting` (POST /enrol, get a ticket), `waiting -> no_channel` (approved, enrolled id), `no_channel -> active` (put in a channel) and `waiting -> declined`. The relay cannot call us, so everything is a long poll. Imports: `json, queue, re, uuid, os, signal, socket, subprocess, sys, threading, time, urllib.error, urllib.request`, `datetime, timezone`, `BaseHTTPRequestHandler, ThreadingHTTPServer`, `Path`, and `ai, analyzer, finder`.

**Verbatim: config and default state**
~~~~python
APP_NAME = "repo-finder"
HOST = os.environ.get("HOST", "127.0.0.1")
PORT = int(os.environ.get("PORT", "5000"))
DEBUG = os.environ.get("DEBUG", "1") not in ("0", "false", "False", "")
DEFAULT_SERVER = "https://relay-production-7643.up.railway.app/upwork"

ROOT = Path(__file__).resolve().parent
STATIC = ROOT / "static"
STATE_FILE = ROOT / "state.json"

LONG_POLL = 25          # seconds the relay may hold a poll open
MIN_POLL_GAP = 3        # never ask more often than this, even if answered at once
RETRY_AFTER_ERROR = 5
LOG_MAX = 300
MESSAGES_MAX = 200
~~~~
Then:
- `now()` returns the UTC ISO time with seconds. `debug(*args)` prints `[HH:MM:SS] [debug] …` only in DEBUG mode.
~~~~python
DEFAULT_STATE = {
    "server_url": DEFAULT_SERVER,
    "name": APP_NAME,
    "label": f"{APP_NAME} on {socket.gethostname()}",
    "status": "unregistered",
    "ticket": None,
    "worker_id": None,
    "channels": [],
    "requested_at": None,
    "enrolled_at": None,
    "channel_at": None,
    "last_contact": None,
    "last_error": None,
    "last_response": None,
    "stats": {"pings": 0, "polls": 0, "errors": 0, "messages": 0},
    "messages": [],
    "members": [],
    "matches": {},
    "compositions": {},     # missed mode: per job, the JD analysis and the missed experience
    # engine: "auto" (debug mode -> this Claude Code session, otherwise the Claude API),
    # "session" (hand-off to a Claude Code session), "claude" (Claude API), "rules" (no AI)
    "settings": {"engine": "auto", "anthropic_api_key": ""},
    "log": [],
}
~~~~
- `lock = threading.RLock()`, `wake = threading.Event()`.
- `load_state()`: deep-copy the default. If the file exists, merge only the known keys and merge the stats with the default stats. Legacy migration: `settings.mode == "composing"` becomes `"missed"`, and composition statuses `composed` become `missed` and `composing` become `in_progress`. On an unreadable file, print `state.json unreadable, starting fresh: …` to stderr. `STATE = load_state()`.
- `save()` writes `state.tmp` (indent 2), then replaces `state.json`.
- `log(level, text)` appends `{at, level, text}` while holding the lock, keeps the last LOG_MAX, saves, and prints `[HH:MM:SS] [level] text`.
- `set_status(status, text=None, level="info")` sets and saves the status. Only if it changed, it logs `text or f"Status is now {status}"`.

**Relay client**
- `relay(method, path, body=None, token=None, timeout=15)` requests `server_url.rstrip("/") + path` with `Accept: application/json`, `User-Agent: repo-finder-worker`, `Content-Type` when there is a body, and `Authorization: Bearer` when there is a token. HTTPError is not raised: the error's code and body are returned instead. It parses JSON (falling back to text, and `{}` when empty) and debug-logs `->` and `<-` (body[:500]). Under the lock it sets `last_contact`, `last_response = {at, request: "METHOD path", status, body}` and `last_error = None`. It returns `(code, parsed)`. Only network errors raise.
- `field(data, *names)` returns the first non-empty value among the names when data is a dict.
- `register()`: body `{name, label}`, `stats.pings += 1`. Log `Registering as '{name}' with {server_url}`. `POST /enrol`. The ticket is `field(ticket, ref, id)`. If code ≥ 300 or there is no ticket, raise `RuntimeError(f"Registration refused: {message|detail or HTTP code}")`. Otherwise set `ticket`, `worker_id=None`, `channels=[]`, `requested_at=now`, `enrolled_at=None` and `channel_at=None`, then `set_status("waiting", "Registration sent. Waiting for the server to approve it.")` and `wake.set()`.
- `poll_enrolment()`: `GET /enrol/{ticket}?wait=25` with timeout LONG_POLL+15, then `polls += 1`. If code is 200, `worker_id|id` is present and the status is not pending/waiting: set the worker_id, clear the ticket, set enrolled_at, log ok `Approved. Enrolled id allocated: {id}`, and `set_status("no_channel", "Enrolled, but the server has not allocated a channel yet.", "warn")`. If code is 404 or the status is unknown/declined/rejected/refused/expired: clear the ticket and `set_status("declined", message or "Registration was declined or expired.", "error")`.
- `channels_from(data)`: `field(channels, memberships)`. A list is mapped through `c.get("name", c.get("channel", str(c)))` for dicts and `str(c)` otherwise. A dict gives its keys. Anything else gives None.
- `poll_messages()`: `GET /messages?wait=25` with the bearer worker_id, then `polls += 1`.
  - On 401: clear `worker_id` and `channels`, then `set_status("unregistered", f"The server no longer knows this id ({message or status}). Register again.", "error")` and return.
  - msgs = the data if it is a list, else `field(messages, items) or []`. Call `handle_notices(field(notices) or [])`. `chans = channels_from(data)`, falling back to the saved channels when that is None and some are saved. `no_channel_hint` = code is 403/409, or `"channel" in status` and the status is not ok/active, or chans is not None and is empty.
  - If there are chans, save them. If there are msgs or chans and the status is not active: set `channel_at` and `set_status("active", f"Channel allocated{': ' + ', '.join(chans) if chans else ''}. Working.", "ok")`. Otherwise, if no_channel_hint and the status is not no_channel: `set_status("no_channel", "The server has taken this worker out of every channel.", "warn")`.
  - If there are msgs, call `handle_messages(msgs)`.
- `handle_notices(notices)`: for each dict notice, log info `Notice: {message or kind}`. If it has a channel: kind joined/added appends it, and left/removed/kicked/parted removes it. Save. If there were notices, no channels remain and the status is active, set no_channel with the same message.
- `handle_messages(msgs)`: append each message as `{"received_at": now(), **m}` (non-dicts become `{"body": m}`), tracking the max integer `seq` per channel. Keep the last MESSAGES_MAX, `stats.messages += len`, save, and log `Received N message(s)`. Call `plan_jobs()`. Then `POST /ack {channel, seq}` per channel. A failure logs warn `Ack for {ch}#{seq} failed: HTTP {code}`.
- `worker_loop()` loops forever. When waiting with a ticket, `poll_enrolment`. When no_channel/active with a worker_id, `poll_messages`. Otherwise `wake.wait(); wake.clear(); continue`. On any exception: set `last_error={at,text}`, `errors += 1`, save, log error `Server unreachable: {exc}`, `wake.wait(RETRY_AFTER_ERROR)`, clear. After a successful poll: save, then wait out the rest of MIN_POLL_GAP.

**Members**
- `MONTH = ^\d{4}-(0[1-9]|1[0-2])$`. `class BadRequest(ValueError)`.
- `member_from(body)` validates with these exact messages: `Name is required`, `'From' must be a month (YYYY-MM)`, `'To' must be a month (YYYY-MM)`, `'To' can't be before 'From'`, `Similarity must be a number from 0 to 1`, `Similarity must be from 0 to 1`. It returns `{name[:120], from, to or None, similarity: round(x, 2)}`.
- `add_member` appends `{id: uuid4().hex[:12], **member, added_at}`, logs `Member added: {name} (similarity ≥ {s:.2f})` and calls `plan_jobs()`. `update_member` finds the member by id (`No such member`), updates it, logs `Member updated: …` and calls `plan_jobs()`.

**Verbatim: job pipeline constants**
~~~~python
jobs_queue = queue.Queue()
queued = set()
ACTIVE_MATCH = ("queued", "searching")
ENGINES = ("auto", "session", "claude", "rules")
MODES = {"github": ("GitHub search", "rules/github-search.md"),
         "missed": ("Missed", "rules/missed.md")}
HEARTBEAT = ROOT / "ai_tasks" / "session_heartbeat"
SESSION_FRESH = 30          # seconds: a session that touched the heartbeat this recently is listening
~~~~
- `engine_choice()` returns `settings.engine` if it is valid, else `"auto"`. `work_mode()` returns `settings.mode` if it is valid, else `"github"`. `engine()` resolves auto to `"session" if DEBUG else "claude"`.
- `session_seen()` returns the seconds since the heartbeat file's mtime (at least 0), or None.
- `api_key()` = `ANTHROPIC_API_KEY` env, else `settings.anthropic_api_key`, else `""`.
- `same_person(a, b)`: whitespace-normalise and lowercase both. Empty gives false. Equal, or a one-word name equal to the other's first word, gives true ("Zachary" equals "Zachary Wing").
- `jobs_by_id()` merges `STATE.messages` by `body.job`, skipping those with no job or title and those whose `sender == STATE.name`. Each entry is `{job_id, title, description:"", person:None, channel, ts, seqs:[]}`. The longest description wins, and its title comes with it. The first non-empty person wins, and the smallest `ts` wins.
- `member_for(job)` returns `(member, None)` or `(None, why)`, with why = `no person on this job` or `{person} is not a registered member`. If several members share the name and the job has a ts, prefer the one whose from–to period (with to defaulting to `9999-12`) covers the post month, computed in local time.

**GitHub search mode**
- `plan_jobs(force=None)`: in missed mode, `return plan_compose(force)`. Otherwise, holding the lock, for each job:
  - With no member: unless the current status is found, queued or searching, set `{job_id, person, channel, status:"unrelated", reason, updated_at}`.
  - Add `member, member_id` to the base. If the similarity is None: status `needs_threshold`.
  - If the current status is queued or searching: skip.
  - `period = [from, to]`. The match is `stale` when there is none, or its status is unrelated, needs_threshold or error, or its threshold, period or `engine_mode` (default `"rules"`) differs from the current ones, or it is found but some repo lacks `"analysis"`.
  - If it is awaiting_ai, not stale and not forced: skip.
  - If stale or forced: keep `reply` and `sent_repos` if set, set status queued with the threshold and period, and queue `("search", jid)`. Otherwise, if it is found and `reply.status != "sent"`, queue `("reply", jid)`.
  - Save, then put each item on `jobs_queue` unless it is already in `queued`.
- `search_job(jid)`: mark it searching. `exclude = set()`, since repos other workers picked may be picked again. `where = f"similarity ≥ {t:.2f}, created and last pushed within {from} – {to or 'now'}"`.
  - session: `ai.create_task`, then status `awaiting_ai` with `engine_mode` and `task` (the relative path). Log `Job {jid} handed to the Claude Code session for AI search and analysis ({where})`.
  - Otherwise log `Searching GitHub for repos like job {jid} — “{title}” ({where}; engine {mode})`. claude: with no key, raise `ai.AIError("no Anthropic API key: add one in Profile → AI engine")`, else `ai.run_claude(..., log=lambda t: log("info", f"[{jid}] {t}"))`. rules: `finder.find_similar(..., period=tuple(period), analyze=analyzer.analyze, log=debug)` with `engine="rules"`. On an exception, status error, logging `Search for job {jid} failed: {exc}`. Then `apply_result`.
- `apply_result(jid, res, mode)`: `prev_reply, sent = reply or {}, sent_repos`. If there is no `sent`, look for the channel's echo of our own reply (messages from our name whose `body.job_id == jid` and that have repos). If there is one, `sent` comes from its URLs and prev_reply = `{status:"sent", seq, at: received_at}`. `same` = prev_reply was sent, there are repos, and the sorted URLs equal the sent URLs. Update the match: status `found` or `none`, plus repos, best_score, considered, profile, summary, report, queries, engine, usage, `sent_repos` (only if same), engine_mode, searched_at, `reply` (prev if same, else None), error None and task None. Add the usage cost to `stats.ai_cost_usd` (rounded to 4). `ai.close_task(jid)`. With repos, log ok `Job {jid}: N similar repo(s) [engine] — name (0.83), …`. If same, log `…same repos as the reply already sent (seq S); not posting again`, else `send_reply`. With no repos, log warn `Job {jid}: no repo within the period reached {t:.2f} (best {b:.2f} of {n}) [engine]`.
- `submit_ai_result(body)`: the job and match must exist with status awaiting_ai, error, none or found (`job {jid} is not waiting for an AI result`). `ai.validate`; failures become `BadRequest(f"result rejected: {exc}")`. Set the report with `ai.report_text`, then `apply_result(..., "session")`.
- `reply_body(jid, match)` = `{event:"similar_repos", job_id, person, worker: name, threshold, period:{from, to or "present"}, engine, summary, links:[{url, label: f"{repo short name} · similarity {s:.2f}"}], repos:[{url, similarity: score, created, last_push, reason?, fit?, **analysis, description: analyzer.as_text(analysis) or description}], report?, at}`. Never include owners or stars.
- `send_reply(jid)`: only when the match is found. If the worker isn't active with an id: `reply = {status:"pending", error:"worker is not active on a channel yet", at}`. Otherwise `POST /publish {channel, body}`. On success, set `sent_repos`. `reply = {status: sent|failed, code, at, request, seq, error}`. Log ok `Replied to job {jid} on #{ch} with N repo link(s)`, or error `Reply to job {jid} on #{ch} refused: {error}`.

**Missed mode**: `ACTIVE_COMPOSE = ("queued", "in_progress")`.
- `plan_compose(force=None)` mirrors plan_jobs over `STATE.compositions`. Unrelated is written only when there is no record or it is already unrelated. needs_threshold is used when the similarity is None. Skip active records. Skip awaiting_ai records with the same engine_mode and threshold unless forced. If the status is missed or below_score with scores and the threshold moved, re-gate without recomposing: set the new threshold and status `missed` if `min(scores) >= bar`, else `below_score`. The record is stale when there is none; when it is unrelated, needs_threshold or error; when it is awaiting_ai with a different engine or threshold; or when it is missed or below_score without scores. Stale or forced queues `("compose", jid)` with status queued. Otherwise, if it is missed and the reply was not sent, queue `("compose_reply", jid)`.
- `own_repos(jid)` returns the GitHub-search match's repos if its status is found, else `[]`.
- `compose_job(jid)`: mark it in_progress. `grounding = f"{n} repo(s) of the member's work"` or `"no repos found for it yet"`. In session mode: `ai.create_compose_task`, status awaiting_ai, and log `Job {jid} handed to the Claude Code session for missed ({grounding})`. Otherwise log `Missed job {jid} — “{title}” ({grounding}; engine {mode})`. A non-claude engine raises `missed needs an AI engine: pick Claude API or Claude Code session`, and a missing key raises the same message as before. Otherwise `ai.run_compose`. An error sets status error and logs `Missed job {jid} failed: {exc}`. Then `apply_compose`.
- `apply_compose(jid, res, mode)`: status `below_score` if `res.below`, else `missed`, plus analysis, projects, scores, rounds, `text = ai.compose_text`, engine, usage, `grounded_on` (own repo URLs), engine_mode, missed_at, reply None, error None and task None. Add the cost. `ai.close_task(jid, ai.COMPOSE)`. If below, log warn `Job {jid}: still below {t:.2f} after {r} rewrite(s) — {title (0.71); …}. Not posting; use Compose again.`. Otherwise log ok `Job {jid}: missed N project(s) at or above {t:.2f} after {r} rewrite(s) [engine] — …` and call `send_compose_reply`.
- `submit_compose_result(body)`: the status must be awaiting_ai, error, missed or below_score (`job {jid} is not waiting for a missed result`). `ai.validate_compose` (strict) catches AIError, ValueError, TypeError and AttributeError, raising `result rejected: …`. Then `apply_compose(..., "session")`.
- `send_compose_reply(jid)`: only when the status is missed, with the same pending rule as before. Body `{event:"missed_experience", job_id, person, worker, engine, threshold, analysis, projects, scores, rewrites: rounds, report: text, at}`. Log ok `Replied to job {jid} on #{ch} with the missed experience`, or error `Missed reply to job {jid} on #{ch} refused: …`.
- `finder_loop()` takes items from the queue, discards each from `queued`, and dispatches `search|reply|compose|compose_reply`. An exception logs `{kind} for job {jid} failed: {exc}`.

**Local HTTP API**
- `public_state()` returns a deep copy of STATE plus `app`, `debug` and `host = f"http://{HOST}:{PORT}"`. `settings` is replaced by `{engine: engine(), choice: engine_choice(), mode: work_mode(), modes: {k: {name, rules}}, session: {connected: seen ≤ 30, seen_ago}, has_key, key_hint: "…"+last 4, key_from_env, model: ai.MODEL}`, so the raw key never leaves the server. Outside debug mode, drop `last_response`.
- `Handler(BaseHTTPRequestHandler)`, `server_version = "repo-finder/0.1"`. Requests are logged through debug(), except `/api/state`. JSON responses get `Cache-Control: no-store` and Content-Length.
- GET `/api/state` returns public_state(). Other GETs serve `static/` (`/` serves index.html, and the query string is stripped). The resolved path must be inside STATIC, otherwise 404 `{"error":"not found"}`. Types are `.html .js .css .svg` with `; charset=utf-8`, and Cache-Control is `no-store` in debug mode, else `max-age=60`.
- POST routes. Each returns `public_state()` on success, `400 {error}` on BadRequest, and `502 {error}` (also logged) on any other exception. Unknown paths return 404.
  - `/api/register`: 409 `already registered or waiting` if the status is waiting, no_channel or active; otherwise `register()`.
  - `/api/cancel`: clear the ticket, worker_id and channels, then `set_status("unregistered", "Registration forgotten locally.", "warn")`.
  - `/api/profile`: while registered, only `label` may change; otherwise `name`, `label` and `server_url` (non-empty stripped strings). Log `Profile updated`.
  - `/api/members`, `/api/members/update`, `/api/members/delete` (by id, then plan_jobs).
  - `/api/ai/result` → submit_ai_result, `/api/ai/compose` → submit_compose_result.
  - `/api/settings`: the optional fields are `engine` (if valid), `mode` (if valid), `anthropic_api_key` (non-empty) and `clear_key`. Log `Work mode: {mode name} · AI engine: {engine}` + ` (API key set)` when there is a key, then `plan_jobs()`.
  - `/api/jobs/search` (`No such job`) → `plan_jobs(force=id)`. `/api/jobs/compose` → `plan_compose(force=id)`.
  - `/api/clear-log` empties the log and the messages.
- `serve()`: matches still queued or searching, and compositions still queued or in_progress, become error with `interrupted by a restart`. Start the daemon threads `worker` and `finder`, call `plan_jobs()` and `wake.set()`, start a ThreadingHTTPServer, print ` * repo-finder worker on http://HOST:PORT  (debug=on|off)` and ` * relay: {url}  status: {status}`, and serve forever, ending quietly on KeyboardInterrupt.
- Debug reloader, like Flask's: `watched_mtimes()` covers `ROOT/*.py` and `STATIC/*`. `run_with_reloader()` prints ` * Debug mode: on — restarting on file changes` and turns SIGTERM into KeyboardInterrupt. In a loop, it spawns `sys.executable app.py` with `REPO_FINDER_CHILD=1` and checks every second. On a change it prints ` * Detected change, reloading`, terminates the child and restarts it. If the child exits on its own, it calls `sys.exit(returncode)`. On KeyboardInterrupt it terminates the child and returns.
- `__main__`: run the reloader if DEBUG is on and this is not the child, else `serve()`.

Check: `python3 -c "import app"` works. `python3 app.py` starts, `curl -s 127.0.0.1:5000/api/state` returns JSON with `status: "unregistered"`, and `python3 ai.py` prints the usage.

## Phase 6: Frontend

`static/index.html` (verbatim)
~~~~html
<!doctype html>
<html lang="en">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>repo-finder</title>
  <link rel="icon" type="image/svg+xml" href="/favicon.svg">
  <link rel="stylesheet" href="/style.css">
  <script src="/app.js" defer></script>
</head>
<body>
  <header class="topbar">
    <div class="brand">
      <img class="logo" src="/favicon.svg" alt="" aria-hidden="true">
      <span class="name">repo-finder</span>
      <span class="badge debug" id="debug-badge" hidden>debug</span>
    </div>
    <nav class="tabs" role="tablist">
      <button class="tab active" role="tab" data-tab="workspace">Workspace</button>
      <button class="tab" role="tab" data-tab="report">Report</button>
      <button class="tab" role="tab" data-tab="profile">Profile</button>
    </nav>
  </header>

  <main>
    <!-- Workspace -->
    <section class="panel" id="tab-workspace">
      <div class="card">
        <div class="card-head"><h3>Incoming jobs</h3><span class="muted small" id="msg-count"></span></div>
        <div id="messages" class="list"><p class="empty">No jobs yet. They appear here once the server sends them.</p></div>
      </div>
    </section>

    <!-- Report -->
    <section class="panel" id="tab-report" hidden>
      <div class="stats">
        <div class="stat"><div class="stat-v" id="s-pings">0</div><div class="stat-k">Registrations sent</div></div>
        <div class="stat"><div class="stat-v" id="s-polls">0</div><div class="stat-k">Server polls</div></div>
        <div class="stat"><div class="stat-v" id="s-messages">0</div><div class="stat-k">Messages received</div></div>
        <div class="stat"><div class="stat-v" id="s-errors">0</div><div class="stat-k">Errors</div></div>
      </div>
      <div class="card">
        <div class="card-head"><h3>Activity log</h3><button class="btn small ghost" id="clear-btn">Clear</button></div>
        <div id="log" class="list log"></div>
      </div>
      <div class="card" id="debug-card" hidden>
        <div class="card-head"><h3>Last server response</h3><span class="muted small">debug</span></div>
        <pre id="last-response">—</pre>
      </div>
    </section>

    <!-- Profile -->
    <section class="panel" id="tab-profile" hidden>
      <div class="card status-card collapsible" id="card-registration">
        <div class="status-head">
          <div>
            <div class="eyebrow">Server connection</div>
            <h2 id="status-title">Not registered</h2>
            <p class="muted" id="status-text">This worker has not asked the server to join yet.</p>
          </div>
          <div class="head-tools"><span class="pill" id="status-pill">unregistered</span></div>
        </div>

        <div class="alert warn" id="channel-warning" hidden>
          <strong>Channel is not allocated yet.</strong>
          The server has enrolled this worker but has not put it in a channel. It will start working as soon as one is allocated.
        </div>
        <div class="alert error" id="error-box" hidden></div>

        <ol class="steps" id="steps">
          <li data-step="request">Registration sent</li>
          <li data-step="approved">Approved &amp; ID allocated</li>
          <li data-step="channel">Channel allocated</li>
        </ol>

        <dl class="facts">
          <div><dt>Server</dt><dd id="f-server">—</dd></div>
          <div><dt>Enrolled ID</dt><dd><code id="f-id">—</code></dd></div>
          <div><dt>Channels</dt><dd id="f-channels">—</dd></div>
          <div><dt>Last contact</dt><dd id="f-contact">—</dd></div>
        </dl>

        <div class="actions">
          <button class="btn primary" id="register-btn">Register to server</button>
          <button class="btn ghost" id="forget-btn" hidden>Forget registration</button>
        </div>
      </div>

      <form class="card collapsible" id="profile-form">
        <div class="card-head">
          <div><h3>Worker profile</h3><div class="summary muted small" id="sum-profile"></div></div>
          <div class="head-tools"></div>
        </div>
        <label>Name <span class="muted small">— how the server lists this worker</span>
          <input name="name" required pattern="[A-Za-z0-9][A-Za-z0-9_.\-]{0,63}">
        </label>
        <label>Label <span class="muted small">— optional note shown to the approver</span>
          <input name="label">
        </label>
        <label>Server URL
          <input name="server_url" type="url" required>
        </label>
        <p class="muted small" id="profile-lock" hidden>Name and server can't change while registered. Forget the registration first.</p>
        <div class="actions"><button class="btn primary" type="submit">Save</button><span class="muted small" id="saved-note"></span></div>
      </form>
      <div class="card collapsible" id="card-identity">
        <div class="card-head">
          <div><h3>Identity</h3><div class="summary muted small" id="sum-identity"></div></div>
          <div class="head-tools"></div>
        </div>
        <dl class="facts">
          <div><dt>Status</dt><dd id="p-status">—</dd></div>
          <div><dt>Enrolled ID</dt><dd><code id="p-id">—</code></dd></div>
          <div><dt>Requested</dt><dd id="p-requested">—</dd></div>
          <div><dt>Enrolled</dt><dd id="p-enrolled">—</dd></div>
          <div><dt>Channel since</dt><dd id="p-channel">—</dd></div>
          <div><dt>Local address</dt><dd id="p-host">—</dd></div>
        </dl>
      </div>
      <div class="card collapsible" id="card-mode">
        <div class="card-head">
          <div><h3>Work mode</h3><div class="summary muted small" id="sum-mode"></div></div>
          <div class="head-tools"><span class="pill active" id="mode-pill">—</span></div>
        </div>
        <p class="muted small">What this worker does with each related job. Each mode follows its own rules file.</p>
        <div class="modes">
          <label class="engine"><input type="radio" name="mode" value="github">
            <span><strong>GitHub search</strong><br>
            <span class="muted small">Finds 1–2 similar repos made within the member's period, analyses them and replies with the links.</span><br>
            <code class="small">rules/github-search.md</code></span></label>
          <label class="engine"><input type="radio" name="mode" value="missed">
            <span><strong>Missed</strong><br>
            <span class="muted small">Analyses each related JD, composes 1–2 matching past projects and replies with them. GitHub search is paused in this mode.</span><br>
            <code class="small">rules/missed.md</code></span></label>
        </div>
      </div>
      <div class="card collapsible" id="card-ai">
        <div class="card-head">
          <div><h3>AI engine</h3><div class="summary muted small" id="sum-ai"></div></div>
          <div class="head-tools"><span class="pill" id="ai-pill">—</span></div>
        </div>
        <p class="muted small">Who searches GitHub, judges similarity, analyses the repos and writes the report for each related job.</p>
        <div class="session-status" id="session-status"></div>
        <div class="engines" id="engines">
          <label class="engine"><input type="radio" name="engine" value="auto">
            <span><strong>Automatic</strong> <span class="muted small">(recommended)</span><br>
            <span class="muted small">Debug mode: the Claude Code session running this project is its AI — the project hands it each job and it hands back the result. Normal mode (<code>DEBUG=0</code>): the Claude API.</span></span></label>
          <label class="engine"><input type="radio" name="engine" value="claude">
            <span><strong>Claude API</strong> <span class="muted small" id="ai-model"></span><br>
            <span class="muted small">Claude plans the search, scores every candidate with a reason, analyses the picks and writes the report. About $1 per job.</span></span></label>
          <label class="engine"><input type="radio" name="engine" value="session">
            <span><strong>Claude Code session</strong> <span class="muted small">(always, even outside debug mode)</span><br>
            <span class="muted small">Until an API key is added: each job is written to <code>ai_tasks/pending/</code> and the Claude Code session does the AI work, then submits it.</span></span></label>
          <label class="engine"><input type="radio" name="engine" value="rules">
            <span><strong>Rules only</strong><br>
            <span class="muted small">No AI: keyword search and word-matching scores. Free, less accurate.</span></span></label>
        </div>
        <form id="key-form" class="key-row">
          <input name="key" type="password" placeholder="Anthropic API key (sk-ant-…)" autocomplete="off">
          <button class="btn primary" type="submit">Save key</button>
          <button class="btn ghost" type="button" id="clear-key">Remove</button>
        </form>
        <p class="muted small" id="key-status"></p>
      </div>

      <div class="card collapsible" id="card-members">
        <div class="card-head">
          <div><h3>Registered members</h3><div class="summary muted small" id="sum-members"></div></div>
          <div class="head-tools">
            <button class="btn small primary add-btn" type="button" id="add-member-btn"><span aria-hidden="true">+</span> Add member</button>
          </div>
        </div>
        <div id="members-list" class="members"></div>
      </div>
    </section>
  </main>

  <dialog id="confirm-dialog">
    <form method="dialog">
      <h3>Register this worker?</h3>
      <p>This sends a registration request to</p>
      <p><code id="confirm-url"></code></p>
      <p class="muted">as <strong id="confirm-name"></strong>. Someone on the server has to approve it before an ID is allocated.</p>
      <div class="actions end">
        <button class="btn ghost" value="cancel">Cancel</button>
        <button class="btn primary" value="register" id="confirm-register">Register</button>
      </div>
    </form>
  </dialog>

  <dialog id="member-dialog">
    <form id="member-form" novalidate>
      <h3 id="member-dialog-title">Add member</h3>
      <label>Name
        <input name="name" required maxlength="120" autocomplete="off" placeholder="e.g. Zachary Wing">
      </label>
      <div class="month-row">
        <label>From
          <input name="from" type="month" required>
        </label>
        <label>To
          <input name="to" type="month">
        </label>
      </div>
      <label class="check"><input type="checkbox" name="present"> Still a member (no end date)</label>
      <label>Similarity <span class="muted small">— a repo must score at least this (0 to 1) to be sent for this person's jobs</span>
        <div class="sim-row">
          <input name="similarity_range" type="range" min="0" max="1" step="0.01" value="0.6" aria-label="Similarity slider">
          <input name="similarity" type="number" min="0" max="1" step="0.01" value="0.6" required>
        </div>
      </label>
      <p class="form-error" id="member-error" hidden></p>
      <div class="actions end">
        <button class="btn ghost" type="button" id="member-cancel">Cancel</button>
        <button class="btn primary" type="submit" id="member-submit">Add</button>
      </div>
    </form>
  </dialog>

  <dialog id="jd-dialog" class="jd-dialog">
    <form method="dialog">
      <div class="jd-head">
        <div>
          <div class="eyebrow" id="jd-meta"></div>
          <h3 id="jd-title"></h3>
          <div class="muted" id="jd-person"></div>
        </div>
        <button class="btn icon" value="close" aria-label="Close">&times;</button>
      </div>
      <div class="jd-body" id="jd-body"></div>
      <div class="actions end"><button class="btn primary" value="close">Close</button></div>
    </form>
  </dialog>

  <div id="toast" role="status" hidden></div>
</body>
</html>
~~~~

`static/style.css` (verbatim)
~~~~css
:root {
  --bg: #f5f6f8; --surface: #fff; --text: #1b1f24; --muted: #667085; --line: #e4e7ec;
  --accent: #3451d1; --accent-ink: #fff;
  --ok: #157f3c; --ok-bg: #e7f6ec; --warn: #9a6200; --warn-bg: #fff4dc;
  --err: #b42318; --err-bg: #fdecea; --wait: #3451d1; --wait-bg: #eaeefc;
  color-scheme: light;
}
@media (prefers-color-scheme: dark) {
  :root {
    --bg: #111317; --surface: #1a1d23; --text: #e6e8eb; --muted: #98a1ae; --line: #2a2f37;
    --accent: #6c84f0; --ok: #4ccb7a; --ok-bg: #12301f; --warn: #f0b44c; --warn-bg: #33270f;
    --err: #f57f73; --err-bg: #3a1714; --wait: #8fa2f5; --wait-bg: #1d2448;
    color-scheme: dark;
  }
}
* { box-sizing: border-box; }
body { margin: 0; background: var(--bg); color: var(--text); font: 14px/1.5 system-ui, -apple-system, "Segoe UI", sans-serif; }
code, pre { font-family: ui-monospace, "SF Mono", Menlo, Consolas, monospace; font-size: 12.5px; }

.topbar {
  position: sticky; top: 0; z-index: 5; display: flex; align-items: center; justify-content: space-between;
  gap: 16px; flex-wrap: wrap; padding: 10px 24px; background: var(--surface); border-bottom: 1px solid var(--line);
}
.brand { display: flex; align-items: center; gap: 10px; }
.logo { width: 24px; height: 24px; display: block; }
.name { font-weight: 700; font-size: 17px; letter-spacing: -.01em; }
.badge.debug { font-size: 11px; padding: 1px 7px; border-radius: 99px; background: var(--warn-bg); color: var(--warn); font-weight: 600; }
.tabs { display: flex; gap: 4px; }
.tab { border: 0; background: none; color: var(--muted); font: inherit; font-weight: 600; padding: 8px 14px; border-radius: 8px; cursor: pointer; }
.tab:hover { color: var(--text); background: var(--bg); }
.tab.active { color: var(--accent); background: var(--wait-bg); }

main { max-width: 960px; margin: 0 auto; padding: 24px; }
.panel { display: grid; gap: 16px; }
[hidden] { display: none !important; }
.card { background: var(--surface); border: 1px solid var(--line); border-radius: 12px; padding: 20px; }
.card h3 { margin: 0 0 12px; font-size: 15px; }
.card-head { display: flex; justify-content: space-between; align-items: center; margin-bottom: 8px; }
.card-head h3 { margin: 0; }
.muted { color: var(--muted); } .small { font-size: 12px; }
.eyebrow { font-size: 11px; text-transform: uppercase; letter-spacing: .06em; color: var(--muted); font-weight: 600; }

.status-head { display: flex; justify-content: space-between; gap: 16px; align-items: flex-start; }
.status-head h2 { margin: 2px 0 4px; font-size: 22px; }
.status-head p { margin: 0; }
.pill { white-space: nowrap; font-size: 12px; font-weight: 700; padding: 4px 10px; border-radius: 99px; background: var(--bg); color: var(--muted); }
.pill.waiting { background: var(--wait-bg); color: var(--wait); }
.pill.no_channel { background: var(--warn-bg); color: var(--warn); }
.pill.active { background: var(--ok-bg); color: var(--ok); }
.pill.declined { background: var(--err-bg); color: var(--err); }
.pill.waiting::before, .pill.active::before { content: ""; display: inline-block; width: 7px; height: 7px; border-radius: 50%; background: currentColor; margin-right: 6px; vertical-align: 1px; animation: pulse 1.4s infinite; }
@keyframes pulse { 50% { opacity: .25; } }

.alert { margin-top: 16px; padding: 10px 14px; border-radius: 8px; }
.alert.warn { background: var(--warn-bg); color: var(--warn); }
.alert.error { background: var(--err-bg); color: var(--err); }

.steps { list-style: none; display: flex; gap: 8px; padding: 0; margin: 20px 0 4px; flex-wrap: wrap; }
.steps li { flex: 1 1 160px; padding: 8px 12px; border-radius: 8px; border: 1px dashed var(--line); color: var(--muted); font-size: 13px; }
.steps li.done { border-style: solid; border-color: transparent; background: var(--ok-bg); color: var(--ok); }
.steps li.done::before { content: "✓ "; font-weight: 700; }
.steps li.current { border-style: solid; border-color: var(--wait); color: var(--wait); }

.facts { display: grid; grid-template-columns: repeat(auto-fit, minmax(200px, 1fr)); gap: 12px 24px; margin: 16px 0; }
.facts dt { font-size: 12px; color: var(--muted); }
.facts dd { margin: 2px 0 0; overflow-wrap: anywhere; }

.actions { display: flex; gap: 8px; align-items: center; flex-wrap: wrap; }
.actions.end { justify-content: flex-end; margin-top: 20px; }
.btn { font: inherit; font-weight: 600; border-radius: 8px; padding: 8px 16px; cursor: pointer; border: 1px solid var(--line); background: var(--surface); color: var(--text); }
.btn.primary { background: var(--accent); color: var(--accent-ink); border-color: var(--accent); }
.btn.primary:hover { filter: brightness(1.08); }
.btn.ghost:hover { background: var(--bg); }
.btn.small { padding: 4px 10px; font-size: 12px; }
.btn:disabled { opacity: .5; cursor: not-allowed; }

.list { display: grid; gap: 10px; max-height: 640px; overflow: auto; }
.empty { color: var(--muted); margin: 8px 0; }
.msg { border: 1px solid var(--line); border-radius: 8px; padding: 8px 12px; }
.msg-meta { font-size: 12px; color: var(--muted); cursor: pointer; }
.msg { padding: 6px 12px; border-style: dashed; }
.msg pre { margin: 4px 0 0; white-space: pre-wrap; overflow-wrap: anywhere; }
.log-row { display: grid; grid-template-columns: 76px 56px 1fr; gap: 8px; font-size: 13px; padding: 4px 0; border-bottom: 1px solid var(--line); }
.log-row .lvl { font-weight: 700; font-size: 11px; text-transform: uppercase; }
.lvl.ok { color: var(--ok); } .lvl.warn { color: var(--warn); } .lvl.error { color: var(--err); } .lvl.info { color: var(--muted); }

.stats { display: grid; grid-template-columns: repeat(auto-fit, minmax(160px, 1fr)); gap: 16px; }
.stat { background: var(--surface); border: 1px solid var(--line); border-radius: 12px; padding: 16px 20px; }
.stat-v { font-size: 26px; font-weight: 700; font-variant-numeric: tabular-nums; }
.stat-k { color: var(--muted); font-size: 12px; }
pre#last-response { margin: 0; max-height: 320px; overflow: auto; background: var(--bg); padding: 12px; border-radius: 8px; }

form label { display: block; margin-bottom: 14px; font-weight: 600; }
form input { display: block; width: 100%; margin-top: 4px; font: inherit; font-weight: 400; padding: 8px 10px; border-radius: 8px; border: 1px solid var(--line); background: var(--bg); color: var(--text); }
form input:disabled { opacity: .6; }

dialog { border: 0; border-radius: 14px; padding: 24px; width: min(440px, calc(100vw - 32px)); background: var(--surface); color: var(--text); box-shadow: 0 20px 60px rgba(0,0,0,.25); }
dialog::backdrop { background: rgba(15, 18, 25, .45); }
dialog h3 { margin: 0 0 8px; font-size: 18px; }
dialog p { margin: 4px 0; } dialog code { overflow-wrap: anywhere; }

.job { display: flex; align-items: center; gap: 16px; padding: 14px 16px; border: 1px solid var(--line); border-left: 4px solid var(--accent); border-radius: 10px; background: var(--surface); transition: box-shadow .15s, transform .15s; }
.job:hover { box-shadow: 0 4px 16px rgba(16, 24, 40, .08); transform: translateY(-1px); }
.job-main { flex: 1; min-width: 0; }
.job-title { margin: 0 0 6px; font-size: 15px; line-height: 1.35; }
.job-meta { display: flex; align-items: center; gap: 10px; flex-wrap: wrap; }
.job-person { display: inline-flex; align-items: center; gap: 6px; font-weight: 600; }
.avatar { width: 22px; height: 22px; border-radius: 50%; display: inline-grid; place-items: center; background: var(--wait-bg); color: var(--accent); font-size: 11px; font-weight: 700; }
.job-id { font-family: ui-monospace, "SF Mono", Menlo, Consolas, monospace; font-size: 12px; padding: 2px 8px; border-radius: 6px; background: var(--bg); color: var(--muted); }
.job-foot { margin-top: 6px; font-size: 12px; color: var(--muted); }
.jd-dialog { width: min(640px, calc(100vw - 32px)); }
.jd-head { display: flex; justify-content: space-between; gap: 12px; align-items: flex-start; }
.jd-head h3 { margin: 4px 0 2px; }
.btn.icon { padding: 2px 10px; font-size: 20px; line-height: 1; border: 0; background: none; color: var(--muted); }
.jd-body { margin-top: 16px; padding: 14px 16px; background: var(--bg); border-radius: 8px; white-space: pre-wrap; line-height: 1.6; max-height: 60vh; overflow: auto; }

/* collapsible panels */
.head-tools { display: flex; align-items: center; gap: 10px; flex-shrink: 0; }
.collapse-head { cursor: pointer; user-select: none; }
.card.collapsible > .card-head { margin-bottom: 0; }
.card.collapsible > .card-head h3 { margin: 0; }
.collapse-body { display: grid; grid-template-rows: 1fr; transition: grid-template-rows .32s cubic-bezier(.4, 0, .2, 1), opacity .25s ease; }
.collapse-inner { min-height: 0; overflow: hidden; }
.card.collapsible > .card-head + .collapse-body > .collapse-inner { padding-top: 14px; transition: padding-top .32s cubic-bezier(.4, 0, .2, 1); }
.card.collapsed > .collapse-body > .collapse-inner { padding-top: 0 !important; }
.card.collapsed .collapse-body { grid-template-rows: 0fr; opacity: 0; }
.summary { max-height: 0; opacity: 0; overflow: hidden; white-space: nowrap; text-overflow: ellipsis; transition: max-height .3s ease, opacity .25s ease; }
.card.collapsed .summary { max-height: 20px; opacity: 1; margin-top: 2px; }
.toggle {
  width: 30px; height: 30px; border-radius: 50%; display: grid; place-items: center; padding: 0; cursor: pointer;
  border: 1px solid var(--line); background: var(--surface); color: var(--muted);
  transition: background .2s, color .2s, border-color .2s, box-shadow .2s;
}
.toggle:hover { background: var(--wait-bg); color: var(--accent); border-color: transparent; box-shadow: 0 2px 8px rgba(52, 81, 209, .18); }
.toggle:focus-visible { outline: 2px solid var(--accent); outline-offset: 2px; }
.toggle svg { transition: transform .32s cubic-bezier(.4, 0, .2, 1); }
.card.collapsed .toggle svg { transform: rotate(180deg); }
.card.collapsed .toggle { background: var(--accent); color: var(--accent-ink); border-color: var(--accent); }
.card.collapsed { padding-block: 14px; }
.card { transition: padding .3s ease; }

/* members */
.add-btn span { font-size: 15px; line-height: 1; margin-right: 2px; }
.members { display: grid; gap: 8px; }
.member { display: flex; align-items: center; gap: 12px; padding: 12px 14px; border: 1px solid var(--line); border-radius: 10px; }
.member.current { border-left: 4px solid var(--ok); }
.member-main { flex: 1; min-width: 0; }
.member-name { display: flex; align-items: center; gap: 8px; font-weight: 600; flex-wrap: wrap; }
.member-dates { display: flex; gap: 10px; flex-wrap: wrap; margin-top: 4px; padding-left: 30px; font-size: 13px; }
.member-dates .range { font-variant-numeric: tabular-nums; }
.tag { font-size: 11px; font-weight: 700; padding: 1px 8px; border-radius: 99px; }
.tag.ok { background: var(--ok-bg); color: var(--ok); }
.btn.remove { color: var(--muted); }
.btn.remove:hover, .btn.remove.armed { color: var(--err); border-color: var(--err); background: var(--err-bg); }
.month-row { display: grid; grid-template-columns: 1fr 1fr; gap: 12px; }
label.check { display: flex; align-items: center; gap: 8px; font-weight: 400; }
label.check input { width: auto; margin: 0; }
.form-error { color: var(--err); background: var(--err-bg); padding: 8px 12px; border-radius: 8px; margin: 0 0 4px; }
dialog form input { display: block; width: 100%; margin-top: 4px; }
dialog label { display: block; margin-bottom: 14px; font-weight: 600; }
dialog label.check input { display: inline-block; width: auto; margin: 0; }
input:disabled { opacity: .5; }
@media (max-width: 480px) { .month-row { grid-template-columns: 1fr; } .member { flex-direction: column; align-items: stretch; } }

/* similarity + matches */
.sim-row { display: grid; grid-template-columns: 1fr 90px; gap: 12px; align-items: center; margin-top: 4px; }
.sim-row input[type=range] { padding: 0; accent-color: var(--accent); margin: 0; }
.sim-row input[type=number] { margin: 0; }
.tag.sim { background: var(--wait-bg); color: var(--accent); }
.tag.warn { background: var(--warn-bg); color: var(--warn); }
.member-tools { display: flex; gap: 6px; }
.job { flex-direction: column; align-items: stretch; gap: 0; }
.job-top { display: flex; align-items: center; gap: 16px; }
.job.related { border-left-color: var(--ok); }
.tag.related-tag { background: var(--ok-bg); color: var(--ok); }
.match { margin-top: 12px; padding-top: 12px; border-top: 1px dashed var(--line); }
.match.unrelated { margin-top: 8px; padding-top: 8px; }
.match.unrelated .match-title { font-size: 12px; color: var(--muted); font-weight: 400; }
.match-head { display: flex; align-items: center; justify-content: space-between; gap: 10px; }
.match-title { font-size: 13px; font-weight: 600; }
.match.searching .match-title::before, .match.queued .match-title::before { content: ""; display: inline-block; width: 10px; height: 10px; margin-right: 8px; border: 2px solid var(--accent); border-right-color: transparent; border-radius: 50%; animation: spin .8s linear infinite; vertical-align: -1px; }
@keyframes spin { to { transform: rotate(360deg); } }
.match.needs_threshold .match-title, .match.none .match-title { color: var(--warn); }
.match.error .match-title { color: var(--err); }
.repos { display: grid; gap: 8px; margin-top: 10px; }
.repo { padding: 10px 12px; border-radius: 8px; background: var(--bg); border: 1px solid transparent; transition: border-color .15s; }
.repo:hover { border-color: var(--line); }
.repo-top { display: flex; gap: 16px; align-items: center; }
a.repo-name { text-decoration: none; } a.repo-name:hover { text-decoration: underline; }
.repo-main { flex: 1; min-width: 0; }
.repo-name { font-weight: 700; color: var(--accent); overflow-wrap: anywhere; }
.repo-desc { font-size: 13px; margin-top: 2px; }
.repo-facts { display: flex; gap: 10px; flex-wrap: wrap; font-size: 12px; color: var(--muted); margin-top: 4px; }
.score { width: 70px; text-align: right; flex-shrink: 0; }
.score-v { font-weight: 800; font-size: 18px; font-variant-numeric: tabular-nums; color: var(--ok); }
.score-bar { height: 5px; border-radius: 99px; background: var(--line); overflow: hidden; margin-top: 4px; }
.score-bar > div { height: 100%; background: var(--ok); border-radius: inherit; }
.reply { margin-top: 8px; font-size: 12px; font-weight: 600; }
.reply.sent { color: var(--ok); } .reply.failed { color: var(--err); } .reply.pending, .reply.sending { color: var(--muted); }
@media (max-width: 560px) { .job-top { flex-direction: column; align-items: stretch; } }

.analysis { margin-top: 8px; }
.analysis > summary { cursor: pointer; font-size: 12px; font-weight: 700; color: var(--accent); width: fit-content; }
.analysis-grid { display: grid; grid-template-columns: repeat(auto-fit, minmax(260px, 1fr)); gap: 10px; margin-top: 8px; }
.an { background: var(--surface); border: 1px solid var(--line); border-radius: 8px; padding: 10px 12px; border-top: 3px solid var(--accent); }
.an h5 { margin: 0 0 6px; font-size: 12px; text-transform: uppercase; letter-spacing: .04em; color: var(--muted); }
.an ul { margin: 0; padding-left: 18px; font-size: 13px; }
.an li { margin: 3px 0; overflow-wrap: anywhere; }
.an.workflow { border-top-color: #0e9384; } .an.tech_stack { border-top-color: #7a5af8; }
.an.architecture { border-top-color: #dd2590; } .an.know_how { border-top-color: #dc6803; } .an.smart_approach { border-top-color: var(--ok); }
.member { cursor: pointer; transition: border-color .15s, box-shadow .15s; }
.member:hover { border-color: var(--accent); box-shadow: 0 2px 10px rgba(52, 81, 209, .1); }
.btn.edit { color: var(--accent); }

/* AI engine */
.engines { display: grid; gap: 8px; margin: 12px 0; }
.modes { display: grid; grid-template-columns: repeat(auto-fit, minmax(220px, 1fr)); gap: 8px; margin: 12px 0 0; }
.engine { display: flex; gap: 10px; align-items: flex-start; padding: 10px 12px; border: 1px solid var(--line); border-radius: 10px; cursor: pointer; font-weight: 400; margin: 0; }
.engine:has(input:checked) { border-color: var(--accent); background: var(--wait-bg); }
.engine input { margin-top: 3px; accent-color: var(--accent); width: auto; display: inline; }
.key-row { display: flex; gap: 8px; flex-wrap: wrap; }
.key-row input { flex: 1 1 240px; margin: 0; }
.engine-line { font-size: 11px; color: var(--muted); margin-top: 6px; text-transform: uppercase; letter-spacing: .04em; }
.ai-summary { margin: 6px 0 0; font-size: 13px; }
.repo-reason, .repo-fit { font-size: 13px; margin-top: 6px; }
.repo-fit { color: var(--ok); }
.highlights { margin: 8px 0 0; padding-left: 18px; font-size: 13px; }
.highlights li { margin: 3px 0; }
.score.low .score-v { color: var(--warn); } .score.low .score-bar > div { background: var(--warn); }
.an.tools_technologies { border-top-color: #7a5af8; } .an.responsibilities { border-top-color: #0e9384; }
.an.seniority_signals { border-top-color: #dd2590; } .an.ats_keywords { border-top-color: #dc6803; }
.report { margin-top: 10px; }
.report > summary { cursor: pointer; font-size: 12px; font-weight: 700; color: var(--accent); }
.report pre { white-space: pre-wrap; background: var(--bg); padding: 12px; border-radius: 8px; max-height: 420px; overflow: auto; font-size: 12px; }

.session-status { margin-top: 10px; padding: 8px 12px; border-radius: 8px; font-size: 13px; font-weight: 600; }
.session-status.on { background: var(--ok-bg); color: var(--ok); }
.session-status.off { background: var(--warn-bg); color: var(--warn); }

#toast { position: fixed; bottom: 20px; left: 50%; transform: translateX(-50%); background: var(--text); color: var(--bg); padding: 8px 16px; border-radius: 8px; font-weight: 600; }

@media (max-width: 560px) {
  .topbar, main { padding-left: 16px; padding-right: 16px; }
  .tab { padding: 6px 10px; }
  .status-head { flex-direction: column; }
}
~~~~

`static/app.js`: plain ES with `"use strict"`, no modules or libraries. It polls `GET /api/state` every **1.5 s** (errors are ignored, since the server restarts in debug mode) and re-renders everything from that state. Helpers: `$ = id → getElementById`, `fmt(iso)` → `toLocaleString()` or `—`, `time(iso)` → `toLocaleTimeString()`, and `el(tag, cls, text)`. Use `textContent` everywhere; `innerHTML` is only for the chevron SVG.

**Verbatim strings and data**
```js
const STATUS = {
  unregistered: ["Not registered", "This worker has not asked the server to join yet."],
  waiting: ["Waiting for approval", "Registration sent. The server has to approve it and allocate an enrolled ID."],
  no_channel: ["Enrolled — no channel", "The server approved this worker, but no channel is allocated yet."],
  active: ["Working", "Enrolled and connected to a channel. Listening for messages."],
  declined: ["Registration declined", "The server refused the request, or it expired. You can register again."],
};
const CHEVRON = '<svg viewBox="0 0 24 24" width="16" height="16" fill="none" stroke="currentColor" stroke-width="2.4" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><path d="m6 15 6-6 6 6"/></svg>';
const COLLAPSE_KEY = "repo-finder.collapsed";
const ENGINE_NAMES = { claude: "Claude API", session: "Claude Code session", rules: "Rules only" };
const JD = [["must_have_skills", "Must-have skills"], ["tools_technologies", "Tools and technologies"],
  ["responsibilities", "Responsibilities"], ["seniority_signals", "Seniority signals"], ["ats_keywords", "ATS keywords"]];
const ANALYSIS = [["requirements", "Project requirements"], ["workflow", "Workflow"], ["tech_stack", "Tech stack"],
  ["architecture", "Architecture"], ["know_how", "Know-how"], ["smart_approach", "Smart approach"]];
```

Behaviour:
1. **Tabs**: clicking a `.tab` shows `#tab-{name}`, toggles `.active` and calls `history.replaceState` with `#name`. On load, open the hash tab if it is valid, else workspace.
2. **Collapsible panels**: for each `.card.collapsible`, its head (`:scope > .status-head, :scope > .card-head`) stays, and every following sibling moves into `div.collapse-body > div.collapse-inner`. A `button.toggle` with the chevron is appended to the head's `.head-tools`, and the head gets `.collapse-head`. Toggling flips `.collapsed`, sets `body.inert`, `aria-expanded` and the title/ariaLabel `Expand panel`/`Shrink panel`, and stores the collapsed card ids in localStorage under `COLLAPSE_KEY`. Clicking the head toggles too, unless the click was on a button, link or input.
3. `api(path, body)`: GET with `cache:"no-store"` when there is no body, otherwise a JSON POST. A non-OK response throws `data.error || "HTTP n"`. `toast(text)` shows `#toast` for 2.8 s.
4. **Register**: the button fills `#confirm-url` and `#confirm-name` and opens `#confirm-dialog`. On close with returnValue `register`, it disables the button, POSTs `/api/register`, and toasts `Registration sent — waiting for approval` (or the error). **Forget** takes two clicks: the first arms it (`Click again to forget`, which reverts after 3 s), and the second POSTs `/api/cancel` and toasts `Registration forgotten`.
5. **Members dialog**: add or edit. The title is `Add member` or `Edit {name}`, and the submit button reads `Add` or `Save`. The similarity slider and number input stay in sync (default 0.6). "Still a member" disables and clears To. Client validation messages, shown in `#member-error`: `Enter a name.`, `Pick the month they started (From).`, `Pick an end month (To), or tick “Still a member”.`, `“To” can't be before “From”.`, `Similarity must be from 0 to 1.`. Submit to `/api/members/update` (with the id) or `/api/members`, then toast `Saved {name}` or `Added {name}`. Server errors go into `#member-error`. When editing a member whose similarity is null, focus the similarity field.
6. **Members list**: the summary is `N member(s)` or `No members yet`. When empty: `No members yet. Use “Add member” to register one.`. Sort current members (no `to`) first, then by `from` descending. Each row is `.member` (plus `.current` with no `to`) and opens the editor on click. It shows an avatar initial, the name, a `Current` tag, then the dates `Mon YYYY – Mon YYYY|Present` (`toLocaleDateString` month short and year numeric), a span such as `1 yr 3 mos` (inclusive months, defaulting to `1 mo`), and a tag `Similarity ≥ 0.60` (`tag sim`) or `Similarity not set — jobs wait` (`tag warn`). The tools are `✎ Edit` and `Remove`. Remove takes two clicks: `Confirm remove` with the `.armed` class, which reverts after 3 s. The second click POSTs `/api/members/delete` and toasts `Removed {name}`.
7. **Work mode** radios POST `/api/settings {mode}` and toast `Work mode: {strong text}`. The pill shows the mode name. The summary is `{name} · rules in {file}` + ` · GitHub search paused` when the mode is not github.
8. **AI engine** radios POST `{engine}` and toast `AI engine: …`. The radio checked is `settings.choice || engine`. The pill shows `ENGINE_NAMES[engine]`, with the class `pill active` unless the engine is rules. `#session-status` is visible only when the engine is session: class `on` shows `● Claude Code session connected — checked in {n}s ago. Jobs are processed as they arrive.`, and class `off` shows `○ Claude Code session not connected (last seen {min} min ago). Jobs wait in ai_tasks/pending/ until it is.` (the "last seen" part only when known). `#ai-model` shows `(model)`. Key form: an empty key toasts `Paste the API key first`; otherwise save it, clear the field and toast `API key saved`. Remove posts `{clear_key:true}` and toasts `API key removed`. Key status: `API key …abcd from ANTHROPIC_API_KEY|saved.` + ` Select “Claude API” to use it.` when the engine is not claude, or `No API key yet — jobs will fail until one is saved.` (engine claude), or `No API key yet.`. The summary is the engine name + ` (debug mode)` (when debug and choice is auto) + ` · connected|not connected` (session) + ` · N job(s) waiting for the session` (awaiting_ai across matches and compositions) + ` · API spend $x.xx`.
9. **render(s)**: the debug badge and debug card are shown only in debug mode. The status title, text and pill (text with `_` turned into a space, class `pill {status}`) come from the state. `#channel-warning` is shown when the status is no_channel. `#error-box` shows `Can't reach the server ({time}): {text}. Retrying…`. The steps: request is done when `requested_at` is set and the status is not unregistered/declined, or when there is a worker_id; approved is done when there is a worker_id; channel is done when the status is active. The first undone step gets `current` unless the status is unregistered/declined. Facts: server, id (`not allocated`), channels (joined, else `allocated` when active, else `none`) and last contact. The Register and Forget buttons swap when registered. Jobs: `#msg-count` shows `N received`, and the messages are listed newest first through `renderJob`, with the empty text `No jobs yet. They appear here once the server sends them.`. The Report counters show pings, polls, messages and errors. The log is listed newest first as `.log-row` (time, `lvl {level}`, text), or `No activity yet.`. The last response is shown as pretty JSON in debug mode. Profile: only fill the inputs when the form isn't dirty (any input marks it dirty, and saving clears it, showing `Saved` for 2 s). Disable name and server_url while registered and show `#profile-lock`. Identity facts. Summaries: `{name} · {server_url}` and `ID {id}` or `No enrolled ID yet`.
10. **renderJob(m)**: a message with no `body.title` (or `fields.title`) is shown raw as `details.msg`, with the summary `event: X · job Y · by Z — #channel · seq N · time` (or `message — …`) and a `<pre>` of the body. Its open state survives re-renders through `openRows` (key `channel:seq`). A job is an `article.job` with `.job-top` (`.job-main`: `h4.job-title`, `.job-meta` with the person avatar and name or `Unknown` and `.job-id` `#{job or seq}`, and `.job-foot` `#channel · from sender · date`) plus a `View JD` button. That button opens `#jd-dialog` with the meta `Job X · #ch · seq N`, the title, `Posted by P`, and the description or `No description was sent with this job.`. Below that, append `renderMatch(match)` if there is a match and (not missed mode or the match isn't unrelated), and `renderComposition(comp)` if there is a composition and (missed mode or it isn't unrelated). If either record is related, add `.related` and the meta tag `Related · {member}`.
11. **renderMatch(x)**: a `div.match {status}`. The title text per status:
    - unrelated: `Not related — {reason}`
    - needs_threshold: `Related to {member}. Set {member}'s similarity in Profile → Registered members to start the search.`
    - queued: `Queued for a GitHub search…`
    - awaiting_ai: `{Handed to the Claude Code session | Waiting for the Claude Code session to connect} — it searches GitHub (≥ {t}, made within {period}), analyses the picks and writes the report…`
    - searching: `Searching GitHub for repos with similarity ≥ {t}, made within {period}, and analysing them…`
    - found: `Similar repos (≥ {t}, made within {period})`
    - none: `No repo made within {period} reached {t} — the best of {considered} scored {best}`
    - error: `Search failed: {error}`

    `period` = `Mon YYYY – Mon YYYY|now`, or `the registered period`. A `Search again` button appears unless the status is queued, searching, needs_threshold or awaiting_ai; it POSTs `/api/jobs/search` and toasts `Searching again`. For found/none: an `.engine-line` reading `By {engine} · $cost`, and the summary as `p.ai-summary`. For found, each repo is a `.repo` with a link (opening in a new tab, noopener), its description, facts (`created → last_push`, language, parts `k NN%`), a `.score` (value to 2 decimals plus a bar), `Why: …`, `Fit for this job: …`, and `renderAnalysis`. After the repos comes the reply line: `✓ Sent to #{ch} as a reply to job {id}`, `✗ Server refused the reply: {error}`, `Reply waiting: {error}` or `Sending reply…`. For found/none, a `details.report` labelled `Full report`. Every `<details>` keeps its open state in `openRows`.
12. **renderComposition(x)**: a `div.match compose {css}`, where in_progress maps to `searching`, below_score maps to `none`, and anything else keeps its status. The titles:
    - queued: `Queued for missed…`
    - in_progress: `Analysing the JD and missed the experience…`
    - awaiting_ai: `… — it analyses the JD and composes the experience…`
    - needs_threshold: `Related to {member}. Set {member}'s similarity in Profile → Registered members — it is the bar each missed project must reach.`
    - missed: `Missed experience for {member} (every project ≥ {t})`
    - below_score: `Still below {t} after {rounds} rewrite(s) — not posted. Check it and compose again.`
    - error: `Missed failed: {error}`
    - unrelated: as in renderMatch.

    A `Compose again` button (missed, below_score or error) POSTs `/api/jobs/compose` and toasts `Missed again`. For missed/below_score: an engine line `By {engine} · $cost · grounded on {n} repo(s)|no repos · {r} rewrite(s)`, the real problem as `p.ai-summary`, and a `details.analysis` labelled `JD analysis` with an `.analysis-grid` of `section.an {key}` (h5 label plus ul). Then each project as a `.repo`: `Project {i}: {title}`, the context, tech stack facts, a `.score` (plus `.low` when below the threshold), part facts (`real problem NN%` …, plus `credibility cap` when not credible), `Feedback: …` only when below the threshold, and a `ul.highlights`. The reply line is shown when the status is missed or the reply was sent. Last comes a `details.report` labelled `Full missed reply` with `x.text`.
13. **renderAnalysis(a, key)**: a `details.analysis` with the summary `Analysis · {n} findings` and a grid of the ANALYSIS sections.
14. The Activity log's Clear button POSTs `/api/clear-log`.

## Phase 7: Final checks

1. `python3 -m py_compile app.py ai.py finder.py analyzer.py`.
2. Start `python3 app.py` in the background and open `http://127.0.0.1:5000`. Confirm that all three tabs render, the panels collapse and the state survives a reload. Add a member (for example `Zachary Wing`, from `2024-01`, present, 0.6), check that it is listed with `Current`, then edit and remove it.
3. Switch the work mode and AI engine and check that the pills and summaries update. `python3 ai.py tasks` must run.
4. Don't register with the real relay unless I ask you to. Stop the server when you're done.
5. Report what was built and any check that failed.
