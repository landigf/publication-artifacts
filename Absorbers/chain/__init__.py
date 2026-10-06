"""The deeper chain: parse, justify, decide (policy v2), explain.

Isolated from the frozen A0..A3 machinery. Reuses agents.llm_client, agents.base,
taskgen and agents.policy_core; never modifies them. Its requests, policy and
results live in their own namespace so the three frozen sweeps are untouched.
"""
