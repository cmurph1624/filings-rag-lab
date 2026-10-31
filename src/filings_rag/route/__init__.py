"""Query routing (P2): decide whether a question is answered from XBRL facts, prose, or both.

`[router] mode`:
- "rules": metric phrases, judgment cues and period regexes. No model.
- "llm": a small model returns the plan as JSON, cached per question in `query_routes`.

A plan names the route (fact | mixed | prose, as in `routes@1`), the metrics from the concept
map, the periods the question needs data from (FinanceBench labels), per-metric options for
formulas the question spells out (e.g. DPO over purchases), and an aggregate ("average" when
the question asks for a multi-year average). `facts.py` turns a plan into computed facts.
"""
