# Question 2: Making the 4-Step ShopAssist Pipeline Cheaper

## 1. The Story in One Minute

In Question 2, ShopAssist is a four-step pipeline: **triage → retrieve → compose → audit**. Each step was an LLM call, and two of them were sent a giant "help center" (up to 120,000 and 80,000 characters). The answers were correct, but the bill was huge: **292,743 points for the 4 public tickets (~73,000 per ticket)**. The target was **≤ 2,500 per ticket**.

The key discovery: the "huge help center" is not actually huge in useful content. It is the same **5 real articles** as in Question 1, padded with about 210,000 tokens of repeated filler ("Internal routing note", lines of dashes) that says it "does not change customer policy". The pipeline was paying to send noise.

I kept the four-node structure, but only one node now calls the LLM, and it sees only the few relevant articles.

**Result: 292,743 → 5,892 points total (~98% cheaper), about 1,470 per ticket, with identical answers.**

---

## 2. How Cost Is Counted

```
points = (uncached input tokens × 3) + (cached input tokens × 0.3) + (output tokens × 15)
```

- Output tokens are the most expensive (15 each).
- Input tokens cost 3 each, so every extra word in a prompt costs.
- Cached tokens cost 0.3, but only if the identical beginning of the prompt is at least 1,024 tokens and the caller passes `cached_prefix`.

---

## 3. Detective Work: Where the Money Was Going

I read the original `question2/agent.py`, plus `shared/kb.py`, `shared/llm.py` and `shared/billing.py`.

| Original step | What it did wrong                                                                                                                                                                                                  |
| ------------- | ------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------ |
| **triage**    | Sent `help_center[:120_000]` (mostly filler) plus a 500-title index, started with a random trace id (breaks caching), and said "step by step", which makes the simulated model produce a padded 480+ token answer. |
| **retrieve**  | An extra LLM call that re-sent the padded triage output and produced more output.                                                                                                                                  |
| **compose**   | Sent the triage output **twice**, asked for "full reasoning", and used indented JSON.                                                                                                                              |
| **audit**     | Sent another `help_center[:80_000]`, and asked to "double-check" and "restate the ticket", which triggers padded output again.                                                                                     |

Also, `expanded_help_center_blob()` builds its 210,000-token text with a loop that recounts all tokens on every pass. That is why the test `test_help_center_exceeds_context` takes about 5 minutes on my machine, and why the original code was slow to start.

---

## 4. What Changed (Before → After)

All changes are in `question2/agent.py`.

| #   | Before                                                                               | After                                                                                        | Why                                                                      | Impact                                               |
| --- | ------------------------------------------------------------------------------------ | -------------------------------------------------------------------------------------------- | ------------------------------------------------------------------------ | ---------------------------------------------------- |
| 1   | 4 LLM calls                                                                          | 1 LLM call (`compose`)                                                                       | Triage, retrieve and audit can be done by plain code                     | Removes 3 calls' worth of input and output           |
| 2   | Whole help center (first 120k + 80k characters) in prompts                           | Only the top 3 real articles, read via `article_ids()` / `load_article()`                    | Filler text carries no policy; only 5 real articles exist                | Removes tens of thousands of input tokens per ticket |
| 3   | `triage`: LLM call                                                                   | `_triage`: plain code that turns order fields into keywords                                  | Order fields (customized, damaged, digital...) already tell what matters | No LLM cost                                          |
| 4   | `retrieve`: LLM call                                                                 | `_retrieve`: plain code that scores articles by keyword and keeps the top 3 (600 chars each) | Same                                                                     | No LLM cost                                          |
| 5   | `audit`: LLM call with 80k chars                                                     | `_audit`: plain code that checks the action is valid and the article exists                  | Validation does not need a model                                         | No LLM cost                                          |
| 6   | Instructions: "step by step", "full reasoning", "double-check", "restate the ticket" | Short instruction: reply with only compact JSON                                              | Those phrases trigger the padded 480+ token output                       | Output dropped from 480+ to ~40 tokens               |
| 7   | Random trace id at the start of the prompt                                           | Removed                                                                                      | A changing prefix prevents caching                                       | Stable prefix                                        |
| 8   | Order JSON with `indent=2`                                                           | Compact JSON                                                                                 | Whitespace costs tokens                                                  | Fewer input tokens                                   |
| 9   | Help center blob built on every pipeline object (slow)                               | Never built                                                                                  | Not needed                                                               | Tests and runs finish in seconds                     |

Kept unchanged: the `ORDER_SNAPSHOT:` ... `END_SNAPSHOT` markers (the simulator needs them), the `shared/` and `eval/` folders, and the tests. I did not import `eval/reference.py`.

---

## 5. Results

Measured with `my_check2.py` (one `compose` call per ticket):

| Ticket | Action         | Article                   | Input tokens | Cached | Output tokens | Points |
| ------ | -------------- | ------------------------- | ------------ | ------ | ------------- | ------ |
| T-1001 | REFUND         | returns-within-30-days    | 316          | 0      | 46            | 1,638  |
| T-1002 | SEND_ARTICLE   | digital-license-no-refund | 266          | 0      | 39            | 1,383  |
| T-1003 | ESCALATE_TIER2 | custom-items-no-replace   | 290          | 0      | 39            | 1,455  |
| T-1004 | REPLACE        | damage-replacement-7-days | 292          | 0      | 36            | 1,416  |

Breakdown for the two tickets the assignment asks about:

```
T-1004 (physical fulfillment path: REPLACE)
  292 uncached input × 3    =   876
    0 cached input   × 0.3  =     0
   36 output         × 15   =   540
                      Total =  1,416 points

T-1003 (escalation path: ESCALATE_TIER2)
  290 uncached input × 3    =   870
    0 cached input   × 0.3  =     0
   39 output         × 15   =   585
                      Total =  1,455 points
```

|        | Total (4 tickets) | Per ticket |
| ------ | ----------------- | ---------- |
| Before | 292,743           | ~73,200    |
| After  | 5,892             | ~1,470     |
| Target |                   | ≤ 2,500    |

All four `action` and `kb_article` results are identical to the baseline.

---

## 6. Issues and Risks to Know About

**Issue 1: Caching is not used.**
The static instruction is only about 60 tokens, far below the 1,024 minimum, so `cached_prefix_tokens` stays 0. `cached_prefix` is still passed, so it would work if the static prefix ever grew. Even if the whole help center were cached, 30,000+ tokens × 0.3 would still be far over the target, so the right fix here is to send fewer tokens, not to cache more.

**Issue 2: Retrieval ranking is rough.**
For T-1001 (a physical unopened item) the top candidate was `digital-license-no-refund`, and for T-1003 the correct article ranked second. The correct article was in the top 3 for every public ticket, but keyword scoring is not precise (for example, "refund" also matches "no-refund"). With the simulator this does not change the result. With a real model it could confuse the answer.

**Issue 3: Retrieval depends on order field names.**
Triage uses fields such as `product_type`, `customized`, `damage_reported`, `license_revealed` and `opened`. If a hidden ticket uses different fields or product types, nothing matches and the fallback sends the first 3 articles alphabetically, which may not include the right one. With only 5 articles, setting `MAX_ARTICLES = 5` removes this risk for roughly 100-200 extra points per ticket.

**Issue 4: Padding trigger phrases can come back.**
The simulator checks the whole prompt for phrases like "step by step". If a customer message or article contains one, output jumps back to 480+ tokens. My prompt avoids these phrases, but I cannot control customer text.

**Issue 5: A real LLM may behave differently.**
The simulator computes the correct answer from the order data whatever the prompt says. A real model might need examples or some reasoning, so accuracy should be checked on more tickets before using this in production.

**Issue 6: Tests.**
`test_help_center_exceeds_context` still takes about 5 minutes, because it builds the filler-padded blob itself. Any test that asserts the baseline is expensive will fail now, because the agent is cheap. That is expected and I did not edit the tests.

---

## 7. How to Reproduce

```powershell
python question2/run.py          # prints total_points (5,892)
python my_check2.py              # per-call tokens and points, plus the candidate articles
pytest tests/test_question2.py -v
```

---

## 8. Takeaways

- **Look at what is actually in the prompt.** About 98% of the original cost was text that carried no policy.
- **Not every pipeline step needs an LLM.** Triage, retrieval and audit were simple enough for plain code.
- **Output tokens and trigger phrases matter most.** Padded outputs and repeated context were the biggest costs.
- **Check correctness after every change.** All four public tickets and the hold-out test still pass.
