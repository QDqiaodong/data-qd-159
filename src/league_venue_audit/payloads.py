"""Strict parsing and validation of the independent schedule JSON.

The schedule file contains exactly three arrays::

    {"venues":  [{"id", "capacity"}],
     "limits":  [{"venue", "start", "end", "capacity"}],
     "bookings":[{"fixture", "venue", "start", "end", "units"}]}

All identifiers reuse the league-table contract; minutes are plain integers
in 0..1_000_000_000 (booleans rejected), capacities 0..1000 and units
1..1000.  Each array holds at most 20 000 records and may be empty.  Every
limit and booking references a known venue; bookings additionally reference a
fixture that exists in the source snapshot and has no formal result, with at
most one booking per fixture.
"""
from league_table.common import ident, require, rows

TMAX = 1_000_000_000
CAPACITY_MAX = 1000
UNITS_MAX = 1000
RECORD_LIMIT = 20_000


def _minute(value):
    if type(value) is not int or not 0 <= value <= TMAX:
        raise ValueError('minute must be an integer in 0..1000000000')
    return value


def _capacity(value):
    if type(value) is not int or not 0 <= value <= CAPACITY_MAX:
        raise ValueError('capacity must be an integer in 0..1000')
    return value


def _units(value):
    if type(value) is not int or not 1 <= value <= UNITS_MAX:
        raise ValueError('units must be an integer in 1..1000')
    return value


def validate_schedule(data, fixtures, completed):
    """Validate the schedule payload against the source snapshot.

    ``fixtures`` is the source fixture id set and ``completed`` the set of
    fixture ids carrying a formal result.  Returns the canonical
    ``(venues, limits, bookings)`` tuples used by the audit engine.
    """
    require(data, 'venues limits bookings')
    raw_venues = rows(data['venues'], RECORD_LIMIT)
    raw_limits = rows(data['limits'], RECORD_LIMIT)
    raw_bookings = rows(data['bookings'], RECORD_LIMIT)

    venues = {}
    for entry in raw_venues:
        require(entry, 'id capacity')
        venue_id = ident(entry['id'])
        if venue_id in venues:
            raise ValueError('duplicate venue id: ' + venue_id)
        venues[venue_id] = _capacity(entry['capacity'])

    limits = []
    for entry in raw_limits:
        require(entry, 'venue start end capacity')
        venue_id = ident(entry['venue'])
        if venue_id not in venues:
            raise ValueError('limit references unknown venue: ' + venue_id)
        start = _minute(entry['start'])
        end = _minute(entry['end'])
        if not start < end:
            raise ValueError('limit interval must satisfy start < end')
        limits.append((venue_id, start, end, _capacity(entry['capacity'])))

    bookings = []
    booked_fixtures = set()
    for entry in raw_bookings:
        require(entry, 'fixture venue start end units')
        fixture_id = ident(entry['fixture'])
        if fixture_id not in fixtures:
            raise ValueError('booking references unknown fixture: ' + fixture_id)
        if fixture_id in completed:
            raise ValueError('fixture already has a formal result: ' + fixture_id)
        if fixture_id in booked_fixtures:
            raise ValueError('fixture booked more than once: ' + fixture_id)
        venue_id = ident(entry['venue'])
        if venue_id not in venues:
            raise ValueError('booking references unknown venue: ' + venue_id)
        start = _minute(entry['start'])
        end = _minute(entry['end'])
        if not start < end:
            raise ValueError('booking interval must satisfy start < end')
        bookings.append((fixture_id, venue_id, start, end, _units(entry['units'])))
        booked_fixtures.add(fixture_id)

    return venues, limits, bookings


def pending_fixtures(fixtures, completed, bookings):
    """Fixture ids with no formal result and no booking, ascending."""
    booked = {booking[0] for booking in bookings}
    return sorted(fid for fid in fixtures
                  if fid not in completed and fid not in booked)
