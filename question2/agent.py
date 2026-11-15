from __future__ import annotations

import json
from dataclasses import dataclass

from question1.agent import parse_resolution
from shared.billing import CostSession
from shared.kb import article_ids, load_article
from shared.llm import ChatMessage, LlmClient
from shared.tools import format_thread, get_order, get_ticket

ALLOWED_ACTIONS = {"REFUND", "REPLACE", "SEND_ARTICLE", "ESCALATE_TIER2"}
MAX_ARTICLES = 3          # candidate articles sent to the model
MAX_ARTICLE_CHARS = 600   # characters kept per candidate article

# Static instructions. Kept identical on every call so it can act as a cacheable prefix.
SYSTEM_PROMPT = (
    "You are ShopAssist, a Tier-1 support copilot. "
    "Choose one action (REFUND, REPLACE, SEND_ARTICLE, ESCALATE_TIER2) and the one "
    "best-matching article slug from the CANDIDATE ARTICLES. "
    'Reply with only JSON: {"action":"...","kb_article":"..."}'
)


@dataclass
class TicketResolution:
    action: str
    kb_article: str


class SupportPipeline:
    """Same four nodes as before, but only `compose` pays for an LLM call.

    triage   -> plain code: turn order facts into search keywords
    retrieve -> plain code: pick the most relevant real help-center articles
    compose  -> ONE small LLM call: ticket + order + candidate articles -> compact JSON
    audit    -> plain code: validate the action / article before returning

    The ~210k-token "expanded help center" is filler around the real articles, so it is
    never built or sent. Real articles are read directly with article_ids()/load_article().
    """

    def __init__(self, session: CostSession | None = None):
        self.session = session or CostSession()
        self.llm = LlmClient(self.session)
        self.last_candidates: list[str] = []  # slugs sent to the model (handy for debugging)

    # ---- node 1: triage (code) ------------------------------------------------
    def _triage(self, order: dict) -> list[str]:
        ptype = str(order.get("product_type", "")).lower()
        title = str(order.get("title", "")).lower()
        words: list[str] = []
        if order.get("customized"):
            words += ["custom", "personal"]
        if order.get("damage_reported"):
            words += ["damage", "replacement"]
        if ptype == "digital" or order.get("license_revealed"):
            words += ["digital", "license"]
        if "subscription" in ptype or "subscription" in title:
            words += ["subscription", "cooling"]
        if ptype == "physical":
            words += ["return", "refund"]
        if order.get("opened"):
            words += ["opened"]
        return words

    # ---- node 2: retrieve (code) ----------------------------------------------
    def _retrieve(self, keywords: list[str]) -> list[tuple[str, str]]:
        ids = article_ids()
        scored: list[tuple[int, str]] = []
        for slug in ids:
            slug_l = slug.lower()
            head = load_article(slug)[:300].lower()
            score = sum(3 * slug_l.count(k) + head.count(k) for k in keywords)
            if score > 0:
                scored.append((score, slug))
        scored.sort(key=lambda s: -s[0])  # stable: ties keep alphabetical order
        slugs = [slug for _, slug in scored[:MAX_ARTICLES]]
        if not slugs:  # nothing matched: fall back to all articles (there are only a few)
            slugs = ids[:MAX_ARTICLES]
        return [(s, load_article(s).strip()[:MAX_ARTICLE_CHARS]) for s in slugs]

    # ---- node 3: compose (the only LLM call) ----------------------------------
    def _compose(
        self,
        ticket_id: str,
        thread: str,
        snapshot: str,
        candidates: list[tuple[str, str]],
    ) -> str:
        cand_text = "\n".join(f"[{slug}] {text}" for slug, text in candidates)
        user = "\n\n".join(
            [
                f"TICKET {ticket_id}",
                f"THREAD:\n{thread}",
                f"CANDIDATE ARTICLES:\n{cand_text}",
                f"ORDER_SNAPSHOT:\n{snapshot}\nEND_SNAPSHOT",
            ]
        )
        messages = [ChatMessage("system", SYSTEM_PROMPT), ChatMessage("user", user)]
        prefix = "SYSTEM:\n" + SYSTEM_PROMPT  # identical every call -> cacheable if >= 1024 tokens
        return self.llm.chat("compose", messages, cached_prefix=prefix)

    # ---- node 4: audit (code) -------------------------------------------------
    def _audit(self, completion: str) -> TicketResolution:
        parsed = parse_resolution(completion)
        if parsed.action not in ALLOWED_ACTIONS:
            raise ValueError(f"Audit failed: unknown action {parsed.action!r}")
        if parsed.kb_article not in set(article_ids()):
            raise ValueError(f"Audit failed: unknown article {parsed.kb_article!r}")
        return TicketResolution(parsed.action, parsed.kb_article)

    # ---- orchestration --------------------------------------------------------
    def handle_ticket(self, ticket_id: str) -> TicketResolution:
        ticket = get_ticket(ticket_id)
        order = get_order(ticket["order_id"])
        thread = format_thread(ticket)
        snapshot = json.dumps(order, separators=(",", ":"))

        keywords = self._triage(order)
        candidates = self._retrieve(keywords)
        self.last_candidates = [slug for slug, _ in candidates]
        completion = self._compose(ticket_id, thread, snapshot, candidates)
        return self._audit(completion)