"""Sweep-line capacity/load audit with per-booking relief.

Input model (already validated upstream)
----------------------------------------
* ``venues``  maps venue id -> default capacity (0..1000).
* ``limits``  lists ``(venue, start, end, capacity)`` half-open restrictions;
  they may overlap and are never order-overriding, so the effective capacity
  on any minute is ``min(default, every active limit)``.
* ``bookings`` lists ``(fixture, venue, start, end, units)``; each pending
  fixture has at most one booking.

The engine sweeps the union of all limit/booking endpoints per venue, keeping
the effective capacity (a min-heap with explicit retirement) and the total
load of active bookings, so it produces at most O(N) segments and never
expands per-minute state.

Limit lifecycle under half-open semantics
-----------------------------------------
Each limit pushes the unique token ``(capacity, end, seq)`` onto a min-heap
at its start and marks that same token retired at its end boundary.  The
effective-capacity reader pops heap tops while their token is retired.  A
plain "pop expired heap top" rule is unsound because an expired high-capacity
entry can hide below an active low-capacity top and resurface later as a
phantom minimum; the explicit retirement set removes entries regardless of
heap position.  Every limit is pushed and popped at most once, so the heap
work stays O(N log N).

Per-booking relief without per-booking plan recomputation
---------------------------------------------------------
On an overloaded segment of length ``d`` with capacity ``c``, load ``L`` and
excess ``e = L - c > 0``, cancelling a booking of ``u`` active units removes
``d * min(u, e)`` excess unit-minutes.  The active unit classes are
``1..L``; split them at the excess:

* classes ``1..e`` contribute ``d * u`` each,
* classes ``e+1..L`` contribute the flat ``d * e`` each.

Two Fenwick (BIT) trees over the unit axis 1..1000 accumulate, for every
threshold ``u`` in one sweep:

* ``bit_minutes`` gets a range add of ``d`` on ``1..e``;
* ``bit_flat`` gets a range add of ``d * e`` on ``e+1..L``.

A point query pair then answers the relief of any booking of size ``u``:

    relief(u) = bit_minutes.point(u) * u + bit_flat.point(u)

The identity is checked by brute force over random segment sets in the test
suite.  Each booking stores a snapshot pair at its start event and evaluates
the same pair at its end event (half-open boundaries), so its relief is
measured against one shared original plan; relieves are never combined and
cannot be interpreted as a joint-cancellation effect.
"""
from heapq import heappop, heappush

UNIT_MAX = 1000


class _RangeAddPointQuery:
    """Fenwick tree supporting range adds and point queries in O(log 1000)."""

    __slots__ = ('tree',)

    def __init__(self):
        self.tree = [0] * (UNIT_MAX + 1)

    def add(self, index, delta):
        tree = self.tree
        size = UNIT_MAX
        while index <= size:
            tree[index] += delta
            index += index & -index

    def range_add(self, left, right, delta):
        """Add ``delta`` to every index in the closed interval [left, right]."""
        if left > right or delta == 0:
            return
        self.add(left, delta)
        if right + 1 <= UNIT_MAX:
            self.add(right + 1, -delta)

    def point(self, index):
        tree = self.tree
        total = 0
        while index > 0:
            total += tree[index]
            index -= index & -index
        return total


def _venue_audit(default_capacity, limits, bookings):
    """Audit one venue.

    Returns ``(overload_segments, relief_by_fixture, total_excess)`` where
    each overload segment is ``(start, end, capacity, load)`` already merged
    across endpoints whose capacity and load are unchanged.
    """
    # events: (time, kind, value, tag), kinds:
    #   1 limit end (boundary marker), 2 limit start,
    #   3 booking end,                4 booking start.
    events = []
    for seq, (start, end, capacity) in enumerate(limits):
        token = (capacity, end, seq)
        events.append((start, 2, token, 1))
        events.append((end, 1, token, 1))
    for fixture, start, end, units in bookings:
        events.append((start, 4, units, fixture))
        events.append((end, 3, units, fixture))
    events.sort()

    active_limits = []          # heap of unique (capacity, end, seq) tokens
    retired = set()             # tokens whose half-open life has ended
    # The venue default capacity is a permanent restriction: it always
    # participates in the minimum and is never retired.
    heappush(active_limits, (default_capacity, float('inf'), -1))
    load = 0
    bit_minutes = _RangeAddPointQuery()
    bit_flat = _RangeAddPointQuery()
    snapshots = {}              # fixture -> (minutes, flat) at booking start
    relief = {}
    overloads = []
    total_excess = 0

    def effective_capacity():
        while active_limits and active_limits[0] in retired:
            retired.discard(heappop(active_limits))
        return active_limits[0][0]

    previous_time = None
    cursor = 0
    event_count = len(events)
    while cursor < event_count:
        time = events[cursor][0]
        # 1. Finalize the constant-state segment [previous_time, time).
        if previous_time is not None and time > previous_time:
            capacity = effective_capacity()
            length = time - previous_time
            if load > capacity:
                excess = load - capacity
                total_excess += excess * length
                overloads.append((previous_time, time, capacity, load))
                # Split active unit classes 1..load at the excess.
                bit_minutes.range_add(1, excess, length)
                bit_flat.range_add(excess + 1, load, length * excess)
        # 2. Booking ends: read the closing snapshot first, then remove.
        end_cursor = cursor
        while end_cursor < event_count and events[end_cursor][0] == time:
            _, kind, value, tag = events[end_cursor]
            if kind == 3:
                start_minutes, start_flat = snapshots.pop(tag)
                end_minutes = bit_minutes.point(value)
                end_flat = bit_flat.point(value)
                relief[tag] = ((end_minutes - start_minutes) * value
                               + (end_flat - start_flat))
                load -= value
            end_cursor += 1
        # 3. Starts and limit retirements for the next segment.
        while cursor < event_count and events[cursor][0] == time:
            _, kind, value, tag = events[cursor]
            if kind == 2:                                   # limit starts
                heappush(active_limits, value)
            elif kind == 1:                                 # limit ends
                retired.add(value)
            elif kind == 4:                                 # booking starts
                snapshots[tag] = (bit_minutes.point(value),
                                  bit_flat.point(value))
                load += value
            cursor += 1
        previous_time = time

    merged = []
    for start, end, capacity, venue_load in overloads:
        if (merged and merged[-1][1] == start
                and merged[-1][2] == capacity
                and merged[-1][3] == venue_load):
            merged[-1] = (merged[-1][0], end, capacity, venue_load)
        else:
            merged.append((start, end, capacity, venue_load))
    return merged, relief, total_excess


def audit(venues, limits, bookings):
    """Audit every venue independently and assemble the plan-level report body.

    ``venues`` is an id -> default capacity dict; ``limits`` and ``bookings``
    are already-validated record lists.  Returns
    ``(overloads, booking_relief, excess_unit_minutes)`` with the required
    identifier ordering (venue id, then start minute; fixture id).
    """
    by_venue_limits = {venue: [] for venue in venues}
    by_venue_bookings = {venue: [] for venue in venues}
    for venue, start, end, capacity in limits:
        by_venue_limits[venue].append((start, end, capacity))
    for fixture, venue, start, end, units in bookings:
        by_venue_bookings[venue].append((fixture, start, end, units))

    overloads = []
    booking_relief = {}
    excess_unit_minutes = 0
    for venue in sorted(venues):
        segments, venue_relief, venue_excess = _venue_audit(
            venues[venue], by_venue_limits[venue], by_venue_bookings[venue])
        excess_unit_minutes += venue_excess
        for start, end, capacity, venue_load in segments:
            overloads.append({'venue': venue, 'start': start, 'end': end,
                              'capacity': capacity, 'load': venue_load})
        booking_relief.update(venue_relief)

    relief_rows = [{'fixture': fixture,
                    'relief_unit_minutes': booking_relief[fixture]}
                   for fixture in sorted(booking_relief)]
    return overloads, relief_rows, excess_unit_minutes
