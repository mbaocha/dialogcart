"""
NLU package — SLM-based replacement for the luma rule-based pipeline.

Architecture:
    Stage 1: SLM extraction (HaikuExtractor — replaces luma stages 1-5)
    Stage 2: Calendar binding (ISO-8601 date resolution)

API contract: /resolve → {intent, facts, time_constraint?} on port 9002.
"""
