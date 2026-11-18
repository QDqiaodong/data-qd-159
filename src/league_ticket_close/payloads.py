"""Strict parsing and validation of the ticketing snapshot JSON.

The ticketing file is the complete sales+refunds snapshot with exactly two
top-level arrays::

    {"sales":  [{"id", "day", "discount_cents", "lines"}],
     "refunds":[{"id", "day", "sale", "line", "first", "stop"}]}

Sale ids are unique; ``lines`` is a non-empty array of
``{"id", "fixture", "quantity", "list_price_cents"}`` whose ids are unique
within their own order only.  ``quantity`` is an integer 1..1000000,
``list_price_cents`` 0..1000000 and ``discount_cents`` an integer from 0 up
to the order's pre-discount total.  Refund ids are unique; ``sale``/``line``
reference the original order and line, ``[first, stop)`` is a half-open
ticket-number range with ``1 <= first < stop <= quantity+1``, and a refund
day never precedes its sale day.  Days are integers in 0..1000000000;
amounts, quantities, ticket numbers and days reject booleans and floats.
Sales, total lines across all orders and refunds each hold at most 20000
records.  Validation is global over the complete snapshot: the closing
window never narrows it.
"""
from league_table.common import ident, require, rows

DAY_MAX = 1_000_000_000
QUANTITY_MAX = 1_000_000
PRICE_MAX = 1_000_000
RECORD_LIMIT = 20_000


def _day(value):
    if type(value) is not int or not 0 <= value <= DAY_MAX:
        raise ValueError('day must be an integer in 0..1000000000')
    return value


def _quantity(value):
    if type(value) is not int or not 1 <= value <= QUANTITY_MAX:
        raise ValueError('quantity must be an integer in 1..1000000')
    return value


def _price(value):
    if type(value) is not int or not 0 <= value <= PRICE_MAX:
        raise ValueError('list price must be an integer in 0..1000000')
    return value


def validate_ticketing(data, fixtures):
    """Validate the ticketing payload against the source fixture ids.

    ``fixtures`` is the source fixture id set (pending and completed
    fixtures may both be referenced).  Returns ``(sales, refunds)`` where
    ``sales`` maps sale id -> ``(day, discount_cents, lines)`` with
    ``lines`` mapping line id -> ``(fixture, quantity, list_price_cents)``,
    and ``refunds`` lists ``(refund_id, day, sale_id, line_id, first,
    stop)`` in file order.
    """
    require(data, 'sales refunds')
    raw_sales = rows(data['sales'], RECORD_LIMIT)
    raw_refunds = rows(data['refunds'], RECORD_LIMIT)

    sales = {}
    line_count = 0
    for entry in raw_sales:
        require(entry, 'id day discount_cents lines')
        sale_id = ident(entry['id'])
        if sale_id in sales:
            raise ValueError('duplicate sale id: ' + sale_id)
        day = _day(entry['day'])
        discount = entry['discount_cents']
        if type(discount) is not int or discount < 0:
            raise ValueError('discount must be a non-negative integer')
        raw_lines = rows(entry['lines'], RECORD_LIMIT)
        if not raw_lines:
            raise ValueError('sale lines must be non-empty: ' + sale_id)
        line_count += len(raw_lines)
        if line_count > RECORD_LIMIT:
            raise ValueError('total ticket lines exceed 20000')
        lines = {}
        total = 0
        for raw_line in raw_lines:
            require(raw_line, 'id fixture quantity list_price_cents')
            line_id = ident(raw_line['id'])
            if line_id in lines:
                raise ValueError(
                    'duplicate line id in sale %s: %s' % (sale_id, line_id))
            fixture_id = ident(raw_line['fixture'])
            if fixture_id not in fixtures:
                raise ValueError(
                    'line references unknown fixture: ' + fixture_id)
            quantity = _quantity(raw_line['quantity'])
            price = _price(raw_line['list_price_cents'])
            total += quantity * price
            lines[line_id] = (fixture_id, quantity, price)
        if discount > total:
            raise ValueError(
                'discount exceeds order pre-discount total: ' + sale_id)
        sales[sale_id] = (day, discount, lines)

    refunds = []
    seen_refunds = set()
    for entry in raw_refunds:
        require(entry, 'id day sale line first stop')
        refund_id = ident(entry['id'])
        if refund_id in seen_refunds:
            raise ValueError('duplicate refund id: ' + refund_id)
        seen_refunds.add(refund_id)
        day = _day(entry['day'])
        sale_id = ident(entry['sale'])
        if sale_id not in sales:
            raise ValueError('refund references unknown sale: ' + sale_id)
        sale_day, _discount, lines = sales[sale_id]
        line_id = ident(entry['line'])
        if line_id not in lines:
            raise ValueError(
                'refund references unknown line in sale %s: %s'
                % (sale_id, line_id))
        if day < sale_day:
            raise ValueError('refund day precedes its sale day: ' + refund_id)
        first = entry['first']
        stop = entry['stop']
        quantity = lines[line_id][1]
        if type(first) is not int or type(stop) is not int \
                or not 1 <= first < stop <= quantity + 1:
            raise ValueError(
                'refund range must satisfy 1 <= first < stop <= quantity+1')
        refunds.append((refund_id, day, sale_id, line_id, first, stop))

    return sales, refunds
