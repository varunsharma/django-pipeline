CI test replay
==============

The Travis tox test environments use ``ci_replay.ReplayDiscoverRunner`` to
run previously failed tests first, followed by interrupted tests, tests mapped
to changed source, historically risky tests, and remaining tests. The runner
stops after the first test failure. The docs tox environment is unchanged.

Replay history is best-effort. It is scoped by repository, branch, pull
request, commit, Travis run, job, tox environment, and Python runtime. The
project does not configure a persistent Travis cache for
``.ci-replay-cache``; jobs can start cold and use normal discovery order.
History is a local optimization, not durable state or a correctness
requirement. Invalid or unavailable history is discarded.

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
