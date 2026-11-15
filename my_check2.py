from question2.agent import SupportPipeline

for tid in ["T-1001", "T-1002", "T-1003", "T-1004"]:
    p = SupportPipeline()
    r = p.handle_ticket(tid)
    print(tid, r.action, r.kb_article, "| candidates:", p.last_candidates)
    for c in p.session.calls:
        print("   ", c.label, "in:", c.input_tokens, "cached:", c.cached_prefix_tokens,
              "out:", c.output_tokens, "points:", c.points())