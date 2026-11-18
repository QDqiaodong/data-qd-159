"""Venue capacity audit for already-scheduled, not-yet-played fixtures.

The audit never expands minute-by-minute state and never recomputes the whole
plan per booking: see ``audit.py`` for the sweep-line engine and ``docs/
contract.md`` for the data contract, correctness argument and complexity.
"""
