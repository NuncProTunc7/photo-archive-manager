"""Execution: the journal protocol that makes every rename recoverable (spec 7).

Decomposed as spec 7.1 rev 7 prescribes, because apply, undo and recovery all
perform the same verified rename and must not drift into three implementations:

    rename.py       perform_verified_rename() - the SINGLE filesystem operation
    journal_ops.py  RenameTransaction - the only place journal states change
    unit.py         companion-unit sequencing and revert (spec 7.3)      [Task 7]
    validator.py    plan preflight                                       [Task 6]
    reporter.py     session report + UX copy                             [Task 10]
    apply.py        orchestration only                                   [Task 6]

The first two are built and proven before anything orchestrates them.
"""
