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
