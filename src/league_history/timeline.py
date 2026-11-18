"""Pure value-timeline engine used by the history store.

A *value timeline* is a normalized list of segments covering [0, INF) with
no gaps and no overlaps::

    (left, right, value)   # right == INF means +infinity, value is None (pending)
                           # or a (home_goals, away_goals) tuple.

Adjacent segments carrying exactly the same value are merged.  Business time
lives in 0..1_000_000_000; INF is an internal sentinel one past the largest
legal business instant, and query output maps it back to null.
"""
from bisect import bisect_left

TMAX = 1_000_000_000
INF = TMAX + 1


def normalize(segments):
    """Merge adjacent segments carrying exactly the same complete value."""
    out = []
    for left, right, value in segments:
        if out and out[-1][1] == left and out[-1][2] == value:
            out[-1] = (out[-1][0], right, value)
        else:
            out.append((left, right, value))
    return out


def check_expected(timeline, left, right, expected):
    """The whole [left, right) range, including every internal sub-segment
    and pending holes, must currently equal ``expected`` (None == pending).

    Endpoints, single instants or only the latest record are never enough:
    every segment overlapping the interior of the range is inspected.
    """
    starts = [seg[0] for seg in timeline]
    i = bisect_left(starts, left)
    if i == len(starts) or starts[i] != left:
        i -= 1
    while i < len(timeline):
        seg_left, seg_right, value = timeline[i]
        if seg_left >= right:
            break
        if value != expected:
            return False
        i += 1
    return True


def replace_range(timeline, left, right, replacement):
    """Return a normalized timeline where [left, right) becomes replacement.

    Material outside the range and earlier knowledge are untouched.  A null
    replacement withdraws the range back to pending rather than recording 0:0.
    """
    pieces = []
    for seg_left, seg_right, value in timeline:
        if seg_right <= left or seg_left >= right:
            pieces.append((seg_left, seg_right, value))
            continue
        if seg_left < left:
            pieces.append((seg_left, left, value))
        if seg_right > right:
            pieces.append((right, seg_right, value))
    pieces.append((left, right, replacement))
    pieces.sort(key=lambda seg: seg[0])
    return normalize(pieces)
