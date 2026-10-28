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
