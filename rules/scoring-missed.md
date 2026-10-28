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
