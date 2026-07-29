---
name: research-paper-evidence-workflow
description: Map research-paper claims to supplied evidence, synthesize completed results, construct an evidence-backed outline, and audit a draft for traceability, numeric fidelity, scope, and overclaiming. Use when notes, tables, figures, result files, or a manuscript need a claim-evidence matrix, results narrative, outline, or evidence-focused review. Do not use to design or run experiments, retrieve citations, format or compile LaTeX, manage projects, submit or promote papers, or perform external writes.
version: 1.0.0
author: Orchestra Research, AIP AstroAgent team, and Skill Commons contributors
license: MIT
metadata:
  hermes:
    category: general
    tags:
      - research
      - evidence
      - scientific-writing
      - results
      - review
      - traceability
---

# Research Paper Evidence Workflow

## Boundaries

Work only from the evidence artifacts and manuscript material supplied or explicitly
placed in scope. Inspect them locally and read-only by default.

- Do not execute project code, notebooks, builds, or experiments.
- Do not invent results, uncertainty, citations, source locations, or missing context.
- Do not design follow-up experiments. Record an evidence gap instead.
- Do not create jobs, schedules, subagents, messages, commits, pull requests, uploads,
  submissions, talks, posters, or social-media material.
- Do not clean up or delete files.
- Do not expose credentials, personal data, unpublished participant data, or other
  sensitive values discovered in the evidence set.
- Write an artifact only when the user requests one and approves its destination.

Route literature discovery and citation verification to `arxiv`. Route manuscript
authoring and compilation to `latex-research-paper`, and submission packaging to
`latex-journal-submission-package`.

## Workflow

### 1. Establish the evidence set

Inventory the supplied artifacts before drawing conclusions. For each artifact, record:

- its exact path or stable identifier;
- its type and stated purpose;
- whether it was read successfully;
- the relevant table, figure, row, section, or field;
- any confidentiality or data-handling constraint.

Do not treat a filename, caption, or expected output as proof that a result exists. Mark
unreadable, absent, truncated, or ambiguous evidence explicitly.

### 2. Normalize candidate claims

Extract claims from the manuscript, notes, captions, or requested contribution. Give each
claim a stable ID such as `C01`. Preserve the original manuscript location when reviewing
a draft.

Classify each claim as one or more of:

- contribution or method;
- quantitative result;
- comparison;
- causal statement;
- scope or generalization;
- limitation;
- interpretation.

Keep observation, derived quantity, and interpretation distinct. Treat causal language as
unsupported unless the supplied design and evidence justify causation.

### 3. Build the claim-evidence ledger

Use this minimum schema:

| Field | Required content |
|---|---|
| Claim ID | Stable identifier |
| Claim and location | Exact or faithful wording plus manuscript location |
| Evidence pointer | File or artifact plus table, figure, row, section, or field |
| Status | `supported`, `partially supported`, `unsupported`, `conflicting`, or `unclear` |
| Scope and conditions | Population, dataset, split, model, configuration, or assumptions |
| Quantitative detail | Value, unit, denominator, sample size, precision, and uncertainty |
| Caveat | Limitation, confound, alternative explanation, or mismatch |
| Target section | Where the claim belongs in the paper |

Assign statuses conservatively:

- `supported`: the exact claim, scope, and strength follow from the cited evidence;
- `partially supported`: some wording, scope, or quantitative detail exceeds the evidence;
- `unsupported`: no supplied evidence supports the claim;
- `conflicting`: supplied artifacts disagree materially;
- `unclear`: the artifact cannot be interpreted confidently without more context.

Never silently reconcile inconsistent values. Record every conflicting pointer.

### 4. Synthesize completed results

Let the evidence determine the story. Produce:

1. a one-sentence main finding;
2. a concise finding for each supported or partially supported claim;
3. null, negative, and conflicting results;
4. limitations, confounds, and alternative explanations;
5. unresolved evidence gaps;
6. exact evidence pointers for every item.

Copy numerical values faithfully. Preserve units, signs, precision, denominators, sample
sizes, intervals, and stated uncertainty. Do not add statistical significance or practical
importance when the supplied evidence does not establish it.

### 5. Construct an evidence-backed outline

Build a format-neutral outline around one coherent contribution rather than a list of
artifacts. For every section or subsection, specify:

- its purpose;
- the claim IDs it advances;
- the evidence pointers it uses;
- the caveats it must retain;
- the transition to the next section.

Include limitations and contradictory evidence where they affect interpretation. Do not
introduce a new scientific claim in the conclusion. Leave venue formatting and LaTeX
structure to the downstream authoring skill.

### 6. Audit an existing draft

Retrace every factual, numeric, comparative, causal, and scope-bearing statement to the
ledger. Check:

- exact value and unit parity;
- population, dataset, model, and condition parity;
- correlation versus causation;
- uncertainty, denominator, and sample-size reporting;
- consistency between text, tables, figures, and captions;
- stable terminology and claim strength;
- whether the abstract and conclusion overstate the supported contribution.

Return a prioritized issue ledger with severity, exact manuscript location, claim ID,
evidence status, and the smallest defensible remedy: correct, qualify, relocate, remove,
or mark `[VERIFY]`. Ignore cosmetic prose issues unless they obscure the evidence.

### 7. Deliver the review

Unless the user requests a narrower output, provide:

1. contribution statement;
2. claim-evidence ledger;
3. result synthesis;
4. evidence-backed outline;
5. prioritized issue ledger;
6. unresolved questions and missing evidence.

Keep pointers usable by another researcher. If an artifact is sensitive, use a minimally
identifying local pointer and avoid reproducing sensitive content in the report.

## Verification

- Every reported result and manuscript issue has an exact evidence pointer.
- Every candidate claim has a status, including unsupported and conflicting claims.
- Numeric values preserve the supplied units, precision, denominators, and uncertainty.
- Null and negative results remain visible.
- Causal and generalized claims do not exceed the supplied evidence.
- The outline contains no unsupported claim and no conclusion-only claim.
- No code, experiment, network write, project mutation, or external communication occurred.
