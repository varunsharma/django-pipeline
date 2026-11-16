CI test replay
==============

The Travis tox test environments use ``ci_replay.ReplayDiscoverRunner`` to
run previously failed tests first, followed by interrupted tests, tests mapped
to changed source, historically risky tests, and remaining tests. The runner
stops after the first test failure. Every existing ``TOXENV`` remains
represented in the Travis job list, and each is placed in its own ordered
stage so a failed job prevents later environments from starting. The docs tox
environment remains available through the normal tox configuration.

Replay history is best-effort. Its stable cache key is scoped by repository,
branch, pull request, head ref, tox lane, and Python runtime. Each unresolved
test or collection failure separately retains its originating repository,
pull request, head/base refs and SHAs, commit, run ID/attempt, job, tox lane,
and Python runtime until a clean pass; the bounded recent-context list is
supplemental. Provenance is not part of the cache key. The cache stores test
identifiers and bounded risk counts, never test output.

Travis restores ``.ci-replay-cache`` for pull-request builds, but only stores
cache modifications from normal pushes, so a new pull-request failure may not
persist to its next PR run. Concurrent runs sharing a stable key may restore
the same snapshot and the last saved snapshot can omit updates from another
run. Missing, stale, invalid, or unavailable history starts with the broad
candidate set, and the runner still stops at the first failure. History is not
durable state or a correctness requirement. PR, base SHA, or run-attempt
values may be empty when Travis does not expose them. See
https://docs.travis-ci.com/user/caching/ for cache write rules.

Tests are installed under ``tests.tests`` while the application package is
``pipeline``. ``CI_REPLAY_TEST_PACKAGE`` maps conventional test module names to
changed application source files. This matching is heuristic; tests whose
relationship to a change is not recognized remain in the risk or remaining
groups and are still run unless an earlier failure stops the job.

The ordered singleton Travis stages provide build-wide fail-fast: Travis
cancels later stages after a failed stage, while normal Django cleanup for the
failing job still runs. No branch-wide, commit-wide, or pull-request-wide
cancellation is performed. The empty default Python/environment row is
explicitly excluded from the Travis matrix. Each included test job sets its
Python version and tox lane, leaving only the staged test jobs in the build.
See https://docs.travis-ci.com/user/build-matrix/ for matrix exclusion rules.

Run the focused adapter checks with::

    python -m unittest discover -s ci_replay_tests -v

These checks stay separate from Django test discovery. The existing tox
application suite and docs environment remain in place.
