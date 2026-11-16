"""Best-effort unittest replay ordering for Django's single test job.

This module uses Python 2.6-compatible syntax. History stores test
identifiers and CI provenance, never test output or environment dumps.
"""
from __future__ import print_function, with_statement

import hashlib
import json
import os
import platform
import re
import subprocess
import tempfile
import threading
try:
    # Django 1.4/1.5 expose unittest2 through this compatibility module.
    from django.utils import unittest
except ImportError:
    import unittest

from django.test import TestCase
try:
    from django.test import SimpleTestCase
except ImportError:
    SimpleTestCase = TestCase

MAX_BYTES = 8 * 1024 * 1024
MAX_TESTS = 25000


def _git(*args):
    try:
        process = subprocess.Popen(['git'] + list(args), stdout=subprocess.PIPE,
                                   stderr=subprocess.PIPE)
        timer = threading.Timer(5, process.kill)
        timer.start()
        try:
            output, _ = process.communicate()
        finally:
            timer.cancel()
        if process.returncode:
            return None
        return output.decode('utf-8', 'replace').strip()
    except (OSError, AttributeError):
        return None


def _context():
    env = os.environ
    branch = (env.get('CI_REPLAY_BRANCH') or env.get('TRAVIS_PULL_REQUEST_BRANCH')
              or env.get('BRANCH_NAME') or env.get('GIT_BRANCH')
              or env.get('TRAVIS_BRANCH') or env.get('GITHUB_HEAD_REF')
              or env.get('GITHUB_REF_NAME') or env.get('CI_COMMIT_REF_NAME')
              or _git('rev-parse', '--abbrev-ref', 'HEAD') or '')
    ref = env.get('GITHUB_REF', '')
    pr = (env.get('CI_REPLAY_PR') or env.get('CHANGE_ID') or
          env.get('TRAVIS_PULL_REQUEST') or env.get('CI_MERGE_REQUEST_IID'))
    if not pr and ref.startswith('refs/pull/'):
        pr = ref.split('/')[2]
    values = {
        'repository': env.get('CI_REPLAY_REPOSITORY') or
                      env.get('TRAVIS_REPO_SLUG') or env.get('GITHUB_REPOSITORY')
                      or env.get('CI_PROJECT_PATH') or '',
        'branch': branch,
        'pr': pr or '',
        'sha': _git('rev-parse', 'HEAD') or '',
        'run': env.get('TRAVIS_BUILD_ID') or env.get('GITHUB_RUN_ID')
               or env.get('BUILD_TAG') or env.get('BUILD_NUMBER')
               or env.get('CI_PIPELINE_ID') or '',
        'job': env.get('TRAVIS_JOB_ID') or env.get('GITHUB_JOB')
               or env.get('JOB_NAME')
               or env.get('CI_JOB_ID') or '',
        'lane': (env.get('CI_REPLAY_LANE') or env.get('TOX_ENV_NAME') or
                 env.get('TOX_ENV') or env.get('TOXENV') or 'default'),
        'python': platform.python_version(),
    }
    return dict((key, str(value)[:400]) for key, value in values.items())


class ReplayHistory(object):
    def __init__(self, directory=None):
        self.context = _context()
        scope = [self.context[key] for key in
                 ('repository', 'branch', 'lane', 'python')]
        digest = hashlib.sha256(json.dumps(scope).encode('utf-8')).hexdigest()[:32]
        self.directory = os.path.abspath(directory or
                                         os.environ.get('CI_REPLAY_CACHE_DIR', '.ci-replay-cache'))
        self.path = os.path.join(self.directory, digest + '.json')
        self.state = {'version': 1, 'failed': [], 'pending': [],
                      'risk': {}, 'contexts': []}
        self.checkpoint_disabled = False
        if os.path.islink(self.directory) or os.path.islink(self.path):
            self.checkpoint_disabled = True
        elif os.path.exists(self.path):
            try:
                self._load()
            except ValueError:
                # Cache data is advisory. An invalid file starts a cold run.
                self.state = {'version': 1, 'failed': [], 'pending': [],
                              'risk': {}, 'contexts': []}
        self.previous_sha = (self.state['contexts'][-1].get('sha', '')
                             if self.state['contexts'] else '')
        self.state['contexts'] = (self.state['contexts'] + [self.context])[-12:]
        self.failed_this_run = set()
        self.passed_tests = set()

    def _valid_ids(self, value):
        return (isinstance(value, list) and len(value) <= MAX_TESTS and
                all(isinstance(item, type(u'')) and len(item) <= 2000
                    for item in value))

    def _load(self):
        try:
            if os.path.getsize(self.path) > MAX_BYTES:
                raise ValueError('oversized state')
            with open(self.path) as stream:
                value = json.load(stream)
            if (not isinstance(value, dict) or value.get('version') != 1 or
                    not self._valid_ids(value.get('failed')) or
                    not self._valid_ids(value.get('pending')) or
                    not isinstance(value.get('risk'), dict) or
                    len(value['risk']) > MAX_TESTS or
                    not self._valid_ids(list(value['risk'])) or
                    not all(isinstance(n, int) and 0 <= n <= 1000000
                            for n in value['risk'].values()) or
                    not isinstance(value.get('contexts'), list) or
                    len(value['contexts']) > 12 or
                    not all(isinstance(item, dict) and
                            isinstance(item.get('sha', ''), type(u''))
                            for item in value['contexts'])):
                raise ValueError('invalid schema')
            self.state = value
        except (ValueError, TypeError, AttributeError, IOError, OSError) as exc:
            raise ValueError('Invalid replay history: ' + str(exc))

    def save(self):
        if self.checkpoint_disabled or os.path.islink(self.directory) or os.path.islink(self.path):
            self.checkpoint_disabled = True
            return
        if (len(self.state['failed']) > MAX_TESTS or
                len(self.state['pending']) > MAX_TESTS or
                len(self.state['risk']) > MAX_TESTS):
            self.checkpoint_disabled = True
            return
        try:
            if not os.path.isdir(self.directory):
                os.makedirs(self.directory)
            payload = json.dumps(self.state, sort_keys=True).encode('utf-8')
            if len(payload) > MAX_BYTES:
                self.checkpoint_disabled = True
                return
            fd, temporary = tempfile.mkstemp(prefix='.checkpoint-', dir=self.directory)
        except (IOError, OSError, ValueError):
            self.checkpoint_disabled = True
            return
        try:
            with os.fdopen(fd, 'wb') as stream:
                stream.write(payload)
                stream.flush()
                os.fsync(stream.fileno())
            os.rename(temporary, self.path)
        except (IOError, OSError, ValueError):
            self.checkpoint_disabled = True
        finally:
            if os.path.exists(temporary):
                try:
                    os.unlink(temporary)
                except (IOError, OSError):
                    pass

    def _changed_files(self):
        if not re.match(r'^[0-9a-f]{40}$', self.previous_sha):
            return None
        output = _git('diff', '--no-ext-diff', '--name-only', self.previous_sha,
                      'HEAD', '--')
        return None if output is None else set(output.splitlines())

    def _affected_by_changes(self, test, changed):
        if not changed:
            return False
        module = test.__class__.__module__.replace('.', '/')
        candidates = set([module + '.py'])
        marker = '/tests/test_'
        if marker in module:
            default_package, test_module = module.rsplit(marker, 1)
            package = (os.environ.get('CI_REPLAY_TEST_PACKAGE') or
                       default_package)
            package = package.strip('/').replace('.', '/')
            candidates.add(package + '/' + test_module + '.py')
            candidates.add(package + '/' + test_module + '/__init__.py')
        elif module.startswith('tests/test_'):
            package = os.environ.get('CI_REPLAY_TEST_PACKAGE', '')
            test_module = module[len('tests/test_'):]
            if package:
                package = package.strip('/').replace('.', '/')
                candidates.add(package + '/' + test_module + '.py')
                candidates.add(package + '/' + test_module + '/__init__.py')
                for path in changed:
                    if path.startswith(package + '/'):
                        relative = path[len(package) + 1:]
                        parts = relative.split('/')
                        if (parts[-1] == test_module + '.py' or
                                test_module in parts[:-1]):
                            return True
        return bool(candidates.intersection(changed))

    def order(self, tests):
        failed, pending = set(self.state['failed']), set(self.state['pending'])
        risk = self.state['risk']
        changed = self._changed_files()

        def priority(test):
            identifier = test.id()
            if identifier in failed:
                return (0, 0)
            if identifier in pending:
                return (1, 0)
            if changed is None or self._affected_by_changes(test, changed):
                return (2, -risk.get(identifier, 0))
            return (3, -risk.get(identifier, 0))

        # Keep each TestCase's methods together for setUpClass/tearDownClass,
        # and retain Django's TestCase -> SimpleTestCase -> other suite order.
        groups = []
        by_class = {}
        for test in tests:
            test_class = test.__class__
            if test_class not in by_class:
                by_class[test_class] = []
                groups.append(by_class[test_class])
            by_class[test_class].append(test)

        def group_priority(group):
            test_class = group[0].__class__
            if test_class.__name__ == '_FailedTest':
                # Import and collection failures must be surfaced before tests.
                rank = -1
            elif issubclass(test_class, TestCase):
                rank = 0
            elif issubclass(test_class, SimpleTestCase):
                rank = 1
            else:
                rank = 2
            return (min(priority(test) for test in group), rank)

        groups.sort(key=group_priority)
        tests = []
        for group in groups:
            group.sort(key=priority)
            tests.extend(group)
        pending_ids = []
        seen = set()
        for identifier in [test.id() for test in tests] + self.state['pending']:
            if identifier not in seen:
                pending_ids.append(identifier)
                seen.add(identifier)
        self.state['pending'] = pending_ids
        self.save()
        return tests

    def error(self, test):
        identifier = test.id()
        first_error = identifier not in self.failed_this_run
        self.failed_this_run.add(identifier)
        if identifier not in self.state['failed']:
            self.state['failed'].append(identifier)
        if first_error:
            self.state['risk'][identifier] = min(
                1000000, self.state['risk'].get(identifier, 0) + 1)
        self.save()

    def complete(self, test, passed=False):
        identifier = test.id()
        self.state['pending'] = [item for item in self.state['pending']
                                 if item != identifier]
        if passed and identifier not in self.failed_this_run:
            self.passed_tests.add(identifier)
            self.state['failed'] = [item for item in self.state['failed']
                                    if item != identifier]
        self.save()


class ReplayTextTestResult(unittest.TextTestResult):
    history = None

    def startTest(self, test):
        self._test_errors = set()
        self._passed_tests = {}
        self._failed_classes = set()
        unittest.TextTestResult.startTest(self, test)

    def _mark_class_error(self, test):
        identifier = test.id()
        match = re.search(r'\(([^)]+)\)', identifier)
        if match:
            self._failed_classes.add(match.group(1))
        else:
            self._failed_classes.add(
                test.__class__.__module__ + '.' + test.__class__.__name__)

    def addError(self, test, err):
        if not hasattr(self, '_test_errors'):
            self._test_errors = set()
        self._test_errors.add(test.id())
        self._mark_class_error(test)
        self.history.error(test)
        self.history.complete(test)
        return unittest.TextTestResult.addError(self, test, err)

    def addFailure(self, test, err):
        if not hasattr(self, '_test_errors'):
            self._test_errors = set()
        self._test_errors.add(test.id())
        self._mark_class_error(test)
        self.history.error(test)
        self.history.complete(test)
        return unittest.TextTestResult.addFailure(self, test, err)

    def addSuccess(self, test):
        passed = test.id() not in self._test_errors
        self.history.complete(test)
        if passed:
            self._passed_tests[test.id()] = (
                test, test.__class__.__module__ + '.' + test.__class__.__name__)
        return unittest.TextTestResult.addSuccess(self, test)

    def finalize_history(self):
        for test, class_name in self._passed_tests.values():
            short_name = class_name.rsplit('.', 1)[-1]
            class_failed = any(
                failed == class_name or failed.endswith('.' + short_name)
                for failed in self._failed_classes)
            if not class_failed:
                self.history.complete(test, passed=True)

    def addSkip(self, test, reason):
        self.history.complete(test)
        return unittest.TextTestResult.addSkip(self, test, reason)

    def addExpectedFailure(self, test, err):
        self.history.complete(test)
        return unittest.TextTestResult.addExpectedFailure(self, test, err)

    def addUnexpectedSuccess(self, test):
        self.history.error(test)
        self.history.complete(test)
        return unittest.TextTestResult.addUnexpectedSuccess(self, test)

    def addSubTest(self, test, subtest, err):
        if err is not None:
            if not hasattr(self, '_test_errors'):
                self._test_errors = set()
            self._test_errors.add(test.id())
            self.history.error(test)
            self.history.complete(test)
        add_subtest = getattr(unittest.TextTestResult, 'addSubTest', None)
        if add_subtest is None:
            if err is not None:
                return self.addFailure(test, err)
            return None
        return add_subtest(self, test, subtest, err)


class ReplayTextTestRunner(unittest.TextTestRunner):
    def __init__(self, *args, **kwargs):
        history = kwargs.pop('replay_history')
        kwargs['failfast'] = True
        kwargs['resultclass'] = type(
            'ReplayBoundTextTestResult', (ReplayTextTestResult,),
            {'history': history})
        unittest.TextTestRunner.__init__(self, *args, **kwargs)

    def run(self, test):
        result = unittest.TextTestRunner.run(self, test)
        result.finalize_history()
        return result


try:
    from django.test.runner import DiscoverRunner
except ImportError:
    # django-discover-runner is already a dependency for Django 1.4/1.5.
    from discover_runner import DiscoverRunner


class ReplayDiscoverRunner(DiscoverRunner):
    """Django runner retaining the standard discovery and DB lifecycle."""

    def __init__(self, *args, **kwargs):
        if kwargs.get('parallel', 1) > 1:
            raise ValueError('Replay runner requires a single test process')
        DiscoverRunner.__init__(self, *args, **kwargs)

    def run_suite(self, suite, **kwargs):
        history = ReplayHistory()
        tests = []

        def flatten(items):
            for item in items:
                if isinstance(item, unittest.TestSuite):
                    flatten(item)
                else:
                    tests.append(item)

        flatten(suite)
        suite = unittest.TestSuite(history.order(tests))
        runner = ReplayTextTestRunner(
            verbosity=self.verbosity, replay_history=history)
        return runner.run(suite)
