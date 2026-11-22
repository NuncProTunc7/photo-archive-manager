"""spec 8.3: events - the time/GPS partition of the archive's dated files.

`grouping.py` is the algorithm and is pure: it takes rows and returns events, and
touches neither the filesystem nor the catalog. The SQL that feeds it and the SQL that
stores its answer both live in `pam/catalog/repo/` like every other query in the
project - the input query in `plans.py` (grouping is `pam plan`'s step 0, spec 6.3 rule
5b) and the writer in `events.py`.
"""
