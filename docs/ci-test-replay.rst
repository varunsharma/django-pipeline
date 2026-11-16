CI test replay
==============

The Travis tox test environments use ``ci_replay.ReplayDiscoverRunner`` to
run previously failed tests first, followed by interrupted tests, tests mapped
to changed source, historically risky tests, and remaining tests. The runner
stops after the first test failure. The docs tox environment is unchanged.

Replay history is best-effort. It is scoped by repository, branch, pull
request, commit, Travis run, job, tox environment, and Python runtime. Travis
caches ``.ci-replay-cache`` between normal pushes and restores caches for
pull-request builds, but only stores cache modifications from normal pushes.
New failures from a pull-request build are not guaranteed to persist to its
next run. A missing, stale, invalid, or unavailable cache starts with the broad
candidate set, and the runner still stops at the
first failure. History is not durable state or a correctness requirement.
PR, base SHA, or run-attempt values may be empty when Travis does not expose
them to a job. See https://docs.travis-ci.com/user/caching/ for cache scope
and write rules. Invalid or unavailable history is discarded.

Each tox environment is one Travis job. Fail-fast stops later tests in that
job, while normal Django cleanup still runs. Travis does not reliably cancel
sibling matrix jobs already running, so those jobs can continue. No branch,
commit, or pull-request-wide cancellation is performed.

Tests are installed under ``tests.tests`` while the application package is
``pipeline``. ``CI_REPLAY_TEST_PACKAGE`` maps conventional test module names to
changed application source files. This matching is heuristic; tests whose
relationship to a change is not recognized remain in the risk or remaining
groups and are still run unless an earlier failure stops the job.

Run the focused adapter checks with::

    python -m unittest discover -s ci_replay_tests -v

These checks stay separate from Django test discovery. The existing tox
application suite and docs environment remain in place.

Unresolved test and collection failures keep their originating repository, pull request, head/base refs and SHAs, run ID/attempt, job, tox lane, and Python runtime attached per test ID. That provenance has no 12-run expiry and is removed only after a clean pass; the bounded recent-context list is supplemental. The cache schema change starts once from cold history when older cached data is present.
