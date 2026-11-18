"""Cross-fixture ticket clearing engine.

Input model (already validated upstream)
----------------------------------------
* ``fixtures`` is the set of fixture ids from the source snapshot.
* ``sales`` maps sale id -> ``(day, discount_cents, lines)`` where ``lines``
  maps line id -> ``(fixture, quantity, list_price_cents)``.
* ``refunds`` lists ``(refund_id, day, sale_id, line_id, first, stop)`` with
  the half-open ticket-number range ``[first, stop)``.

Compact per-ticket representation
---------------------------------
A line's net is distributed over its tickets as a two-step function: with
``(base, extra) = divmod(net, quantity)``, tickets ``1..extra`` carry
``base + 1`` cents and tickets ``extra+1..quantity`` carry ``base``.  The
triple ``(quantity, base, extra)`` is derived once from the validated sale
line, before any refund is processed, and is never mutated afterwards;
refunds only read it.  A refund's amount is therefore anchored to the
original sale alone and cannot be shifted by the presence, absence or
ordering of other refunds.  A range sum is the O(1) closed form
``range_sum`` below, so a line of one million tickets is never expanded
per ticket, in computation or in the report.

Cross-period duplicate refunds
------------------------------
Every refund range of the complete snapshot is grouped by ``(sale, line)``
and checked for overlap before any report is produced; the closing window
only selects which validated records become entries of this period, it
never narrows the validation set.  Because the ticketing file is the
complete sales+refunds snapshot, a ticket refunded on any earlier business
day and again inside the window always appears twice in the file and is
rejected; adjacent ranges that merely touch (``stop == first``) stay legal.
"""

KIND_RANK = {'sale': 0, 'refund': 1}


def allocate_discount(discount, pres):
    """Split an order discount across its lines, exact to the cent.

    ``pres`` maps line id -> pre-discount amount; returns line id ->
    allocated discount.  Each line first receives the integer part of its
    exact proportional share ``discount * pre / total``; the remaining
    cents go one per line to the largest remainders, ties broken by
    ascending line id.  The remainder is always smaller than the line
    count, so no line ever receives more than one extra cent.  A zero
    pre-discount total allocates zero to every line.
    """
    total = sum(pres.values())
    shares = {line_id: 0 for line_id in pres}
    if total == 0 or discount == 0:
        return shares
    remainders = []
    for line_id, pre in pres.items():
        exact = discount * pre
        shares[line_id] = exact // total
        remainders.append((-(exact % total), line_id))
    remainders.sort()
    remaining = discount - sum(shares.values())
    for index in range(remaining):
        shares[remainders[index][1]] += 1
    return shares


def range_sum(base, extra, first, stop):
    """Sum of per-ticket nets over the half-open range [first, stop).

    Tickets ``1..extra`` carry ``base + 1`` cents, all later tickets carry
    ``base``; the sum is evaluated in O(1) without expanding tickets.
    """
    count = stop - first
    bonus = max(0, min(stop - 1, extra) - first + 1)
    return count * base + bonus


def close(fixtures, sales, refunds, from_day, to_day):
    """Produce ``(fixture_rows, entries)`` for the window [from_day, to_day).

    Every source fixture gets one row, all-zero when it has no business.
    Entries cover in-window sale lines and in-window refunds only, but the
    duplicate-refund check spans the complete snapshot.
    """
    # Per-line sale-time facts; derived once and never mutated by refunds.
    lines = {}
    for sale_id, (day, discount, sale_lines) in sales.items():
        pres = {line_id: quantity * price
                for line_id, (_fixture, quantity, price)
                in sale_lines.items()}
        shares = allocate_discount(discount, pres)
        for line_id, (fixture_id, quantity, _price) in sale_lines.items():
            net = pres[line_id] - shares[line_id]
            base, extra = divmod(net, quantity)
            lines[(sale_id, line_id)] = (day, fixture_id, net, base, extra)

    # Refund amounts anchored to the sale-time steps, plus the global
    # duplicate-refund check over the complete input (any business day).
    amounts = {}
    spans = {}
    for refund_id, _day, sale_id, line_id, first, stop in refunds:
        key = (sale_id, line_id)
        _day0, _fixture, _net, base, extra = lines[key]
        amounts[refund_id] = range_sum(base, extra, first, stop)
        spans.setdefault(key, []).append((first, stop))
    for sale_id, line_id in spans:
        ordered = sorted(spans[(sale_id, line_id)])
        for previous, current in zip(ordered, ordered[1:]):
            if current[0] < previous[1]:
                raise ValueError(
                    'ticket refunded more than once: sale %s line %s '
                    'ranges %s and %s overlap'
                    % (sale_id, line_id, previous, current))

    opening = {fixture_id: 0 for fixture_id in fixtures}
    window_sales = {fixture_id: 0 for fixture_id in fixtures}
    window_refunds = {fixture_id: 0 for fixture_id in fixtures}
    entries = []
    for sale_id, (day, _discount, sale_lines) in sales.items():
        for line_id, (fixture_id, _quantity, _price) in sale_lines.items():
            net = lines[(sale_id, line_id)][2]
            if day < from_day:
                opening[fixture_id] += net
            elif day < to_day:
                window_sales[fixture_id] += net
                entries.append({'kind': 'sale', 'day': day,
                                'fixture': fixture_id, 'sale': sale_id,
                                'line': line_id, 'amount_cents': net})
    for refund_id, day, sale_id, line_id, first, stop in refunds:
        fixture_id = lines[(sale_id, line_id)][1]
        amount = amounts[refund_id]
        if day < from_day:
            opening[fixture_id] -= amount
        elif day < to_day:
            window_refunds[fixture_id] += amount
            entries.append({'kind': 'refund', 'day': day,
                            'fixture': fixture_id, 'sale': sale_id,
                            'line': line_id, 'refund': refund_id,
                            'first': first, 'stop': stop,
                            'amount_cents': amount})
    entries.sort(key=lambda entry: (
        entry['day'], KIND_RANK[entry['kind']], entry['sale'],
        entry['line'], entry.get('refund', '')))
    rows = [{'fixture': fixture_id,
             'opening_cents': opening[fixture_id],
             'sales_cents': window_sales[fixture_id],
             'refunds_cents': window_refunds[fixture_id],
             'closing_cents': opening[fixture_id] + window_sales[fixture_id]
                              - window_refunds[fixture_id]}
            for fixture_id in sorted(fixtures)]
    return rows, entries
