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
