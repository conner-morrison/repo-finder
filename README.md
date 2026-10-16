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

A job is **related** to a member when the job's `person` is that member and
the job was posted inside the member's period. A repost of a job without a
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

The scoring and analysis are rule-based (no language model), so they reflect
what a repository says about itself.

## State

Everything — registration, enrolled id, members, received jobs, results, log —
is kept in `state.json` (git-ignored: it holds the worker's bearer id), so a
restart resumes where it left off.
