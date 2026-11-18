"""Offline per-player suspension accounting over a fixed league snapshot.

Registrations, case issuances and revocations take effect before the fixture
of a given sequence number kicks off; case balances follow players across
transfers.  See ``engine.py`` for the ordered replay and ``docs/contract.md``
for the data contract, correctness argument and complexity bound.
"""
