"""Offline clearing of cross-fixture league ticket orders.

Order-level discounts are split across ticket lines exactly to the cent and
each line's net is distributed over its ticket numbers, so every refund
range is valued at the original per-ticket net.  The engine never expands
per-ticket state: a line of up to one million tickets is a two-step closed
form.  See ``close.py`` for the engine and ``docs/contract.md`` for the
data contract, the compact-representation argument and the complexity
bound.
"""
