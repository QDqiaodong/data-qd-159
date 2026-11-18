"""Business tests for league-ticket-close.

An independent brute-force reference re-derives every expected value with
fractions.Fraction for the discount shares and fully expanded per-ticket
lists, so the expected results do not derive from the engine under test.
"""
import random
import unittest
from fractions import Fraction

from league_ticket_close.close import allocate_discount, close, range_sum
from league_ticket_close.payloads import validate_ticketing

FIXTURES = {'m1', 'm2', 'm3'}


def sale(sale_id, day, discount, lines):
    return {'id': sale_id, 'day': day, 'discount_cents': discount,
            'lines': [{'id': line_id, 'fixture': fixture,
                       'quantity': quantity, 'list_price_cents': price}
                      for line_id, fixture, quantity, price in lines]}


def refund(refund_id, day, sale_id, line_id, first, stop):
    return {'id': refund_id, 'day': day, 'sale': sale_id, 'line': line_id,
            'first': first, 'stop': stop}


def run_close(payload, left, right, fixtures=FIXTURES):
    sales, refunds = validate_ticketing(payload, set(fixtures))
    return close(set(fixtures), sales, refunds, left, right)


def brute_close(payload, left, right, fixtures=FIXTURES):
    """Independent reference: Fraction shares + expanded per-ticket lists."""
    sales, refunds = validate_ticketing(payload, set(fixtures))
    tickets = {}
    info = {}
    for sale_id, (day, discount, lines) in sales.items():
        total = sum(quantity * price
                    for _fixture, quantity, price in lines.values())
        shares = {line_id: 0 for line_id in lines}
        if total:
            exact = {line_id: Fraction(discount * quantity * price, total)
                     for line_id, (_f, quantity, price) in lines.items()}
            floors = {line_id: share.numerator // share.denominator
                      for line_id, share in exact.items()}
            remaining = discount - sum(floors.values())
            ranked = sorted(lines, key=lambda line_id: (
                -(exact[line_id] - floors[line_id]), line_id))
            shares = dict(floors)
            for line_id in ranked[:remaining]:
                shares[line_id] += 1
        for line_id, (fixture, quantity, price) in lines.items():
            net = quantity * price - shares[line_id]
            base, extra = divmod(net, quantity)
            tickets[(sale_id, line_id)] = (
                [base + 1] * extra + [base] * (quantity - extra))
            info[(sale_id, line_id)] = (day, fixture, net)
    claimed = {}
    amounts = {}
    for refund_id, day, sale_id, line_id, first, stop in refunds:
        key = (sale_id, line_id)
        span = set(range(first, stop))
        if claimed.setdefault(key, set()) & span:
            raise ValueError('ticket refunded more than once')
        claimed[key] |= span
        amounts[refund_id] = sum(tickets[key][number - 1]
                                 for number in range(first, stop))
    opening = {fixture: 0 for fixture in fixtures}
    window_sales = {fixture: 0 for fixture in fixtures}
    window_refunds = {fixture: 0 for fixture in fixtures}
    entries = []
    for (sale_id, line_id), (day, fixture, net) in info.items():
        if day < left:
            opening[fixture] += net
        elif day < right:
            window_sales[fixture] += net
            entries.append({'kind': 'sale', 'day': day, 'fixture': fixture,
                            'sale': sale_id, 'line': line_id,
                            'amount_cents': net})
    for refund_id, day, sale_id, line_id, first, stop in refunds:
        fixture = info[(sale_id, line_id)][1]
        amount = amounts[refund_id]
        if day < left:
            opening[fixture] -= amount
        elif day < right:
            window_refunds[fixture] += amount
            entries.append({'kind': 'refund', 'day': day, 'fixture': fixture,
                            'sale': sale_id, 'line': line_id,
                            'refund': refund_id, 'first': first,
                            'stop': stop, 'amount_cents': amount})
    rank = {'sale': 0, 'refund': 1}
    entries.sort(key=lambda row: (
        row['day'], rank[row['kind']], row['sale'], row['line'],
        row.get('refund', '')))
    rows = [{'fixture': fixture, 'opening_cents': opening[fixture],
             'sales_cents': window_sales[fixture],
             'refunds_cents': window_refunds[fixture],
             'closing_cents': opening[fixture] + window_sales[fixture]
                              - window_refunds[fixture]}
            for fixture in sorted(fixtures)]
    return rows, entries


def ranges_of(numbers):
    """Group sorted ticket numbers into maximal half-open ranges."""
    start = previous = None
    for number in numbers:
        if previous is not None and number == previous + 1:
            previous = number
            continue
        if previous is not None:
            yield start, previous + 1
        start = previous = number
    if previous is not None:
        yield start, previous + 1


def random_payload(rng):
    fixtures = sorted(FIXTURES)
    sales = []
    refunds = []
    refund_seq = 0
    for index in range(rng.randint(1, 4)):
        sale_id = 's%d' % index
        day = rng.randint(0, 12)
        lines = [('l%d' % line_index, rng.choice(fixtures),
                  rng.randint(1, 8), rng.randint(0, 20))
                 for line_index in range(rng.randint(1, 3))]
        total = sum(quantity * price
                    for _lid, _f, quantity, price in lines)
        discount = rng.randint(0, total) if total else 0
        sales.append(sale(sale_id, day, discount, lines))
        for line_id, _fixture, quantity, _price in lines:
            numbers = [number for number in range(1, quantity + 1)
                       if rng.random() < 0.4]
            for first, stop in ranges_of(numbers):
                refunds.append(refund('r%d' % refund_seq,
                                      rng.randint(day, 13),
                                      sale_id, line_id, first, stop))
                refund_seq += 1
    return {'sales': sales, 'refunds': refunds}


class AcceptanceExample(unittest.TestCase):
    def setUp(self):
        self.payload = {
            'sales': [sale('s1', 5, 3, [('lineA', 'm1', 3, 4),
                                        ('lineB', 'm2', 2, 4)])],
            'refunds': [refund('r1', 10, 's1', 'lineA', 2, 4),
                        refund('r2', 10, 's1', 'lineB', 1, 2)]}

    def test_exact_acceptance_values(self):
        # Discount 3 over pre amounts 12/8: shares 1.8/1.2 -> 2/1, so lineA
        # nets 10 over tickets 4,3,3 and lineB nets 7 over tickets 4,3.
        rows, entries = run_close(self.payload, 10, 20)
        self.assertEqual(rows, [
            {'fixture': 'm1', 'opening_cents': 10, 'sales_cents': 0,
             'refunds_cents': 6, 'closing_cents': 4},
            {'fixture': 'm2', 'opening_cents': 7, 'sales_cents': 0,
             'refunds_cents': 4, 'closing_cents': 3},
            {'fixture': 'm3', 'opening_cents': 0, 'sales_cents': 0,
             'refunds_cents': 0, 'closing_cents': 0}])
        self.assertEqual(entries, [
            {'kind': 'refund', 'day': 10, 'fixture': 'm1', 'sale': 's1',
             'line': 'lineA', 'refund': 'r1', 'first': 2, 'stop': 4,
             'amount_cents': 6},
            {'kind': 'refund', 'day': 10, 'fixture': 'm2', 'sale': 's1',
             'line': 'lineB', 'refund': 'r2', 'first': 1, 'stop': 2,
             'amount_cents': 4}])

    def test_pre_window_duplicate_refund_rejected_even_with_new_id(self):
        # r3 refunds lineA ticket 2 before the window; r1 refunds tickets
        # 2..3 inside the window.  The window must not hide the overlap.
        payload = {'sales': list(self.payload['sales']),
                   'refunds': self.payload['refunds'] + [
                       refund('r3', 7, 's1', 'lineA', 2, 3)]}
        with self.assertRaises(ValueError):
            run_close(payload, 10, 20)

    def test_matches_brute_force(self):
        self.assertEqual(run_close(self.payload, 10, 20),
                         brute_close(self.payload, 10, 20))


class DiscountAllocation(unittest.TestCase):
    def test_remainder_tie_broken_by_line_id(self):
        # Equal pre amounts give equal remainders: the lower line id wins
        # the single remaining cent.
        self.assertEqual(allocate_discount(3, {'b': 10, 'a': 10}),
                         {'a': 2, 'b': 1})

    def test_remainder_descending_then_line_id(self):
        # exact shares 1.75 / 1.25 / 2.0 -> floors 1/1/2, one cent left,
        # and x holds the largest remainder.
        self.assertEqual(allocate_discount(5, {'x': 7, 'y': 5, 'z': 8}),
                         {'x': 2, 'y': 1, 'z': 2})

    def test_zero_total_allocates_zero(self):
        self.assertEqual(allocate_discount(0, {'a': 0, 'b': 0}),
                         {'a': 0, 'b': 0})

    def test_full_discount_zeroes_every_net(self):
        self.assertEqual(allocate_discount(30, {'a': 10, 'b': 20}),
                         {'a': 10, 'b': 20})

    def test_random_allocation_properties(self):
        rng = random.Random(99)
        for _ in range(500):
            pres = {'l%d' % i: rng.randint(0, 50)
                    for i in range(rng.randint(1, 6))}
            total = sum(pres.values())
            discount = rng.randint(0, total) if total else 0
            shares = allocate_discount(discount, pres)
            self.assertEqual(sum(shares.values()), discount)
            for line_id, pre in pres.items():
                self.assertLessEqual(shares[line_id], pre)
                if total:
                    exact = Fraction(discount * pre, total)
                    floor = exact.numerator // exact.denominator
                    self.assertIn(shares[line_id], (floor, floor + 1))

    def test_matches_fraction_reference(self):
        rng = random.Random(100)
        for _ in range(300):
            pres = {'l%d' % i: rng.randint(0, 50)
                    for i in range(rng.randint(1, 6))}
            total = sum(pres.values())
            discount = rng.randint(0, total) if total else 0
            expected = {line_id: 0 for line_id in pres}
            if total:
                exact = {line_id: Fraction(discount * pre, total)
                         for line_id, pre in pres.items()}
                floors = {line_id: int(share)
                          for line_id, share in exact.items()}
                remaining = discount - sum(floors.values())
                ranked = sorted(pres, key=lambda line_id: (
                    -(exact[line_id] - floors[line_id]), line_id))
                expected = dict(floors)
                for line_id in ranked[:remaining]:
                    expected[line_id] += 1
            self.assertEqual(allocate_discount(discount, pres), expected)


class PerTicketNet(unittest.TestCase):
    def test_range_sum_matches_expansion(self):
        # net 10 over 3 tickets -> 4, 3, 3
        base, extra = divmod(10, 3)
        expanded = [4, 3, 3]
        for first in range(1, 5):
            for stop in range(first + 1, 5):
                self.assertEqual(
                    range_sum(base, extra, first, stop),
                    sum(expanded[first - 1:stop - 1]))

    def test_million_ticket_line_closed_form(self):
        quantity = 1_000_000
        net = 10 ** 12 - 7
        base, extra = divmod(net, quantity)
        self.assertEqual((base, extra), (999_999, 999_993))
        # The full-range refund equals the whole net; boundary ranges match
        # the step structure, all without per-ticket expansion.
        self.assertEqual(range_sum(base, extra, 1, quantity + 1), net)
        self.assertEqual(range_sum(base, extra, 1, 999_994),
                         999_993 * 1_000_000)
        self.assertEqual(range_sum(base, extra, 999_994, 1_000_001),
                         7 * 999_999)

    def test_million_ticket_line_end_to_end_not_expanded(self):
        payload = {'sales': [sale('s', 1, 7,
                                  [('big', 'm1', 1_000_000, 1_000_000)])],
                   'refunds': [refund('r', 2, 's', 'big', 1, 1_000_001)]}
        rows, entries = run_close(payload, 1, 3)
        net = 10 ** 12 - 7
        self.assertEqual(rows[0], {'fixture': 'm1', 'opening_cents': 0,
                                   'sales_cents': net, 'refunds_cents': net,
                                   'closing_cents': 0})
        # One sale entry and one refund entry: the report never expands
        # per-ticket rows.
        self.assertEqual(len(entries), 2)
        self.assertEqual(entries[0]['amount_cents'], net)
        self.assertEqual(entries[1]['amount_cents'], net)


class RefundSemantics(unittest.TestCase):
    def test_adjacent_ranges_fully_refund_line_conservation(self):
        payload = {'sales': [sale('s', 1, 0, [('a', 'm1', 5, 10)])],
                   'refunds': [refund('r1', 2, 's', 'a', 1, 3),
                               refund('r2', 3, 's', 'a', 3, 6)]}
        rows, entries = run_close(payload, 1, 10)
        row = rows[0]
        self.assertEqual((row['sales_cents'], row['refunds_cents'],
                          row['closing_cents']), (50, 50, 0))
        sold = sum(entry['amount_cents'] for entry in entries
                   if entry['kind'] == 'sale')
        refunded = [entry['amount_cents'] for entry in entries
                    if entry['kind'] == 'refund']
        self.assertEqual(sum(refunded), sold)
        self.assertEqual(refunded, [20, 30])

    def test_free_tickets_cannot_be_refunded_twice(self):
        payload = {'sales': [sale('s', 1, 0, [('a', 'm1', 2, 0)])],
                   'refunds': [refund('r1', 2, 's', 'a', 1, 2),
                               refund('r2', 3, 's', 'a', 1, 2)]}
        with self.assertRaises(ValueError):
            run_close(payload, 1, 10)

    def test_nested_overlap_rejected(self):
        payload = {'sales': [sale('s', 1, 0, [('a', 'm1', 5, 10)])],
                   'refunds': [refund('r1', 2, 's', 'a', 1, 5),
                               refund('r2', 3, 's', 'a', 2, 3)]}
        with self.assertRaises(ValueError):
            run_close(payload, 1, 10)

    def test_zero_net_entries_kept(self):
        # Full discount zeroes the line; the zero-amount sale and refund
        # entries are still reported.
        payload = {'sales': [sale('s', 1, 10, [('a', 'm1', 2, 5)])],
                   'refunds': [refund('r', 2, 's', 'a', 1, 3)]}
        rows, entries = run_close(payload, 1, 10)
        self.assertEqual(rows[0]['sales_cents'], 0)
        self.assertEqual(rows[0]['refunds_cents'], 0)
        self.assertEqual(rows[0]['closing_cents'], 0)
        self.assertEqual(len(entries), 2)
        self.assertTrue(all(entry['amount_cents'] == 0 for entry in entries))

    def test_refund_amount_ignores_other_refunds(self):
        # A second refund of the same line never shifts the first refund's
        # amount: both are valued from the immutable sale-time steps.
        base_payload = {'sales': [sale('s', 1, 2, [('a', 'm1', 3, 4)])],
                        'refunds': [refund('r1', 2, 's', 'a', 2, 4)]}
        extended = {'sales': base_payload['sales'],
                    'refunds': [refund('r0', 3, 's', 'a', 1, 2),
                                refund('r1', 2, 's', 'a', 2, 4)]}
        _rows1, entries1 = run_close(base_payload, 1, 10)
        _rows2, entries2 = run_close(extended, 1, 10)
        alone = [entry['amount_cents'] for entry in entries1
                 if entry['kind'] == 'refund']
        together = {entry['refund']: entry['amount_cents']
                    for entry in entries2 if entry['kind'] == 'refund'}
        self.assertEqual(alone, [6])
        self.assertEqual(together, {'r0': 4, 'r1': 6})


class WindowBoundaries(unittest.TestCase):
    def test_sale_window_left_closed_right_open(self):
        payload = {'sales': [
            sale('before', 3, 0, [('a', 'm1', 1, 10)]),
            sale('at-left', 4, 0, [('a', 'm1', 1, 10)]),
            sale('at-right', 9, 0, [('a', 'm1', 1, 10)]),
            sale('after', 12, 0, [('a', 'm1', 1, 10)])],
            'refunds': []}
        rows, entries = run_close(payload, 4, 9)
        row = rows[0]
        self.assertEqual((row['opening_cents'], row['sales_cents'],
                          row['refunds_cents'], row['closing_cents']),
                         (10, 10, 0, 20))
        self.assertEqual([entry['sale'] for entry in entries], ['at-left'])

    def test_refund_window_left_closed_right_open(self):
        payload = {'sales': [sale('s', 0, 0, [('a', 'm1', 4, 10)])],
                   'refunds': [refund('before', 3, 's', 'a', 1, 2),
                               refund('at-left', 4, 's', 'a', 2, 3),
                               refund('at-right', 9, 's', 'a', 3, 4),
                               refund('after', 10, 's', 'a', 4, 5)]}
        rows, entries = run_close(payload, 4, 9)
        row = rows[0]
        self.assertEqual((row['opening_cents'], row['sales_cents'],
                          row['refunds_cents'], row['closing_cents']),
                         (30, 0, 10, 20))
        self.assertEqual([entry['refund'] for entry in entries], ['at-left'])

    def test_same_day_sale_and_refund(self):
        payload = {'sales': [sale('s', 5, 0, [('a', 'm1', 2, 10)])],
                   'refunds': [refund('r', 5, 's', 'a', 1, 2)]}
        rows, entries = run_close(payload, 5, 6)
        row = rows[0]
        self.assertEqual((row['opening_cents'], row['sales_cents'],
                          row['refunds_cents'], row['closing_cents']),
                         (0, 20, 10, 10))
        self.assertEqual([entry['kind'] for entry in entries],
                         ['sale', 'refund'])

    def test_legal_records_after_window_ignored(self):
        payload = {'sales': [sale('s', 1, 0, [('a', 'm1', 1, 10)]),
                             sale('late', 50, 0, [('a', 'm1', 1, 5)])],
                   'refunds': [refund('late-r', 60, 'late', 'a', 1, 2)]}
        rows, entries = run_close(payload, 2, 10)
        self.assertEqual(rows[0]['opening_cents'], 10)
        self.assertEqual(rows[0]['sales_cents'], 0)
        self.assertEqual(rows[0]['refunds_cents'], 0)
        self.assertEqual(rows[0]['closing_cents'], 10)
        self.assertEqual(entries, [])

    def test_illegal_record_after_window_still_rejected(self):
        # Duplicate refund of the same ticket after the window.
        payload = {'sales': [sale('s', 1, 0, [('a', 'm1', 2, 10)])],
                   'refunds': [refund('r1', 2, 's', 'a', 1, 2),
                               refund('r2', 60, 's', 'a', 1, 2)]}
        with self.assertRaises(ValueError):
            run_close(payload, 3, 10)
        # Unknown sale reference after the window.
        payload = {'sales': [sale('s', 1, 0, [('a', 'm1', 1, 10)])],
                   'refunds': [refund('r1', 60, 'ghost', 'a', 1, 2)]}
        with self.assertRaises(ValueError):
            run_close(payload, 3, 10)
        # Refund day before its sale day, both after the window.
        payload = {'sales': [sale('s', 50, 0, [('a', 'm1', 1, 10)])],
                   'refunds': [refund('r1', 40, 's', 'a', 1, 2)]}
        with self.assertRaises(ValueError):
            run_close(payload, 3, 10)


class EntryOrdering(unittest.TestCase):
    def test_sales_before_refunds_then_ids(self):
        payload = {'sales': [
            sale('s2', 5, 0, [('b', 'm1', 2, 1), ('a', 'm2', 1, 1)]),
            sale('s1', 5, 0, [('z', 'm3', 1, 1)])],
            'refunds': [refund('r2', 5, 's2', 'b', 1, 2),
                        refund('r1', 5, 's2', 'b', 2, 3)]}
        _rows, entries = run_close(payload, 5, 6)
        self.assertEqual(
            [(entry['kind'], entry['sale'], entry['line'],
              entry.get('refund')) for entry in entries],
            [('sale', 's1', 'z', None),
             ('sale', 's2', 'a', None),
             ('sale', 's2', 'b', None),
             ('refund', 's2', 'b', 'r1'),
             ('refund', 's2', 'b', 'r2')])

    def test_day_dominates_kind(self):
        payload = {'sales': [sale('s', 6, 0, [('a', 'm1', 1, 1)]),
                             sale('s0', 1, 0, [('a', 'm1', 1, 1)])],
                   'refunds': [refund('r', 5, 's0', 'a', 1, 2)]}
        _rows, entries = run_close(payload, 1, 10)
        self.assertEqual([(entry['kind'], entry['day']) for entry in entries],
                         [('sale', 1), ('refund', 5), ('sale', 6)])


class BruteForceCrossCheck(unittest.TestCase):
    def test_random_instances(self):
        rng = random.Random(20261118)
        for _ in range(300):
            payload = random_payload(rng)
            got = run_close(payload, 4, 9)
            expected = brute_close(payload, 4, 9)
            self.assertEqual(got, expected)
            rows, entries = got
            for row in rows:
                self.assertEqual(row['closing_cents'],
                                 row['opening_cents'] + row['sales_cents']
                                 - row['refunds_cents'])
                sold = sum(entry['amount_cents'] for entry in entries
                           if entry['kind'] == 'sale'
                           and entry['fixture'] == row['fixture'])
                returned = sum(entry['amount_cents'] for entry in entries
                               if entry['kind'] == 'refund'
                               and entry['fixture'] == row['fixture'])
                self.assertEqual(sold, row['sales_cents'])
                self.assertEqual(returned, row['refunds_cents'])
            self.assertTrue(all(entry['amount_cents'] >= 0
                                for entry in entries))

    def test_input_permutation_stability(self):
        payload = random_payload(random.Random(11))
        base = run_close(payload, 4, 9)
        rng = random.Random(7)
        for _ in range(20):
            shuffled = {'sales': [dict(entry) for entry in payload['sales']],
                        'refunds': list(payload['refunds'])}
            rng.shuffle(shuffled['sales'])
            rng.shuffle(shuffled['refunds'])
            for entry in shuffled['sales']:
                entry['lines'] = list(entry['lines'])
                rng.shuffle(entry['lines'])
            self.assertEqual(run_close(shuffled, 4, 9), base)


class Validation(unittest.TestCase):
    def assert_invalid(self, payload):
        with self.assertRaises(ValueError):
            validate_ticketing(payload, set(FIXTURES))

    def test_valid_empty_arrays(self):
        sales, refunds = validate_ticketing(
            {'sales': [], 'refunds': []}, set(FIXTURES))
        self.assertEqual((sales, refunds), ({}, []))

    def test_boolean_and_float_scalars_rejected(self):
        good_lines = [('a', 'm1', 2, 10)]
        for bad in (True, 1.0):
            self.assert_invalid({'sales': [sale('s', bad, 0, good_lines)],
                                 'refunds': []})
            self.assert_invalid({'sales': [sale('s', 1, bad, good_lines)],
                                 'refunds': []})
            self.assert_invalid({'sales': [
                sale('s', 1, 0, [('a', 'm1', bad, 10)])], 'refunds': []})
            self.assert_invalid({'sales': [
                sale('s', 1, 0, [('a', 'm1', 2, bad)])], 'refunds': []})
            self.assert_invalid({
                'sales': [sale('s', 1, 0, good_lines)],
                'refunds': [refund('r', 1, 's', 'a', bad, 2)]})
            self.assert_invalid({
                'sales': [sale('s', 1, 0, good_lines)],
                'refunds': [refund('r', 1, 's', 'a', 1, bad)]})
            self.assert_invalid({
                'sales': [sale('s', 1, 0, good_lines)],
                'refunds': [refund('r', bad, 's', 'a', 1, 2)]})

    def test_scalar_bounds(self):
        good_lines = [('a', 'm1', 2, 10)]
        for bad_day in (-1, 1_000_000_001):
            self.assert_invalid({'sales': [sale('s', bad_day, 0, good_lines)],
                                 'refunds': []})
        for bad_quantity in (0, 1_000_001):
            self.assert_invalid({'sales': [
                sale('s', 1, 0, [('a', 'm1', bad_quantity, 10)])],
                'refunds': []})
        for bad_price in (-1, 1_000_001):
            self.assert_invalid({'sales': [
                sale('s', 1, 0, [('a', 'm1', 2, bad_price)])],
                'refunds': []})
        # Discount above the order pre-discount total, and negative.
        self.assert_invalid({'sales': [sale('s', 1, 21, good_lines)],
                             'refunds': []})
        self.assert_invalid({'sales': [sale('s', 1, -1, good_lines)],
                             'refunds': []})
        # Boundary: discount equal to the total is legal.
        validate_ticketing({'sales': [sale('s', 1, 20, good_lines)],
                            'refunds': []}, set(FIXTURES))

    def test_refund_range_bounds(self):
        lines = [('a', 'm1', 3, 10)]
        for first, stop in ((0, 2), (2, 2), (3, 2), (1, 5), (4, 5)):
            self.assert_invalid({
                'sales': [sale('s', 1, 0, lines)],
                'refunds': [refund('r', 1, 's', 'a', first, stop)]})
        # Boundary: the whole line [1, quantity+1) is legal.
        validate_ticketing({
            'sales': [sale('s', 1, 0, lines)],
            'refunds': [refund('r', 1, 's', 'a', 1, 4)]}, set(FIXTURES))

    def test_duplicate_ids_rejected(self):
        lines = [('a', 'm1', 2, 10)]
        self.assert_invalid({'sales': [sale('s', 1, 0, lines),
                                       sale('s', 2, 0, lines)],
                             'refunds': []})
        self.assert_invalid({'sales': [sale('s', 1, 0, lines)],
                             'refunds': [refund('r', 1, 's', 'a', 1, 2),
                                         refund('r', 2, 's', 'a', 2, 3)]})
        self.assert_invalid({'sales': [
            sale('s', 1, 0, [('a', 'm1', 1, 10), ('a', 'm2', 1, 10)])],
            'refunds': []})

    def test_line_ids_may_repeat_across_orders(self):
        payload = {'sales': [sale('s1', 1, 0, [('a', 'm1', 1, 10)]),
                             sale('s2', 1, 0, [('a', 'm2', 1, 10)])],
                   'refunds': [refund('r1', 1, 's1', 'a', 1, 2),
                               refund('r2', 1, 's2', 'a', 1, 2)]}
        sales, refunds = validate_ticketing(payload, set(FIXTURES))
        self.assertEqual(len(sales), 2)
        self.assertEqual(len(refunds), 2)

    def test_unknown_references_rejected(self):
        lines = [('a', 'm1', 2, 10)]
        self.assert_invalid({'sales': [sale('s', 1, 0,
                                            [('a', 'ghost', 2, 10)])],
                             'refunds': []})
        self.assert_invalid({'sales': [sale('s', 1, 0, lines)],
                             'refunds': [refund('r', 1, 'ghost', 'a', 1, 2)]})
        self.assert_invalid({'sales': [sale('s', 1, 0, lines)],
                             'refunds': [refund('r', 1, 's', 'ghost', 1, 2)]})

    def test_refund_day_before_sale_day_rejected(self):
        self.assert_invalid({
            'sales': [sale('s', 5, 0, [('a', 'm1', 2, 10)])],
            'refunds': [refund('r', 4, 's', 'a', 1, 2)]})

    def test_empty_lines_rejected(self):
        self.assert_invalid({'sales': [sale('s', 1, 0, [])], 'refunds': []})

    def test_unknown_and_missing_fields_rejected(self):
        payload = {'sales': [], 'refunds': [], 'surprise': []}
        self.assert_invalid(payload)
        payload = {'sales': [sale('s', 1, 0, [('a', 'm1', 1, 10)])],
                   'refunds': []}
        payload['sales'][0]['note'] = 'x'
        self.assert_invalid(payload)
        payload = {'sales': [sale('s', 1, 0, [('a', 'm1', 1, 10)])],
                   'refunds': []}
        del payload['sales'][0]['day']
        self.assert_invalid(payload)
        payload = {'sales': [sale('s', 1, 0, [('a', 'm1', 1, 10)])],
                   'refunds': []}
        payload['sales'][0]['lines'][0]['note'] = 'x'
        self.assert_invalid(payload)
        payload = {'sales': [sale('s', 1, 0, [('a', 'm1', 1, 10)])],
                   'refunds': [refund('r', 1, 's', 'a', 1, 2)]}
        payload['refunds'][0]['note'] = 'x'
        self.assert_invalid(payload)

    def test_record_limits(self):
        many_sales = [sale('s%d' % i, 1, 0, [('a', 'm1', 1, 1)])
                      for i in range(20_001)]
        self.assert_invalid({'sales': many_sales, 'refunds': []})
        many_lines = [('l%d' % i, 'm1', 1, 1) for i in range(20_001)]
        self.assert_invalid({'sales': [sale('s', 1, 0, many_lines)],
                             'refunds': []})
        split = [sale('s%d' % i, 1, 0,
                      [('l%d_%d' % (i, j), 'm1', 1, 1)
                       for j in range(10_001)])
                 for i in range(2)]
        self.assert_invalid({'sales': split, 'refunds': []})
        many_refunds = [refund('r%d' % i, 1, 's', 'a', 1, 2)
                        for i in range(20_001)]
        self.assert_invalid({'sales': [sale('s', 1, 0, [('a', 'm1', 1, 1)])],
                             'refunds': many_refunds})


if __name__ == '__main__':
    unittest.main()
