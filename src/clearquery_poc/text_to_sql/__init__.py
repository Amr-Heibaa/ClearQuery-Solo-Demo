"""Person 1 Text-to-SQL component (Phase 2b.2).

PROVISIONAL contract ``p1-t2s-0.1`` — the final interface must be agreed with
Person 3 (Abdallah) before integration.

Rules enforced by design:
* SQL is produced only by real inference of the pinned pre-trained model.
* No question-to-SQL lookup, and no Gold SQL substitution: this package does not
  import ``clearquery_poc.gold_sql`` (checked by tests).
* A ``SqlCandidate`` is never a ``ValidatedSql``; it has no ``validated`` field
  and cannot be passed to the executor. Only Person 3's SQL Guard may validate.
"""
