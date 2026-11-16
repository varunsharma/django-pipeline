"""Focused replay adapter contract tests, separate from Django discovery."""
from __future__ import with_statement

import io
import os
import shutil
import tempfile
try:
    import unittest2 as unittest
except ImportError:
    import unittest
from django.test import TestCase
try:
    from django.test import SimpleTestCase
except ImportError:
    SimpleTestCase = TestCase

from ci_replay import (_context, ReplayHistory, ReplayTextTestResult,
                       ReplayTextTestRunner)


class SampleTest(unittest.TestCase):
    def test_pass(self):
        pass

    def test_skip(self):
        self.skipTest('skip')


class ReplayHistoryTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.mkdtemp(prefix='ci-replay-test-')
        self.old_cache = os.environ.get('CI_REPLAY_CACHE_DIR')
        os.environ['CI_REPLAY_CACHE_DIR'] = self.directory
        self.history = ReplayHistory()

    def tearDown(self):
        if self.old_cache is None:
            os.environ.pop('CI_REPLAY_CACHE_DIR', None)
        else:
            os.environ['CI_REPLAY_CACHE_DIR'] = self.old_cache
        shutil.rmtree(self.directory)

    def _result(self):
        return ReplayTextTestResult(
            stream=io.StringIO(), descriptions=False, verbosity=0)

    def test_skip_does_not_clear_previous_failure(self):
        test = SampleTest('test_skip')
        self.history.state['failed'] = [test.id()]
        self.history.state['pending'] = [test.id()]
        self.history.save()

        result = self._result()
        result.history = self.history
        result.startTest(test)
        result.addSkip(test, 'skip')

        self.assertEqual([test.id()], self.history.state['failed'])
        self.assertEqual([], self.history.state['pending'])

    def test_expected_failure_does_not_clear_previous_failure(self):
        test = SampleTest('test_pass')
        self.history.state['failed'] = [test.id()]
        self.history.state['pending'] = [test.id()]
        self.history.save()

        result = self._result()
        result.history = self.history
        result.startTest(test)
        result.addExpectedFailure(
            test, (AssertionError, AssertionError('expected'), None))

        self.assertEqual([test.id()], self.history.state['failed'])
        self.assertEqual([], self.history.state['pending'])

    def test_only_clean_success_clears_previous_failure(self):
        test = SampleTest('test_pass')
        self.history.state['failed'] = [test.id()]
        self.history.state['pending'] = [test.id()]
        self.history.save()

        result = self._result()
        result.history = self.history
        result.startTest(test)
        result.addSuccess(test)
        result.finalize_history()

        self.assertEqual([], self.history.state['failed'])
        self.assertEqual([], self.history.state['pending'])

    def test_call_success_after_teardown_error_does_not_clear_failure(self):
        test = SampleTest('test_pass')
        result = self._result()
        result.history = self.history
        result.startTest(test)
        result.addError(test, (RuntimeError, RuntimeError('teardown'), None))
        result.addSuccess(test)

        self.assertIn(test.id(), self.history.state['failed'])

    def test_class_teardown_error_does_not_clear_prior_failure(self):
        class ClassTeardownCase(unittest.TestCase):
            def test_pass(self):
                pass

            @classmethod
            def tearDownClass(cls):
                raise RuntimeError('class teardown failed')

        test = ClassTeardownCase('test_pass')
        self.history.state['failed'] = [test.id()]
        self.history.save()
        runner = ReplayTextTestRunner(
            stream=io.StringIO(), descriptions=False, verbosity=0,
            replay_history=self.history)

        runner.run(unittest.TestSuite([test]))

        self.assertIn(test.id(), self.history.state['failed'])

    def test_failed_then_pending_then_historically_risky_order(self):
        class OrderedTests(unittest.TestCase):
            def test_remaining(self):
                pass

            def test_risky(self):
                pass

            def test_pending(self):
                pass

            def test_failed(self):
                pass

        tests = [OrderedTests(name) for name in (
            'test_remaining', 'test_risky', 'test_pending', 'test_failed')]
        remaining, risky, pending, failed = [test.id() for test in tests]
        self.history.state['failed'] = [failed]
        self.history.state['pending'] = [pending]
        self.history.state['risk'] = {risky: 7}
        ordered = self.history.order(tests)

        self.assertEqual([failed, pending, risky, remaining],
                         [test.id() for test in ordered])

    def test_changed_source_prioritizes_matching_test_module_only(self):
        class BaseTests(unittest.TestCase):
            def test_base(self):
                pass

        class SignalTests(unittest.TestCase):
            def test_signal(self):
                pass

        BaseTests.__module__ = 'compressor.tests.test_base'
        SignalTests.__module__ = 'compressor.tests.test_signals'
        base_test = BaseTests('test_base')
        signal_test = SignalTests('test_signal')

        self.assertTrue(self.history._affected_by_changes(
            base_test, set(['compressor/base.py'])))
        self.assertFalse(self.history._affected_by_changes(
            signal_test, set(['compressor/base.py'])))

        self.history._changed_files = lambda: set(['compressor/base.py'])
        ordered = self.history.order([signal_test, base_test])
        self.assertEqual([base_test.id(), signal_test.id()],
                         [test.id() for test in ordered])

    def test_top_level_tests_map_to_configured_source_package(self):
        class FormsTests(unittest.TestCase):
            def test_forms(self):
                pass

        FormsTests.__module__ = 'tests.test_forms'
        test = FormsTests('test_forms')
        class CaptchaTests(unittest.TestCase):
            def test_simple_captcha(self):
                pass

        CaptchaTests.__module__ = 'tests.test_simple_captcha'
        captcha_test = CaptchaTests('test_simple_captcha')
        previous_package = os.environ.get('CI_REPLAY_TEST_PACKAGE')
        try:
            os.environ['CI_REPLAY_TEST_PACKAGE'] = 'dynamic_forms'
            self.assertTrue(self.history._affected_by_changes(
                test, set(['dynamic_forms/forms.py'])))
            self.assertTrue(self.history._affected_by_changes(
                captcha_test,
                set(['dynamic_forms/contrib/simple_captcha/models.py'])))
        finally:
            if previous_package is None:
                os.environ.pop('CI_REPLAY_TEST_PACKAGE', None)
            else:
                os.environ['CI_REPLAY_TEST_PACKAGE'] = previous_package

    def test_nested_tests_map_to_configured_source_package(self):
        class StorageTests(unittest.TestCase):
            def test_storage(self):
                pass

        StorageTests.__module__ = 'tests.tests.test_storage'
        test = StorageTests('test_storage')
        previous_package = os.environ.get('CI_REPLAY_TEST_PACKAGE')
        try:
            os.environ['CI_REPLAY_TEST_PACKAGE'] = 'pipeline'
            self.assertTrue(self.history._affected_by_changes(
                test, set(['pipeline/storage.py'])))
        finally:
            if previous_package is None:
                os.environ.pop('CI_REPLAY_TEST_PACKAGE', None)
            else:
                os.environ['CI_REPLAY_TEST_PACKAGE'] = previous_package

    def test_reordered_cases_keep_class_fixture_groups_contiguous(self):
        class FirstCase(unittest.TestCase):
            def test_risky(self):
                pass

            def test_other(self):
                pass

        class SecondCase(unittest.TestCase):
            def test_failed(self):
                pass

            def test_other(self):
                pass

        tests = [FirstCase('test_risky'), FirstCase('test_other'),
                 SecondCase('test_other'), SecondCase('test_failed')]
        risky, first_other, second_other, failed = [test.id() for test in tests]
        self.history.state['failed'] = [failed]
        self.history.state['risk'] = {risky: 4}

        ordered = self.history.order(tests)

        self.assertEqual([failed, second_other, risky, first_other],
                         [test.id() for test in ordered])

    def test_failed_simple_test_case_precedes_unfailed_test_case(self):
        class DatabaseCase(TestCase):
            def test_regular(self):
                pass

        class SimpleCase(SimpleTestCase):
            def test_failed(self):
                pass

        regular = DatabaseCase('test_regular')
        failed = SimpleCase('test_failed')
        self.history.state['failed'] = [failed.id()]

        ordered = self.history.order([regular, failed])

        self.assertEqual([failed.id(), regular.id()],
                         [test.id() for test in ordered])

    def test_collection_error_is_first_and_stops_discovered_tests(self):
        executed = []

        class OrdinaryTest(unittest.TestCase):
            def test_would_run(self):
                executed.append('ordinary')

        collection_error = list(
            unittest.TestLoader().loadTestsFromName(
                '_missing_ci_replay_test_module_'))[0]
        suite = unittest.TestSuite(self.history.order([
            OrdinaryTest('test_would_run'), collection_error]))
        runner = ReplayTextTestRunner(
            stream=io.StringIO(), descriptions=False, verbosity=0,
            replay_history=self.history)

        result = runner.run(suite)

        self.assertEqual([], executed)
        self.assertEqual(1, result.testsRun)
        self.assertIn(collection_error.id(), self.history.state['failed'])

    def test_repository_and_lane_are_part_of_cache_scope(self):
        previous_repository = os.environ.get('CI_REPLAY_REPOSITORY')
        previous_lane = os.environ.get('CI_REPLAY_LANE')
        try:
            os.environ['CI_REPLAY_REPOSITORY'] = 'owner/first'
            os.environ['CI_REPLAY_LANE'] = 'py27-django-1.4'
            first = ReplayHistory()
            os.environ['CI_REPLAY_REPOSITORY'] = 'owner/second'
            second = ReplayHistory()
            os.environ['CI_REPLAY_REPOSITORY'] = 'owner/first'
            os.environ['CI_REPLAY_LANE'] = 'py27-django-1.5'
            third = ReplayHistory()
            self.assertNotEqual(first.path, second.path)
            self.assertNotEqual(first.path, third.path)
        finally:
            if previous_repository is None:
                os.environ.pop('CI_REPLAY_REPOSITORY', None)
            else:
                os.environ['CI_REPLAY_REPOSITORY'] = previous_repository
            if previous_lane is None:
                os.environ.pop('CI_REPLAY_LANE', None)
            else:
                os.environ['CI_REPLAY_LANE'] = previous_lane

    def test_ci_provenance_is_retained_in_replay_context(self):
        names = ('CI_REPLAY_REPOSITORY', 'CI_REPLAY_BRANCH', 'CI_REPLAY_PR',
                 'TRAVIS_BUILD_ID', 'TRAVIS_JOB_ID', 'CI_REPLAY_LANE')
        previous = dict((name, os.environ.get(name)) for name in names)
        try:
            os.environ['CI_REPLAY_REPOSITORY'] = 'owner/project'
            os.environ['CI_REPLAY_BRANCH'] = 'feature/replay'
            os.environ['CI_REPLAY_PR'] = '42'
            os.environ['TRAVIS_BUILD_ID'] = '1001'
            os.environ['TRAVIS_JOB_ID'] = '1002'
            os.environ['CI_REPLAY_LANE'] = 'py27-dj17'
            context = _context()
            self.assertEqual('owner/project', context['repository'])
            self.assertEqual('feature/replay', context['branch'])
            self.assertEqual('42', context['pr'])
            self.assertEqual('1001', context['run'])
            self.assertEqual('1002', context['job'])
            self.assertEqual('py27-dj17', context['lane'])
            self.assertTrue(context['sha'])
            self.assertTrue(context['python'])
        finally:
            for name, value in previous.items():
                if value is None:
                    os.environ.pop(name, None)
                else:
                    os.environ[name] = value

    def test_invalid_cache_falls_back_to_cold_history(self):
        self.history.save()
        with open(self.history.path, 'w') as stream:
            stream.write('{invalid')

        cold_history = ReplayHistory()
        self.assertEqual([], cold_history.state['failed'])
        cold_history.order([SampleTest('test_pass')])
        with open(cold_history.path) as stream:
            self.assertEqual(1, __import__('json').load(stream)['version'])

    def test_symlinked_cache_is_ignored_without_following_it(self):
        self.history.save()
        os.unlink(self.history.path)
        target = os.path.join(self.directory, 'outside.json')
        with open(target, 'w') as stream:
            stream.write('unchanged')
        os.symlink(target, self.history.path)

        isolated_history = ReplayHistory()
        self.assertTrue(isolated_history.checkpoint_disabled)
        isolated_history.order([SampleTest('test_pass')])

        with open(target) as stream:
            self.assertEqual('unchanged', stream.read())

    def test_runner_stops_after_first_failure_and_keeps_remaining_pending(self):
        executed = []

        class OrderedTests(unittest.TestCase):
            def test_first_fails(self):
                executed.append('first')
                self.fail('expected failure')

            def test_second_is_not_started(self):
                executed.append('second')

        suite = unittest.TestSuite([
            OrderedTests('test_first_fails'),
            OrderedTests('test_second_is_not_started'),
        ])
        tests = list(suite)
        first_id, second_id = tests[0].id(), tests[1].id()
        suite = unittest.TestSuite(self.history.order(tests))
        runner = ReplayTextTestRunner(
            stream=io.StringIO(), descriptions=False, verbosity=0,
            replay_history=self.history)

        result = runner.run(suite)

        self.assertEqual(['first'], executed)
        self.assertEqual(1, result.testsRun)
        self.assertEqual([first_id],
                         self.history.state['failed'])
        self.assertEqual([second_id],
                         self.history.state['pending'])


if __name__ == '__main__':
    unittest.main()
