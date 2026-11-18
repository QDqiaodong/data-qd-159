"""Offline local history store for corrected/withdrawn fixture results.

The store keeps explicit fixture identities on a business-validity timeline
([left, right) half-open intervals, right=null means +infinity) and replays
the standings as known at any committed knowledge sequence.
"""
