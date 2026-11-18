"""Business tests for the venue capacity audit.

A minute-grid brute force implementation with fully independent logic
cross-checks the sweep-line engine on small instances and randomized cases,
so the expected results do not derive from the implementation under test.
"""
import random
import unittest

from league_venue_audit.audit import audit
from league_venue_audit.payloads import pending_fixtures, validate_schedule

TMAX = 1_000_000_000


def brute_force(venues, limits, bookings, horizon):
    """Independent O(horizon * records) reference for small time spans."""
    overloads = []
    excess_total = 0
    relief = {booking[0]: 0 for booking in bookings}
    for venue in sorted(venues):
        active_limits = [item for item in limits if item[0] == venue]
        active_bookings = [item for item in bookings if item[1] == venue]
        current = None
        for minute in range(horizon):
            caps = [venues[venue]]
            for _, start, end, capacity in active_limits:
                if start <= minute < end:
                    caps.append(capacity)
            capacity = min(caps)
            load = 0
            present = []
            for fixture, _, start, end, units in active_bookings:
                if start <= minute < end:
                    load += units
                    present.append((fixture, units))
            if load > capacity:
                excess = load - capacity
                excess_total += excess
                segment = (minute, minute + 1, capacity, load)
                if current is not None and current[1] == minute \
                        and current[2] == capacity and current[3] == load:
                    current = (current[0], minute + 1, capacity, load)
                    overloads[-1] = {'venue': venue, 'start': current[0],
                                     'end': current[1], 'capacity': capacity,
                                     'load': load}
                else:
                    current = segment
                    overloads.append({'venue': venue, 'start': minute,
                                      'end': minute + 1, 'capacity': capacity,
                                      'load': load})
                for fixture, units in present:
                    relief[fixture] += min(units, excess)
            else:
                current = None
    relief_rows = [{'fixture': fixture, 'relief_unit_minutes': relief[fixture]}
                   for fixture in sorted(relief)]
    return overloads, relief_rows, excess_total


def schedule(venues, limits, bookings):
    return {'venues': [{'id': vid, 'capacity': cap}
                       for vid, cap in venues.items()],
            'limits': [{'venue': v, 'start': s, 'end': e, 'capacity': c}
                       for v, s, e, c in limits],
            'bookings': [{'fixture': f, 'venue': v, 'start': s, 'end': e,
                          'units': u}
                         for f, v, s, e, u in bookings]}


class AcceptanceExample(unittest.TestCase):
    def setUp(self):
        self.venues = {'v1': 3}
        self.limits = [('v1', 3, 7, 2), ('v1', 5, 9, 1)]
        self.bookings = [('m2', 'v1', 0, 8, 2), ('m3', 'v1', 2, 6, 2)]

    def test_exact_acceptance_values(self):
        overloads, relief, excess = audit(
            self.venues, self.limits, self.bookings)
        self.assertEqual(excess, 10)
        self.assertEqual(relief, [
            {'fixture': 'm2', 'relief_unit_minutes': 9},
            {'fixture': 'm3', 'relief_unit_minutes': 7},
        ])
        self.assertEqual(overloads, [
            {'venue': 'v1', 'start': 2, 'end': 3, 'capacity': 3, 'load': 4},
            {'venue': 'v1', 'start': 3, 'end': 5, 'capacity': 2, 'load': 4},
            {'venue': 'v1', 'start': 5, 'end': 6, 'capacity': 1, 'load': 4},
            {'venue': 'v1', 'start': 6, 'end': 8, 'capacity': 1, 'load': 2},
        ])

    def test_matches_brute_force(self):
        expected = brute_force(self.venues, self.limits, self.bookings, 10)
        self.assertEqual(audit(self.venues, self.limits, self.bookings),
                         expected)


class BruteForceCrossCheck(unittest.TestCase):
    def test_random_instances(self):
        rng = random.Random(2026)
        for _ in range(300):
            venue_count = rng.randint(1, 4)
            venues = {f'v{i}': rng.randint(0, 6)
                      for i in range(venue_count)}
            limits = []
            for _ in range(rng.randint(0, 8)):
                venue = rng.choice(list(venues))
                start = rng.randint(0, 18)
                end = rng.randint(start + 1, 20)
                limits.append((venue, start, end, rng.randint(0, 6)))
            bookings = []
            for index in range(rng.randint(0, 10)):
                venue = rng.choice(list(venues))
                start = rng.randint(0, 18)
                end = rng.randint(start + 1, 20)
                bookings.append((f'f{index}', venue, start, end,
                                 rng.randint(1, 5)))
            got = audit(venues, limits, bookings)
            self.assertEqual(got, brute_force(venues, limits, bookings, 20))

    def test_adjacent_bookings_no_spurious_overlap(self):
        venues = {'v': 1}
        bookings = [('a', 'v', 0, 5, 1), ('b', 'v', 5, 10, 1)]
        overloads, relief, excess = audit(venues, [], bookings)
        self.assertEqual(overloads, [])
        self.assertEqual(excess, 0)
        self.assertEqual(relief, [
            {'fixture': 'a', 'relief_unit_minutes': 0},
            {'fixture': 'b', 'relief_unit_minutes': 0},
        ])

    def test_zero_capacity_closes_venue(self):
        venues = {'v': 5}
        limits = [('v', 2, 8, 0)]
        bookings = [('a', 'v', 0, 10, 1)]
        overloads, relief, excess = audit(venues, limits, bookings)
        self.assertEqual(overloads, [
            {'venue': 'v', 'start': 2, 'end': 8, 'capacity': 0, 'load': 1}])
        self.assertEqual(excess, 6)
        self.assertEqual(relief,
                         [{'fixture': 'a', 'relief_unit_minutes': 6}])

    def test_equal_capacity_and_load_merge_across_endpoint(self):
        # Two limits meet at minute 5 but both resolve to capacity 2 while the
        # load stays 3: the source changes yet capacity/load do not, so the
        # overloaded region must be one merged segment.
        venues = {'v': 2}
        limits = [('v', 0, 5, 1), ('v', 5, 10, 1)]
        bookings = [('a', 'v', 0, 10, 3)]
        overloads, relief, excess = audit(venues, limits, bookings)
        self.assertEqual(overloads, [
            {'venue': 'v', 'start': 0, 'end': 10, 'capacity': 1, 'load': 3}])
        self.assertEqual(excess, 20)
        self.assertEqual(relief,
                         [{'fixture': 'a', 'relief_unit_minutes': 20}])

    def test_source_only_change_without_capacity_change_not_split(self):
        # A narrower limit (capacity 2) nested inside a wider one changes the
        # active limit set at both endpoints without changing the effective
        # minimum, so no extra segments may appear.
        venues = {'v': 2}
        limits = [('v', 0, 10, 2), ('v', 3, 7, 2)]
        bookings = [('a', 'v', 0, 10, 3)]
        overloads, _relief, excess = audit(venues, limits, bookings)
        self.assertEqual(overloads, [
            {'venue': 'v', 'start': 0, 'end': 10, 'capacity': 2, 'load': 3}])
        self.assertEqual(excess, 10)

    def test_input_permutation_does_not_change_output(self):
        venues = {'v1': 3, 'v2': 1}
        limits = [('v1', 1, 6, 2), ('v2', 0, 4, 0), ('v1', 4, 9, 1)]
        bookings = [('a', 'v1', 0, 8, 2), ('b', 'v1', 2, 6, 3),
                    ('c', 'v2', 1, 5, 1), ('d', 'v2', 6, 8, 1)]
        first = audit(venues, limits, bookings)
        shuffled_limits = list(limits)
        shuffled_bookings = list(bookings)
        random.Random(3).shuffle(shuffled_limits)
        random.Random(4).shuffle(shuffled_bookings)
        second = audit(venues, shuffled_limits, shuffled_bookings)
        self.assertEqual(first, second)
        self.assertEqual(first, brute_force(venues, limits, bookings, 10))

    def test_identical_overlapping_limits_take_minimum(self):
        venues = {'v': 4}
        limits = [('v', 0, 5, 2), ('v', 0, 5, 2), ('v', 0, 5, 3)]
        bookings = [('a', 'v', 0, 5, 3)]
        overloads, _relief, excess = audit(venues, limits, bookings)
        self.assertEqual(overloads, [
            {'venue': 'v', 'start': 0, 'end': 5, 'capacity': 2, 'load': 3}])
        self.assertEqual(excess, 5)

    def test_booking_larger_than_capacity_relief_caps_at_excess(self):
        venues = {'v': 2}
        bookings = [('a', 'v', 0, 4, 5)]
        overloads, relief, excess = audit(venues, [], bookings)
        self.assertEqual(excess, 12)
        self.assertEqual(relief,
                         [{'fixture': 'a', 'relief_unit_minutes': 12}])
        self.assertEqual(overloads, [
            {'venue': 'v', 'start': 0, 'end': 4, 'capacity': 2, 'load': 5}])

    def test_multiple_venues_sorted_independently(self):
        venues = {'vz': 1, 'va': 1}
        bookings = [('z', 'vz', 0, 2, 2), ('a', 'va', 0, 2, 2)]
        overloads, relief, excess = audit(venues, [], bookings)
        self.assertEqual([row['venue'] for row in overloads], ['va', 'vz'])
        self.assertEqual(relief, [
            {'fixture': 'a', 'relief_unit_minutes': 2},
            {'fixture': 'z', 'relief_unit_minutes': 2},
        ])
        self.assertEqual(excess, 4)

    def test_empty_plan(self):
        self.assertEqual(audit({}, [], []), ([], [], 0))
        self.assertEqual(audit({'v': 3}, [], []), ([], [], 0))


class Validation(unittest.TestCase):
    def setUp(self):
        self.fixtures = {'m1': ('A', 'B'), 'm2': ('A', 'C'),
                         'm3': ('B', 'C')}
        self.completed = {'m1'}

    def parse(self, payload):
        return validate_schedule(payload, set(self.fixtures), self.completed)

    def test_valid_empty_arrays(self):
        venues, limits, bookings = self.parse(
            schedule({}, [], []))
        self.assertEqual((venues, limits, bookings), ({}, [], []))

    def test_boolean_capacity_minutes_and_units_rejected(self):
        for field, value in (('capacity', True), ('start', False),
                             ('end', True), ('units', False)):
            payload = schedule({'v': 3}, [], [])
            if field in ('capacity',):
                payload['venues'][0][field] = value
            elif field == 'units':
                payload['bookings'].append(
                    {'fixture': 'm2', 'venue': 'v', 'start': 0,
                     'end': 1, 'units': value})
            else:
                payload['limits'].append(
                    {'venue': 'v', 'start': 0, 'end': 1, 'capacity': 1})
                payload['limits'][0][field] = value
            with self.assertRaises(ValueError):
                self.parse(payload)

    def test_float_values_rejected_even_when_integral(self):
        # The strict integer rule rejects bools and every float, including
        # values that happen to be integral such as 1.0.
        payload = schedule({'v': 1.5}, [], [])
        with self.assertRaises(ValueError):
            self.parse(payload)
        payload = schedule({'v': 1}, [], [])
        payload['limits'].append(
            {'venue': 'v', 'start': 0.0, 'end': 1, 'capacity': 1})
        with self.assertRaises(ValueError):
            self.parse(payload)
        payload = schedule({'v': 1}, [], [])
        payload['bookings'].append(
            {'fixture': 'm2', 'venue': 'v', 'start': 0, 'end': 1,
             'units': 1.0})
        with self.assertRaises(ValueError):
            self.parse(payload)

    def test_unknown_venue_reference_rejected(self):
        payload = {'venues': [], 'limits': [
            {'venue': 'ghost', 'start': 0, 'end': 1, 'capacity': 1}],
            'bookings': []}
        with self.assertRaises(ValueError):
            self.parse(payload)

    def test_booking_unknown_or_completed_fixture_rejected(self):
        base = schedule({'v': 2}, [], [])
        base['bookings'] = [
            {'fixture': 'missing', 'venue': 'v', 'start': 0, 'end': 1,
             'units': 1}]
        with self.assertRaises(ValueError):
            self.parse(base)
        base['bookings'][0]['fixture'] = 'm1'
        with self.assertRaises(ValueError):
            self.parse(base)

    def test_duplicate_fixture_booking_and_venue_rejected(self):
        payload = schedule({'v': 2}, [], [
            ('m2', 'v', 0, 1, 1), ('m2', 'v', 2, 3, 1)])
        with self.assertRaises(ValueError):
            self.parse(payload)
        payload = {'venues': [{'id': 'v', 'capacity': 1},
                              {'id': 'v', 'capacity': 2}],
                   'limits': [], 'bookings': []}
        with self.assertRaises(ValueError):
            self.parse(payload)

    def test_interval_and_bound_validation(self):
        for bad_limits, bad_bookings in (
                ([('v', 5, 5, 1)], []),
                ([('v', -1, 2, 1)], []),
                ([], [('m2', 'v', 3, 2, 1)]),
        ):
            with self.assertRaises(ValueError):
                self.parse(schedule({'v': 2}, bad_limits, bad_bookings))
        for capacity in (-1, 1001):
            with self.assertRaises(ValueError):
                self.parse(schedule({'v': capacity}, [], []))
        with self.assertRaises(ValueError):
            self.parse(schedule({'v': 2}, [], [('m2', 'v', 0, 1, 0)]))
        with self.assertRaises(ValueError):
            self.parse(schedule({'v': 2}, [], [('m2', 'v', 0, 1, 1001)]))

    def test_unknown_top_level_and_record_fields_rejected(self):
        payload = schedule({'v': 1}, [], [])
        payload['extra'] = []
        with self.assertRaises(ValueError):
            self.parse(payload)
        payload = schedule({'v': 1}, [], [])
        payload['venues'][0]['note'] = 'x'
        with self.assertRaises(ValueError):
            self.parse(payload)

    def test_pending_fixtures_listed(self):
        _venues, _limits, bookings = self.parse(schedule(
            {'v': 1}, [], [('m2', 'v', 0, 1, 1)]))
        self.assertEqual(
            pending_fixtures(set(self.fixtures), self.completed, bookings),
            ['m3'])

    def test_repeated_pair_pending_vs_completed_distinguished(self):
        # Two fixtures between the same teams: one completed, one pending.
        fixtures = {'g1': ('A', 'B'), 'g2': ('A', 'B')}
        payload = schedule({'v': 1}, [], [('g2', 'v', 0, 1, 1)])
        _v, _l, bookings = validate_schedule(payload, fixtures, {'g1'})
        self.assertEqual(
            pending_fixtures(fixtures, {'g1'}, bookings), [])
        with self.assertRaises(ValueError):
            validate_schedule(
                schedule({'v': 1}, [], [('g1', 'v', 0, 1, 1)]),
                fixtures, {'g1'})


class ScaleAndRange(unittest.TestCase):
    def test_span_near_time_maximum(self):
        venues = {'v': 1}
        bookings = [('a', 'v', 0, TMAX, 1)]
        overloads, relief, excess = audit(venues, [], bookings)
        self.assertEqual(excess, 0)
        self.assertEqual(relief, [
            {'fixture': 'a', 'relief_unit_minutes': 0}])
        limits = [('v', 0, TMAX, 0)]
        overloads, relief, excess = audit(venues, limits, bookings)
        self.assertEqual(excess, TMAX)
        self.assertEqual(overloads, [
            {'venue': 'v', 'start': 0, 'end': TMAX,
             'capacity': 0, 'load': 1}])
        self.assertEqual(relief, [
            {'fixture': 'a', 'relief_unit_minutes': TMAX}])
        # A restriction ending exactly at the legal maximum is accepted.
        edge_limits = [('v', TMAX - 1, TMAX, 0)]
        overloads, _relief, excess = audit(venues, edge_limits, bookings)
        self.assertEqual(excess, 1)
        self.assertEqual(overloads, [
            {'venue': 'v', 'start': TMAX - 1, 'end': TMAX,
             'capacity': 0, 'load': 1}])

    def test_large_overlapping_plan_matches_closed_form(self):
        # 20 000 unit bookings over one common span, default capacity 1000:
        # load is constant and every booking contributes the same relief.
        count = 20_000
        span = 60
        venues = {'v': 1000}
        bookings = [(f'f{i:05d}', 'v', 10, 10 + span, 1)
                    for i in range(count)]
        overloads, relief, excess = audit(venues, [], bookings)
        self.assertEqual(len(overloads), 1)
        self.assertEqual(overloads[0], {
            'venue': 'v', 'start': 10, 'end': 70,
            'capacity': 1000, 'load': count})
        expected_excess = (count - 1000) * span
        self.assertEqual(excess, expected_excess)
        self.assertEqual(len(relief), count)
        # Removing one single-unit booking cuts the excess by one unit on
        # every overloaded minute of the shared span.
        self.assertTrue(all(row['relief_unit_minutes'] == span
                            for row in relief))
        self.assertEqual(relief[0]['fixture'], 'f00000')
        self.assertEqual(relief[-1]['fixture'], 'f19999')

    def test_many_staggered_bookings_runtime_bounded(self):
        # Each booking [i, i+3) overlaps only its neighbours; outputs stay O(N).
        count = 20_000
        venues = {'v': 2}
        bookings = [(f'f{i:05d}', 'v', i, i + 3, 2)
                    for i in range(count)]
        overloads, relief, excess = audit(venues, [], bookings)
        # Minute loads: 2 at the two edge minutes, 4 next to them, 6 on the
        # interior -> excess 0, 2, 4, 2, 0 across the five boundary regions,
        # which sums to the closed form 4*count - 4 (verified per small N).
        expected_excess = 4 * count - 4
        self.assertEqual(excess, expected_excess)
        self.assertEqual(len(overloads), 3)
        self.assertEqual(overloads[1]['load'], 6)
        self.assertEqual(len(relief), count)
        self.assertTrue(all(0 <= row['relief_unit_minutes'] <= 6
                            for row in relief))


if __name__ == '__main__':
    unittest.main()
