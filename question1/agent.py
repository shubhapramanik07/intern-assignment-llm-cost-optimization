# Lets you write type hints like CostSession | None without errors on older Python. You can ignore it.
from __future__ import annotations

# json: converts between Python dictionaries and JSON text.
import json
# re: regular expressions, for searching patterns in text.
import re
# uuid: generates random unique IDs.
import uuid
# dataclass: a shortcut for making a simple class that just holds data.
from dataclasses import dataclass


# CostSession: the “taxi meter” that records tokens and points.
from shared.billing import CostSession
# all_articles_blob: returns every help-center article as one giant string.
from shared.kb import all_articles_blob
# ChatMessage, LlmClient: the pretend LLM client and its message format.
from shared.llm import ChatMessage, LlmClient
# get_ticket, get_order, format_thread: load a ticket, load its order, and format the conversation as text.
from shared.tools import format_thread, get_order, get_ticket


@dataclass
class TicketResolution:
    action: str
    kb_article: str
    customer_reply: str
    raw_completion: str

# Takes the model’s text and turns it into a TicketResolution.
def parse_resolution(text: str) -> TicketResolution:
    m = re.search(r"\{[\s\S]*\}", text) #Finds the JSON inside the text.
    if not m:
        raise ValueError(f"No JSON in model output:\n{text[:400]}")
    data = json.loads(m.group()) #Converts the found JSON text into a Python dictionary.
    return TicketResolution(
        action=str(data["action"]).upper(),
        kb_article=str(data["kb_article"]),
        customer_reply=str(data.get("customer_reply", "")), #it's optional, eaither reply or empty string if missing.
        raw_completion=text,
    )


# A block of text with four example tickets and their correct answers. It teaches the model the output format by showing examples. It’s sent on every call, so it costs input tokens each time.
_FEW_SHOT = """
Example ticket resolutions (format to copy):

1. Unopened headphones, day 10 → {"action":"REFUND","kb_article":"returns-within-30-days"}
2. Ebook, license shown → {"action":"SEND_ARTICLE","kb_article":"digital-license-no-refund"}
3. Cracked custom mug → {"action":"ESCALATE_TIER2","kb_article":"custom-items-no-replace"}
4. Cracked stock mug, day 2 → {"action":"REPLACE","kb_article":"damage-replacement-7-days"}
"""


class SupportCopilot:
    def __init__(self, session: CostSession | None = None):
        self.session = session or CostSession()
        self.llm = LlmClient(self.session)

    def _static_system(self) -> str:
        return "\n\n".join(
            [
                "You are ShopAssist, a Tier-1 support copilot. "
                "Pick one action (REFUND, REPLACE, SEND_ARTICLE, ESCALATE_TIER2) "
                "and the matching help-center article.",
                "HELP CENTER:",
                all_articles_blob(),
                'Reply with only JSON: {"action":"...","kb_article":"..."}',
            ]
        )

    def build_messages(self, ticket_id: str) -> list[ChatMessage]:
        ticket = get_ticket(ticket_id)
        order = get_order(ticket["order_id"])
        thread = format_thread(ticket)
        # snapshot = json.dumps(order, indent=2)
        snapshot = json.dumps(order, separators=(",", ":"))
        user = "\n\n".join(
            [
                f"TICKET_ID: {ticket_id}",
                f"THREAD:\n{thread}",
                f"ORDER_SNAPSHOT:\n{snapshot}\nEND_SNAPSHOT",
            ]
        )
        return [ChatMessage("system", self._static_system()), ChatMessage("user", user)]

    def handle_ticket(self, ticket_id: str) -> TicketResolution:
        messages = self.build_messages(ticket_id)
        prefix = "SYSTEM:\n" + messages[0].content
        completion = self.llm.chat("handle_ticket", messages, cached_prefix=prefix)
        return parse_resolution(completion)

# Line	Problem
# f"Run id {uuid.uuid4()}"	-> A random ID at the very start of the prompt means the beginning changes every call, so caching never works. It’s likely planted on purpose.
# all_articles_blob() -> Sends every article, but a ticket needs only 1 or 2.
# “Think step by step. Write full reasoning, quote any article...”	-> Makes the model write a long answer. Output costs 15 points per token.
# customer_reply in the JSON	-> More output tokens, and it isn’t graded.
# _FEW_SHOT	-> Extra tokens on every call. Possibly keepable if you move it into the cacheable prefix.
# json.dumps(order, indent=2)	-> Extra whitespace tokens. Use compact JSON.
# Static and ticket parts mixed	-> Caching needs the identical part first. Here the static part is mostly in the system message, which is good, but the random ID ruins it.