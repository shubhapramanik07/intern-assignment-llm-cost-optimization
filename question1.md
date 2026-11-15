# Question 1: Making ShopAssist Cheaper (Story + Write-up)

## 1. The Story in One Minute

ShopAssist is a support bot. For each ticket it picks an **action** (`REFUND`, `REPLACE`, `SEND_ARTICLE`, `ESCALATE_TIER2`) and a **help-center article** (`kb_article`).

The bot was already **correct**. It was just **expensive**: about **35,316 points** for the 4 public tickets (~8,800 per ticket). The target was **≤ 3,000 per ticket**.

I did not make the bot smarter. I found out *why* each call was costing so much and removed the waste, without touching the answers.

**Result: 35,316 → 6,780 points total (~81% cheaper), about 1,700 per ticket, with identical answers.**

---

## 2. How Cost Is Counted

Every LLM call costs points:

```
points = (uncached input tokens × 3) + (cached input tokens × 0.3) + (output tokens × 15)
```

What this means in practice:

- **Output is the most expensive part.** One output token costs 5× an input token.
- **Input tokens cost 3 each.** Shorter prompts are cheaper.
- **Cached tokens cost 0.3 each (10× cheaper)**, but only if the beginning of the prompt (the prefix) is identical across calls and at least 1,024 tokens long.

---

## 3. Detective Work: Where the Money Was Going

I printed the actual prompt and completion of one call (`my_check.py`) and read `shared/llm.py` and `shared/billing.py`. Three discoveries:

1. **The simulated model pads its answer when it sees certain phrases.** If the prompt contains words like *"step by step"*, *"full reasoning"*, or *"quote the"*, the model returns a 480+ token essay full of repeated "Additional audit note" lines. The original instructions said exactly that ("Think step by step. Write full reasoning, quote any article…"). At 15 points per output token, this was the biggest cost.
2. **A random ID was at the very start of the prompt** (`Run id <uuid>`). The prompt began differently on every call, so caching could never work.
3. **The cache was never switched on.** `chat()` only caches if the caller passes `cached_prefix=`, and the original code never did (`cached_prefix_tokens=0`).

---

## 4. What Changed (Before → After)

All changes are in `question1/agent.py`.

| # | Before | After | Why I did it | Impact |
|---|--------|-------|--------------|--------|
| 1 | Instructions: "Think step by step. Write full reasoning, quote any article you rely on…" | "Reply with only JSON: `{"action":"...","kb_article":"..."}`" | Those phrases triggered the 480+ token padded output | Biggest saving: output dropped from ~480+ tokens to ~40 |
| 2 | `Run id {uuid.uuid4()}` at the start of the system prompt | Removed | A changing prefix breaks caching, and the ID has no use | Prompt prefix is now identical every call |
| 3 | `_FEW_SHOT` examples sent on every call | No longer sent (text left in the file, unused) | Extra input tokens on every call. They also contained the exact answers for the public tickets, so relying on them would not generalize | Fewer input tokens |
| 4 | Order JSON with `indent=2` | Compact JSON `separators=(",", ":")` | Indentation/newlines add tokens for no benefit | Fewer input tokens |
| 5 | Static and changing content mixed, no cache hint | Static content in `_static_system()` (first), ticket content last, and `cached_prefix=` passed to `chat()` | Caching only works if the identical part comes first and is declared | Caching is *ready*, but see Issue 2 below |
| 6 | Asked the model for `customer_reply` | Not asked for | Not graded, costs output tokens | Smaller output |

Things deliberately **not** changed:

- `parse_resolution` (still works, `customer_reply` is optional in it).
- `ORDER_SNAPSHOT:` ... `END_SNAPSHOT` markers. The simulator needs them to find the order data.
- The `shared/` and `eval/` folders and the tests.
- I did not import `eval/reference.py`.

---

## 5. Results

Per ticket (measured with `my_check.py`):

| Ticket | Action | Input tokens | Cached | Output tokens | Points |
|--------|--------|-------------|--------|---------------|--------|
| T-1001 | REFUND | 377 | 0 | 46 | 1,821 |
| T-1002 | SEND_ARTICLE | 369 | 0 | 39 | 1,692 |
| T-1003 | ESCALATE_TIER2 | 356 | 0 | 39 | 1,653 |
| T-1004 | REPLACE | 358 | 0 | 36 | 1,614 |

Example breakdown for T-1001:

```
377 uncached input × 3  = 1,131
  0 cached input   × 0.3 =     0
 46 output         × 15  =   690
                    Total  = 1,821 points
```

| | Total points (4 tickets) | Per ticket |
|---|---|---|
| Before | 35,316 | ~8,800 |
| After | 6,780 | ~1,700 |
| Target | | ≤ 3,000 |

The correct `action` and `kb_article` for all 4 tickets are unchanged, and `test_holdout_subscription` (a ticket type outside the public 4) passes.

---

## 6. Issues and Risks to Know About

**Issue 1: One test fails on purpose.**
`test_baseline_cost_is_high` asserts the agent costs more than 15,000 points. After optimization it costs 6,780, so the assertion fails. This is expected, because that test only guards the original baseline. I did not edit the test.

**Issue 2: Caching is not actually used in Question 1.**
The prompt is only ~370 tokens and the cache needs a prefix of at least 1,024 tokens, so `cached_prefix_tokens` stays 0. The code is ready, and caching switches on automatically if the static part grows past 1,024 tokens. I did not pad the prompt with filler text to force it, since that would be gaming the meter, not optimizing.

**Issue 3: The simulator's "verbose trigger" phrases can come back.**
The simulator searches the whole prompt, including the customer's message, for phrases like "step by step". If a hidden ticket's thread contains one, the output turns verbose again and the cost jumps (~7,000+ points). My code cannot control customer text.

**Issue 4: The whole help center is still sent on every call.**
`all_articles_blob()` sends every article. This is fine with the 5 short articles in Question 1, but it scales badly. Question 2's help center is much larger, so articles must be retrieved selectively there.

**Issue 5: A real LLM would behave differently from the simulator.**
The simulator computes the right answer from the order data no matter how the prompt is worded. A real model might need examples or a bit of reasoning to stay accurate, so removing them could reduce accuracy. Before using this in production, I would check accuracy on a larger set of tickets.

**Issue 6: JSON parsing is a little fragile.**
`parse_resolution` takes everything from the first `{` to the last `}`. That works for one clean JSON object, but would break if the model added extra braces in its text.

---

## 7. How to Reproduce

```powershell
python question1/run.py                    # prints total_points
python my_check.py                         # per-ticket token and points breakdown
pytest tests/test_question1.py -v          # test_baseline_cost_is_high is expected to fail
```

> Note: `tests/test_question2.py::test_help_center_exceeds_context` takes ~5 minutes on my machine because it counts tokens in a very large help center. That is slow, not stuck.

---

## 8. Takeaways

- **Read the billing rule first.** Output costs 15/token, so shorter answers beat everything else.
- **Look at the real prompt and completion.** Printing one call revealed the padding, the random ID and the missing cache hint.
- **Cost optimization must not change answers.** I checked the 4 public tickets and the hold-out test after every change.
- **Static content first, changing content last**, so caching can work when it applies.