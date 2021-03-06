# coding: utf-8
# Copyright 2013 Google Inc. All Rights Reserved.
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
#      http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS-IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.

"""Tests that walk through Course Builder pages."""

__author__ = 'Sean Lip'

import __builtin__
import copy
import csv
import datetime
import logging
import os
import re
import shutil
import time
import urllib
import zipfile
import appengine_config
from controllers import lessons
from controllers import sites
from controllers import utils
from controllers.utils import XsrfTokenManager
from models import config
from models import courses
from models import jobs
from models import models
from models import transforms
from models import vfs
from models.courses import Course
import modules.admin.admin
from modules.announcements.announcements import AnnouncementEntity
from tools import verify
from tools.etl import etl
from tools.etl import remote
import actions
from actions import assert_contains
from actions import assert_contains_all_of
from actions import assert_does_not_contain
from actions import assert_equals
from google.appengine.api import memcache
from google.appengine.api import namespace_manager


# A number of data files in a test course.
COURSE_FILE_COUNT = 70
# Base filesystem location for test data.
TEST_DATA_BASE = '/tmp/experimental/coursebuilder/test-data/'


# There is an expectation in our tests of automatic import of data/*.csv files,
# which is achieved below by selecting an alternative factory method.
courses.Course.create_new_default_course = (
    courses.Course.custom_new_default_course_for_test)


class InfrastructureTest(actions.TestBase):
    """Test core infrastructure classes agnostic to specific user roles."""

    def test_response_content_type_is_application_json_in_utf_8(self):
        response = self.testapp.get(
            '/rest/config/item?key=gcb_config_update_interval_sec')
        self.assertEqual(
            'application/json, charset=utf-8', response.headers['Content-Type'])

    def test_xsrf_token_manager(self):
        """Test XSRF token operations."""

        # os.environ['AUTH_DOMAIN'] = 'test_domain'
        # os.environ['APPLICATION_ID'] = 'test app'

        # Issues and verify anonymous user token.
        action = 'test-action'
        token = utils.XsrfTokenManager.create_xsrf_token(action)
        assert '/' in token
        assert utils.XsrfTokenManager.is_xsrf_token_valid(token, action)

        # Impersonate real user.
        os.environ['USER_EMAIL'] = 'test_email'
        os.environ['USER_ID'] = 'test_id'

        # Issues and verify real user token.
        action = 'test-action'
        token = utils.XsrfTokenManager.create_xsrf_token(action)
        assert '/' in token
        assert utils.XsrfTokenManager.is_xsrf_token_valid(token, action)

        # Check forged time stamp invalidates token.
        parts = token.split('/')
        assert len(parts) == 2
        forgery = '%s/%s' % (long(parts[0]) + 1000, parts[1])
        assert not forgery == token
        assert not utils.XsrfTokenManager.is_xsrf_token_valid(forgery, action)

        # Check token properly expires.
        action = 'test-action'
        time_in_the_past = long(
            time.time() - utils.XsrfTokenManager.XSRF_TOKEN_AGE_SECS)
        # pylint: disable-msg=protected-access
        old_token = utils.XsrfTokenManager._create_token(
            action, time_in_the_past)
        assert not utils.XsrfTokenManager.is_xsrf_token_valid(old_token, action)

        # Clean up.
        # del os.environ['APPLICATION_ID']
        # del os.environ['AUTH_DOMAIN']
        del os.environ['USER_EMAIL']
        del os.environ['USER_ID']

    def test_import_course(self):
        """Tests importing one course into another."""

        # Setup courses.
        sites.setup_courses('course:/a::ns_a, course:/b::ns_b, course:/:/')

        # Validate the courses before import.
        all_courses = sites.get_all_courses()
        dst_app_context_a = all_courses[0]
        dst_app_context_b = all_courses[1]
        src_app_context = all_courses[2]

        dst_course_a = courses.Course(None, app_context=dst_app_context_a)
        dst_course_b = courses.Course(None, app_context=dst_app_context_b)
        src_course = courses.Course(None, app_context=src_app_context)

        assert not dst_course_a.get_units()
        assert not dst_course_b.get_units()
        assert 11 == len(src_course.get_units())

        # Import 1.2 course into 1.3.
        errors = []
        src_course_out, dst_course_out_a = dst_course_a.import_from(
            src_app_context, errors)
        if errors:
            raise Exception(errors)
        assert len(
            src_course.get_units()) == len(src_course_out.get_units())
        assert len(
            src_course_out.get_units()) == len(dst_course_out_a.get_units())

        # Import 1.3 course into 1.3.
        errors = []
        src_course_out_a, dst_course_out_b = dst_course_b.import_from(
            dst_app_context_a, errors)
        if errors:
            raise Exception(errors)
        assert src_course_out_a.get_units() == dst_course_out_b.get_units()

        # Test delete.
        units_to_delete = dst_course_a.get_units()
        deleted_count = 0
        for unit in units_to_delete:
            assert dst_course_a.delete_unit(unit)
            deleted_count += 1
        dst_course_a.save()
        assert deleted_count == len(units_to_delete)
        assert not dst_course_a.get_units()
        assert not dst_course_a.app_context.fs.list(os.path.join(
            dst_course_a.app_context.get_home(), 'assets/js/'))

        # Clean up.
        sites.reset_courses()

    def test_create_new_course(self):
        """Tests creating a new course."""

        # Setup courses.
        sites.setup_courses('course:/test::ns_test, course:/:/')

        # Add several units.
        course = courses.Course(None, app_context=sites.get_all_courses()[0])
        link = course.add_link()
        unit = course.add_unit()
        assessment = course.add_assessment()
        course.save()
        assert course.find_unit_by_id(link.unit_id)
        assert course.find_unit_by_id(unit.unit_id)
        assert course.find_unit_by_id(assessment.unit_id)
        assert 3 == len(course.get_units())
        assert assessment.unit_id == 3

        # Check unit can be found.
        assert unit == course.find_unit_by_id(unit.unit_id)
        assert not course.find_unit_by_id(999)

        # Update unit.
        unit.title = 'Test Title'
        course.update_unit(unit)
        course.save()
        assert 'Test Title' == course.find_unit_by_id(unit.unit_id).title

        # Update assessment.
        assessment_content = open(os.path.join(
            appengine_config.BUNDLE_ROOT,
            'assets/js/assessment-Pre.js'), 'rb').readlines()
        assessment_content = u''.join(assessment_content)
        errors = []
        course.set_assessment_content(assessment, assessment_content, errors)
        course.save()
        assert not errors
        assessment_content_stored = course.app_context.fs.get(os.path.join(
            course.app_context.get_home(),
            course.get_assessment_filename(assessment.unit_id)))
        assert assessment_content == assessment_content_stored

        # Test adding lessons.
        lesson_a = course.add_lesson(unit)
        lesson_b = course.add_lesson(unit)
        lesson_c = course.add_lesson(unit)
        course.save()
        assert [lesson_a, lesson_b, lesson_c] == course.get_lessons(
            unit.unit_id)
        assert lesson_c.lesson_id == 6

        # Reorder lessons.
        new_order = [
            {'id': link.unit_id},
            {
                'id': unit.unit_id,
                'lessons': [
                    {'id': lesson_b.lesson_id},
                    {'id': lesson_a.lesson_id},
                    {'id': lesson_c.lesson_id}]},
            {'id': assessment.unit_id}]
        course.reorder_units(new_order)
        course.save()
        assert [lesson_b, lesson_a, lesson_c] == course.get_lessons(
            unit.unit_id)

        # Move lesson to another unit.
        another_unit = course.add_unit()
        course.move_lesson_to(lesson_b, another_unit)
        course.save()
        assert [lesson_a, lesson_c] == course.get_lessons(unit.unit_id)
        assert [lesson_b] == course.get_lessons(another_unit.unit_id)
        course.delete_unit(another_unit)
        course.save()

        # Make the course available.
        get_environ_old = sites.ApplicationContext.get_environ

        def get_environ_new(self):
            environ = get_environ_old(self)
            environ['course']['now_available'] = True
            return environ

        sites.ApplicationContext.get_environ = get_environ_new

        # Test public/private assessment.
        assessment_url = (
            '/test/' + course.get_assessment_filename(assessment.unit_id))
        assert not assessment.now_available
        response = self.get(assessment_url, expect_errors=True)
        assert_equals(response.status_int, 403)
        assessment = course.find_unit_by_id(assessment.unit_id)
        assessment.now_available = True
        course.update_unit(assessment)
        course.save()
        response = self.get(assessment_url)
        assert_equals(response.status_int, 200)

        # Check delayed assessment deletion.
        course.delete_unit(assessment)
        response = self.get(assessment_url)  # note: file is still available
        assert_equals(response.status_int, 200)
        course.save()
        response = self.get(assessment_url, expect_errors=True)
        assert_equals(response.status_int, 404)

        # Test public/private activity.
        lesson_a = course.find_lesson_by_id(None, lesson_a.lesson_id)
        lesson_a.now_available = False
        lesson_a.has_activity = True
        course.update_lesson(lesson_a)
        errors = []
        course.set_activity_content(lesson_a, u'var activity = []', errors)
        assert not errors
        activity_url = (
            '/test/' + course.get_activity_filename(None, lesson_a.lesson_id))
        response = self.get(activity_url, expect_errors=True)
        assert_equals(response.status_int, 403)
        lesson_a = course.find_lesson_by_id(None, lesson_a.lesson_id)
        lesson_a.now_available = True
        course.update_lesson(lesson_a)
        course.save()
        response = self.get(activity_url)
        assert_equals(response.status_int, 200)

        # Check delayed activity.
        course.delete_lesson(lesson_a)
        response = self.get(activity_url)  # note: file is still available
        assert_equals(response.status_int, 200)
        course.save()
        response = self.get(activity_url, expect_errors=True)
        assert_equals(response.status_int, 404)

        # Test deletes removes all child objects.
        course.delete_unit(link)
        course.delete_unit(unit)
        assert not course.delete_unit(assessment)
        course.save()
        assert not course.get_units()
        assert not course.app_context.fs.list(os.path.join(
            course.app_context.get_home(), 'assets/js/'))

        # Clean up.
        sites.ApplicationContext.get_environ = get_environ_old
        sites.reset_courses()

    def test_unit_lesson_not_available(self):
        """Tests that unavailable units and lessons behave correctly."""

        # Setup a new course.
        sites.setup_courses('course:/test::ns_test, course:/:/')
        config.Registry.test_overrides[
            models.CAN_USE_MEMCACHE.name] = True

        app_context = sites.get_all_courses()[0]
        course = courses.Course(None, app_context=app_context)

        # Add a unit that is not available.
        unit_1 = course.add_unit()
        unit_1.now_available = False
        lesson_1_1 = course.add_lesson(unit_1)
        lesson_1_1.title = 'Lesson 1.1'
        course.update_unit(unit_1)

        # Add a unit with some lessons available and some lessons not available.
        unit_2 = course.add_unit()
        unit_2.now_available = True
        lesson_2_1 = course.add_lesson(unit_2)
        lesson_2_1.title = 'Lesson 2.1'
        lesson_2_1.now_available = False
        lesson_2_2 = course.add_lesson(unit_2)
        lesson_2_2.title = 'Lesson 2.2'
        lesson_2_2.now_available = True
        course.update_unit(unit_2)

        # Add a unit with all lessons not available.
        unit_3 = course.add_unit()
        unit_3.now_available = True
        lesson_3_1 = course.add_lesson(unit_3)
        lesson_3_1.title = 'Lesson 3.1'
        lesson_3_1.now_available = False
        course.update_unit(unit_3)

        # Add a unit that is available.
        unit_4 = course.add_unit()
        unit_4.now_available = True
        lesson_4_1 = course.add_lesson(unit_4)
        lesson_4_1.title = 'Lesson 4.1'
        lesson_4_1.now_available = True
        course.update_unit(unit_4)

        course.save()

        assert [lesson_1_1] == course.get_lessons(unit_1.unit_id)
        assert [lesson_2_1, lesson_2_2] == course.get_lessons(unit_2.unit_id)
        assert [lesson_3_1] == course.get_lessons(unit_3.unit_id)

        # Make the course available.
        get_environ_old = sites.ApplicationContext.get_environ

        def get_environ_new(self):
            environ = get_environ_old(self)
            environ['course']['now_available'] = True
            return environ

        sites.ApplicationContext.get_environ = get_environ_new

        private_tag = 'id="lesson-title-private"'

        # Simulate a student traversing the course.
        email = 'test_unit_lesson_not_available@example.com'
        name = 'Test Unit Lesson Not Available'

        actions.login(email, is_admin=False)
        actions.register(self, name)

        # Accessing a unit that is not available redirects to the main page.
        response = self.get('/test/unit?unit=%s' % unit_1.unit_id)
        assert_equals(response.status_int, 302)

        response = self.get('/test/unit?unit=%s' % unit_2.unit_id)
        assert_equals(response.status_int, 200)
        assert_contains('Lesson 2.1', response.body)
        assert_contains('This lesson is not available.', response.body)
        assert_does_not_contain(private_tag, response.body)

        response = self.get('/test/unit?unit=%s&lesson=%s' % (
            unit_2.unit_id, lesson_2_2.lesson_id))
        assert_equals(response.status_int, 200)
        assert_contains('Lesson 2.2', response.body)
        assert_does_not_contain('This lesson is not available.', response.body)
        assert_does_not_contain(private_tag, response.body)

        response = self.get('/test/unit?unit=%s' % unit_3.unit_id)
        assert_equals(response.status_int, 200)
        assert_contains('Lesson 3.1', response.body)
        assert_contains('This lesson is not available.', response.body)
        assert_does_not_contain(private_tag, response.body)

        response = self.get('/test/unit?unit=%s' % unit_4.unit_id)
        assert_equals(response.status_int, 200)
        assert_contains('Lesson 4.1', response.body)
        assert_does_not_contain('This lesson is not available.', response.body)
        assert_does_not_contain(private_tag, response.body)

        actions.logout()

        # Simulate an admin traversing the course.
        email = 'test_unit_lesson_not_available@example.com_admin'
        name = 'Test Unit Lesson Not Available Admin'

        actions.login(email, is_admin=True)
        actions.register(self, name)

        # The course admin can access a unit that is not available.
        response = self.get('/test/unit?unit=%s' % unit_1.unit_id)
        assert_equals(response.status_int, 200)
        assert_contains('Lesson 1.1', response.body)

        response = self.get('/test/unit?unit=%s' % unit_2.unit_id)
        assert_equals(response.status_int, 200)
        assert_contains('Lesson 2.1', response.body)
        assert_does_not_contain('This lesson is not available.', response.body)
        assert_contains(private_tag, response.body)

        response = self.get('/test/unit?unit=%s&lesson=%s' % (
            unit_2.unit_id, lesson_2_2.lesson_id))
        assert_equals(response.status_int, 200)
        assert_contains('Lesson 2.2', response.body)
        assert_does_not_contain('This lesson is not available.', response.body)
        assert_does_not_contain(private_tag, response.body)

        response = self.get('/test/unit?unit=%s' % unit_3.unit_id)
        assert_equals(response.status_int, 200)
        assert_contains('Lesson 3.1', response.body)
        assert_does_not_contain('This lesson is not available.', response.body)
        assert_contains(private_tag, response.body)

        response = self.get('/test/unit?unit=%s' % unit_4.unit_id)
        assert_equals(response.status_int, 200)
        assert_contains('Lesson 4.1', response.body)
        assert_does_not_contain('This lesson is not available.', response.body)
        assert_does_not_contain(private_tag, response.body)

        actions.logout()

        # Clean up app_context.
        sites.ApplicationContext.get_environ = get_environ_old

    def test_custom_assessments(self):
        """Tests that custom assessments are evaluated correctly."""

        # Setup a new course.
        sites.setup_courses('course:/test::ns_test, course:/:/')
        config.Registry.test_overrides[
            models.CAN_USE_MEMCACHE.name] = True

        app_context = sites.get_all_courses()[0]
        course = courses.Course(None, app_context=app_context)

        email = 'test_assessments@google.com'
        name = 'Test Assessments'

        assessment_1 = course.add_assessment()
        assessment_1.title = 'first'
        assessment_1.now_available = True
        assessment_1.weight = 0
        assessment_2 = course.add_assessment()
        assessment_2.title = 'second'
        assessment_2.now_available = True
        assessment_2.weight = 0
        course.save()
        assert course.find_unit_by_id(assessment_1.unit_id)
        assert course.find_unit_by_id(assessment_2.unit_id)
        assert 2 == len(course.get_units())

        # Make the course available.
        get_environ_old = sites.ApplicationContext.get_environ

        def get_environ_new(self):
            environ = get_environ_old(self)
            environ['course']['now_available'] = True
            return environ

        sites.ApplicationContext.get_environ = get_environ_new

        first = {'score': '1.00', 'assessment_type': assessment_1.unit_id}
        second = {'score': '3.00', 'assessment_type': assessment_2.unit_id}

        # Update assessment 1.
        assessment_1_content = open(os.path.join(
            appengine_config.BUNDLE_ROOT,
            'assets/js/assessment-Pre.js'), 'rb').readlines()
        assessment_1_content = u''.join(assessment_1_content)
        errors = []
        course.set_assessment_content(
            assessment_1, assessment_1_content, errors)
        course.save()
        assert not errors

        # Update assessment 2.
        assessment_2_content = open(os.path.join(
            appengine_config.BUNDLE_ROOT,
            'assets/js/assessment-Mid.js'), 'rb').readlines()
        assessment_2_content = u''.join(assessment_2_content)
        errors = []
        course.set_assessment_content(
            assessment_2, assessment_2_content, errors)
        course.save()
        assert not errors

        # Register.
        actions.login(email)
        actions.register(self, name)

        # Submit assessment 1.
        actions.submit_assessment(
            self, assessment_1.unit_id, first, base='/test')
        student = models.Student.get_enrolled_student_by_email(email)
        student_scores = course.get_all_scores(student)

        assert len(student_scores) == 2

        assert student_scores[0]['id'] == str(assessment_1.unit_id)
        assert student_scores[0]['score'] == 1
        assert student_scores[0]['title'] == 'first'
        assert student_scores[0]['weight'] == 0

        assert student_scores[1]['id'] == str(assessment_2.unit_id)
        assert student_scores[1]['score'] == 0
        assert student_scores[1]['title'] == 'second'
        assert student_scores[1]['weight'] == 0

        # The overall score is None if there are no weights assigned to any of
        # the assessments.
        overall_score = course.get_overall_score(student)
        assert overall_score is None

        # View the student profile page.
        response = self.get('/test/student/home')
        assert_does_not_contain('Overall course score', response.body)

        # Add a weight to the first assessment.
        assessment_1.weight = 10
        overall_score = course.get_overall_score(student)
        assert overall_score == 1

        # Submit assessment 2.
        actions.submit_assessment(
            self, assessment_2.unit_id, second, base='/test')
        # We need to reload the student instance, because its properties have
        # changed.
        student = models.Student.get_enrolled_student_by_email(email)
        student_scores = course.get_all_scores(student)

        assert len(student_scores) == 2
        assert student_scores[1]['score'] == 3
        overall_score = course.get_overall_score(student)
        assert overall_score == 1

        # Change the weight of assessment 2.
        assessment_2.weight = 30
        overall_score = course.get_overall_score(student)
        assert overall_score == int((1 * 10 + 3 * 30) / 40)

        # Save all changes.
        course.save()

        # View the student profile page.
        response = self.get('/test/student/home')
        assert_contains('assessment-score-first">1</span>', response.body)
        assert_contains('assessment-score-second">3</span>', response.body)
        assert_contains('Overall course score', response.body)
        assert_contains('assessment-score-overall">2</span>', response.body)

        # Submitting a lower score for any assessment does not change any of
        # the scores, since the system records the maximum score that has ever
        # been achieved on any assessment.
        first_retry = {'score': '0', 'assessment_type': assessment_1.unit_id}
        actions.submit_assessment(
            self, assessment_1.unit_id, first_retry, base='/test')
        student = models.Student.get_enrolled_student_by_email(email)
        student_scores = course.get_all_scores(student)

        assert len(student_scores) == 2
        assert student_scores[0]['id'] == str(assessment_1.unit_id)
        assert student_scores[0]['score'] == 1

        overall_score = course.get_overall_score(student)
        assert overall_score == int((1 * 10 + 3 * 30) / 40)

        actions.logout()

        # Clean up app_context.
        sites.ApplicationContext.get_environ = get_environ_old

    def test_datastore_backed_file_system(self):
        """Tests datastore-backed file system operations."""
        fs = vfs.AbstractFileSystem(vfs.DatastoreBackedFileSystem('', '/'))

        # Check binary file.
        src = os.path.join(appengine_config.BUNDLE_ROOT, 'course.yaml')
        dst = os.path.join('/', 'course.yaml')

        fs.put(dst, open(src, 'rb'))
        stored = fs.open(dst)
        assert stored.metadata.size == len(open(src, 'rb').read())
        assert not stored.metadata.is_draft
        assert stored.read() == open(src, 'rb').read()

        # Check draft.
        fs.put(dst, open(src, 'rb'), is_draft=True)
        stored = fs.open(dst)
        assert stored.metadata.is_draft

        # Check text files with non-ASCII characters and encoding.
        foo_js = os.path.join('/', 'assets/js/foo.js')
        foo_text = u'This is a test text (тест данные).'
        fs.put(foo_js, vfs.string_to_stream(foo_text))
        stored = fs.open(foo_js)
        assert vfs.stream_to_string(stored) == foo_text

        # Check delete.
        del_file = os.path.join('/', 'memcache.test')
        fs.put(del_file, vfs.string_to_stream(u'test'))
        assert fs.isfile(del_file)
        fs.delete(del_file)
        assert not fs.isfile(del_file)

        # Check open or delete of non-existent does not fail.
        assert not fs.open('/foo/bar/baz')
        assert not fs.delete('/foo/bar/baz')

        # Check new content fully overrides old (with and without memcache).
        test_file = os.path.join('/', 'memcache.test')
        fs.put(test_file, vfs.string_to_stream(u'test text'))
        stored = fs.open(test_file)
        assert u'test text' == vfs.stream_to_string(stored)
        fs.delete(test_file)

        # Check file existence.
        assert not fs.isfile('/foo/bar')
        assert fs.isfile('/course.yaml')
        assert fs.isfile('/assets/js/foo.js')

        # Check file listing.
        bar_js = os.path.join('/', 'assets/js/bar.js')
        fs.put(bar_js, vfs.string_to_stream(foo_text))
        baz_js = os.path.join('/', 'assets/js/baz.js')
        fs.put(baz_js, vfs.string_to_stream(foo_text))
        assert fs.list('/') == sorted([
            u'/course.yaml',
            u'/assets/js/foo.js', u'/assets/js/bar.js', u'/assets/js/baz.js'])
        assert fs.list('/assets') == sorted([
            u'/assets/js/foo.js', u'/assets/js/bar.js', u'/assets/js/baz.js'])
        assert not fs.list('/foo/bar')

    def test_utf8_datastore(self):
        """Test writing to and reading from datastore using UTF-8 content."""
        event = models.EventEntity()
        event.source = 'test-source'
        event.user_id = 'test-user-id'
        event.data = u'Test Data (тест данные)'
        event.put()

        stored_event = models.EventEntity().get_by_id([event.key().id()])
        assert 1 == len(stored_event)
        assert event.data == stored_event[0].data

    def assert_queriable(self, entity, name, date_type=datetime.datetime):
        """Create some entities and check that single-property queries work."""
        for i in range(1, 32):
            item = entity(
                key_name='%s_%s' % (date_type.__class__.__name__, i))
            setattr(item, name, date_type(2012, 1, i))
            item.put()

        # Descending order.
        items = entity.all().order('-%s' % name).fetch(1000)
        assert len(items) == 31
        assert getattr(items[0], name) == date_type(2012, 1, 31)

        # Ascending order.
        items = entity.all().order('%s' % name).fetch(1000)
        assert len(items) == 31
        assert getattr(items[0], name) == date_type(2012, 1, 1)

    def test_indexed_properties(self):
        """Test whether entities support specific query types."""

        # A 'DateProperty' or 'DateTimeProperty' of each persistent entity must
        # be indexed. This is true even if the application doesn't execute any
        # queries relying on the index. The index is still critically important
        # for managing data, for example, for bulk data download or for
        # incremental computations. Using index, the entire table can be
        # processed in daily, weekly, etc. chunks and it is easy to query for
        # new data. If we did not have an index, chunking would have to be done
        # by the primary index, where it is impossible to separate recently
        # added/modified rows from the rest of the data. Having this index adds
        # to the cost of datastore writes, but we believe it is important to
        # have it. Below we check that all persistent date/datetime properties
        # are indexed.

        self.assert_queriable(AnnouncementEntity, 'date', datetime.date)
        self.assert_queriable(models.EventEntity, 'recorded_on')
        self.assert_queriable(models.Student, 'enrolled_on')
        self.assert_queriable(models.StudentAnswersEntity, 'updated_on')
        self.assert_queriable(jobs.DurableJobEntity, 'updated_on')

    def test_assets_and_date(self):
        """Verify semantics of all asset and data files."""

        def echo(unused_message):
            pass

        warnings, errors = verify.Verifier().load_and_verify_model(echo)
        assert not errors and not warnings

    def test_config_visible_from_any_namespace(self):
        """Test that ConfigProperty is visible from any namespace."""

        assert (
            config.UPDATE_INTERVAL_SEC.value ==
            config.UPDATE_INTERVAL_SEC.default_value)
        new_value = config.UPDATE_INTERVAL_SEC.default_value + 5

        # Add datastore override for known property.
        prop = config.ConfigPropertyEntity(
            key_name=config.UPDATE_INTERVAL_SEC.name)
        prop.value = str(new_value)
        prop.is_draft = False
        prop.put()

        # Check visible from default namespace.
        config.Registry.last_update_time = 0
        assert config.UPDATE_INTERVAL_SEC.value == new_value

        # Check visible from another namespace.
        old_namespace = namespace_manager.get_namespace()
        try:
            namespace_manager.set_namespace(
                'ns-test_config_visible_from_any_namespace')

            config.Registry.last_update_time = 0
            assert config.UPDATE_INTERVAL_SEC.value == new_value
        finally:
            namespace_manager.set_namespace(old_namespace)


class AdminAspectTest(actions.TestBase):
    """Test site from the Admin perspective."""

    def test_courses_page_for_multiple_courses(self):
        """Tests /admin page showing multiple courses."""
        # Setup courses.
        sites.setup_courses('course:/aaa::ns_a, course:/bbb::ns_b, course:/:/')
        config.Registry.test_overrides[
            models.CAN_USE_MEMCACHE.name] = True

        # Validate the courses before import.
        all_courses = sites.get_all_courses()
        dst_app_context_a = all_courses[0]
        dst_app_context_b = all_courses[1]
        src_app_context = all_courses[2]

        # This test requires a read-write file system. If test runs on read-
        # only one, we can't run this test :(
        if (not dst_app_context_a.fs.is_read_write() or
            not dst_app_context_a.fs.is_read_write()):
            return

        course_a = courses.Course(None, app_context=dst_app_context_a)
        course_b = courses.Course(None, app_context=dst_app_context_b)

        unused_course, course_a = course_a.import_from(src_app_context)
        unused_course, course_b = course_b.import_from(src_app_context)

        # Rename courses.
        dst_app_context_a.fs.put(
            dst_app_context_a.get_config_filename(),
            vfs.string_to_stream(u'course:\n  title: \'Course AAA\''))
        dst_app_context_b.fs.put(
            dst_app_context_b.get_config_filename(),
            vfs.string_to_stream(u'course:\n  title: \'Course BBB\''))

        # Login.
        email = 'test_courses_page_for_multiple_courses@google.com'
        actions.login(email, True)

        # Check the course listing page.
        response = self.testapp.get('/admin')
        assert_contains_all_of([
            'Course AAA',
            '/aaa/dashboard',
            'Course BBB',
            '/bbb/dashboard'], response.body)

        # Clean up.
        sites.reset_courses()

    def test_python_console(self):
        """Test access rights to the Python console."""

        email = 'test_python_console@google.com'

        # The default is that the console should be turned off
        self.assertFalse(modules.admin.admin.DIRECT_CODE_EXECUTION_UI_ENABLED)

        # Test the console when it is enabled
        modules.admin.admin.DIRECT_CODE_EXECUTION_UI_ENABLED = True

        # Check normal user has no access.
        actions.login(email)
        response = self.testapp.get('/admin?action=console')
        assert_equals(response.status_int, 302)

        response = self.testapp.post('/admin?action=console')
        assert_equals(response.status_int, 302)

        # Check delegated admin has no access.
        os.environ['gcb_admin_user_emails'] = '[%s]' % email
        actions.login(email)
        response = self.testapp.get('/admin?action=console')
        assert_equals(response.status_int, 200)
        assert_contains(
            'You must be an actual admin user to continue.', response.body)

        response = self.testapp.get('/admin?action=console')
        assert_equals(response.status_int, 200)
        assert_contains(
            'You must be an actual admin user to continue.', response.body)

        del os.environ['gcb_admin_user_emails']

        # Check actual admin has access.
        actions.login(email, True)
        response = self.testapp.get('/admin?action=console')
        assert_equals(response.status_int, 200)

        response.form.set('code', 'print "foo" + "bar"')
        response = self.submit(response.form)
        assert_contains('foobar', response.body)

        # Finally, test that the console is not found when it is disabled
        modules.admin.admin.DIRECT_CODE_EXECUTION_UI_ENABLED = False

        actions.login(email, True)
        self.testapp.get('/admin?action=console', status=404)
        self.testapp.post('/admin?action=console_run', status=404)

    def test_non_admin_has_no_access(self):
        """Test non admin has no access to pages or REST endpoints."""

        email = 'test_non_admin_has_no_access@google.com'
        actions.login(email)

        # Add datastore override.
        prop = config.ConfigPropertyEntity(
            key_name='gcb_config_update_interval_sec')
        prop.value = '5'
        prop.is_draft = False
        prop.put()

        # Check user has no access to specific pages and actions.
        response = self.testapp.get('/admin?action=settings')
        assert_equals(response.status_int, 302)

        response = self.testapp.get(
            '/admin?action=config_edit&name=gcb_admin_user_emails')
        assert_equals(response.status_int, 302)

        response = self.testapp.post(
            '/admin?action=config_reset&name=gcb_admin_user_emails')
        assert_equals(response.status_int, 302)

        # Check user has no rights to GET verb.
        response = self.testapp.get(
            '/rest/config/item?key=gcb_config_update_interval_sec')
        assert_equals(response.status_int, 200)
        json_dict = transforms.loads(response.body)
        assert json_dict['status'] == 401
        assert json_dict['message'] == 'Access denied.'

        # Here are the endpoints we want to test: (uri, xsrf_action_name).
        endpoints = [
            ('/rest/config/item', 'config-property-put'),
            ('/rest/courses/item', 'add-course-put')]

        # Check user has no rights to PUT verb.
        payload_dict = {}
        payload_dict['value'] = '666'
        payload_dict['is_draft'] = False
        request = {}
        request['key'] = 'gcb_config_update_interval_sec'
        request['payload'] = transforms.dumps(payload_dict)

        for uri, unused_action in endpoints:
            response = self.testapp.put(uri + '?%s' % urllib.urlencode(
                {'request': transforms.dumps(request)}), {})
            assert_equals(response.status_int, 200)
            assert_contains('"status": 403', response.body)

        # Check user still has no rights to PUT verb even if he somehow
        # obtained a valid XSRF token.
        for uri, action in endpoints:
            request['xsrf_token'] = XsrfTokenManager.create_xsrf_token(action)
            response = self.testapp.put(uri + '?%s' % urllib.urlencode(
                {'request': transforms.dumps(request)}), {})
            assert_equals(response.status_int, 200)
            json_dict = transforms.loads(response.body)
            assert json_dict['status'] == 401
            assert json_dict['message'] == 'Access denied.'

    def test_admin_list(self):
        """Test delegation of admin access to another user."""

        email = 'test_admin_list@google.com'
        actions.login(email)

        # Add environment variable override.
        os.environ['gcb_admin_user_emails'] = '[%s]' % email

        # Add datastore override.
        prop = config.ConfigPropertyEntity(
            key_name='gcb_config_update_interval_sec')
        prop.value = '5'
        prop.is_draft = False
        prop.put()

        # Check user has access now.
        response = self.testapp.get('/admin?action=settings')
        assert_equals(response.status_int, 200)

        # Check overrides are active and have proper management actions.
        assert_contains('gcb_admin_user_emails', response.body)
        assert_contains('[test_admin_list@google.com]', response.body)
        assert_contains(
            '/admin?action=config_override&name=gcb_admin_user_emails',
            response.body)
        assert_contains(
            '/admin?action=config_edit&name=gcb_config_update_interval_sec',
            response.body)

        # Check editor page has proper actions.
        response = self.testapp.get(
            '/admin?action=config_edit&name=gcb_config_update_interval_sec')
        assert_equals(response.status_int, 200)
        assert_contains('/admin?action=config_reset', response.body)
        assert_contains('name=gcb_config_update_interval_sec', response.body)

        # Remove override.
        del os.environ['gcb_admin_user_emails']

        # Check user has no access.
        response = self.testapp.get('/admin?action=settings')
        assert_equals(response.status_int, 302)

    def test_access_to_admin_pages(self):
        """Test access to admin pages."""

        # assert anonymous user has no access
        response = self.testapp.get('/admin?action=settings')
        assert_equals(response.status_int, 302)

        # assert admin user has access
        email = 'test_access_to_admin_pages@google.com'
        name = 'Test Access to Admin Pages'

        actions.login(email, True)
        actions.register(self, name)

        response = self.testapp.get('/admin')
        assert_contains('Power Searching with Google', response.body)
        assert_contains('All Courses', response.body)

        response = self.testapp.get('/admin?action=settings')
        assert_contains('gcb_admin_user_emails', response.body)
        assert_contains('gcb_config_update_interval_sec', response.body)
        assert_contains('All Settings', response.body)

        response = self.testapp.get('/admin?action=perf')
        assert_contains('gcb-admin-uptime-sec:', response.body)
        assert_contains('In-process Performance Counters', response.body)

        response = self.testapp.get('/admin?action=deployment')
        assert_contains('application_id: testbed-test', response.body)
        assert_contains('About the Application', response.body)

        actions.unregister(self)
        actions.logout()

        # assert not-admin user has no access
        actions.login(email)
        actions.register(self, name)
        response = self.testapp.get('/admin?action=settings')
        assert_equals(response.status_int, 302)

    def test_multiple_courses(self):
        """Test courses admin page with two courses configured."""

        sites.setup_courses(
            'course:/foo:/foo-data, course:/bar:/bar-data:nsbar')

        email = 'test_multiple_courses@google.com'

        actions.login(email, True)
        response = self.testapp.get('/admin')
        assert_contains('Course Builder &gt; Admin &gt; Courses', response.body)
        assert_contains('Total: 2 item(s)', response.body)

        # Check ocurse URL's.
        assert_contains('<a href="/foo/dashboard">', response.body)
        assert_contains('<a href="/bar/dashboard">', response.body)

        # Check content locations.
        assert_contains('/foo-data', response.body)
        assert_contains('/bar-data', response.body)

        # Check namespaces.
        assert_contains('gcb-course-foo-data', response.body)
        assert_contains('nsbar', response.body)

        # Clean up.
        sites.reset_courses()

    def test_add_course(self):
        """Tests adding a new course entry."""

        if not self.supports_editing:
            return

        email = 'test_add_course@google.com'
        actions.login(email, True)

        # Prepare request data.
        payload_dict = {
            'name': 'add_new',
            'title': u'new course (тест данные)', 'admin_email': 'foo@bar.com'}
        request = {}
        request['payload'] = transforms.dumps(payload_dict)
        request['xsrf_token'] = XsrfTokenManager.create_xsrf_token(
            'add-course-put')

        # Execute action.
        response = self.testapp.put('/rest/courses/item?%s' % urllib.urlencode(
            {'request': transforms.dumps(request)}), {})
        assert_equals(response.status_int, 200)

        # Check response.
        json_dict = transforms.loads(transforms.loads(response.body)['payload'])
        assert 'course:/add_new::ns_add_new' == json_dict.get('entry')

        # Re-execute action; should fail as this would create a duplicate.
        response = self.testapp.put('/rest/courses/item?%s' % urllib.urlencode(
            {'request': transforms.dumps(request)}), {})
        assert_equals(response.status_int, 200)
        assert_equals(412, transforms.loads(response.body)['status'])

        # Load the course and check its title.
        new_app_context = sites.get_all_courses(
            'course:/add_new::ns_add_new')[0]
        assert_equals(u'new course (тест данные)', new_app_context.get_title())
        new_course = courses.Course(None, app_context=new_app_context)
        assert not new_course.get_units()


class CourseAuthorAspectTest(actions.TestBase):
    """Tests the site from the Course Author perspective."""

    def test_dashboard(self):
        """Test course dashboard."""

        email = 'test_dashboard@google.com'
        name = 'Test Dashboard'

        # Non-admin does't have access.
        actions.login(email)
        response = self.get('dashboard')
        assert_equals(response.status_int, 302)

        actions.register(self, name)
        assert_equals(response.status_int, 302)
        actions.logout()

        # Admin has access.
        actions.login(email, True)
        response = self.get('dashboard')
        assert_contains('Google &gt; Dashboard &gt; Outline', response.body)

        # Tests outline view.
        response = self.get('dashboard')
        assert_contains('Unit 3 - Advanced techniques', response.body)
        assert_contains('data/lesson.csv', response.body)

        # Check editability.
        if self.supports_editing:
            assert_contains('Add Assessment', response.body)
        else:
            assert_does_not_contain('Add Assessment', response.body)

        # Test assets view.
        response = self.get('dashboard?action=assets')
        assert_contains('Google &gt; Dashboard &gt; Assets', response.body)
        assert_contains('assets/css/main.css', response.body)
        assert_contains('assets/img/Image1.5.png', response.body)
        assert_contains('assets/js/activity-3.2.js', response.body)

        # Test settings view.
        response = self.get('dashboard?action=settings')
        assert_contains(
            'Google &gt; Dashboard &gt; Settings', response.body)
        assert_contains('course.yaml', response.body)
        assert_contains(
            'title: \'Power Searching with Google\'', response.body)
        assert_contains('locale: \'en_US\'', response.body)

        # Check editability.
        if self.supports_editing:
            assert_contains('create_or_edit_settings', response.body)
        else:
            assert_does_not_contain('create_or_edit_settings', response.body)

        # Tests student statistics view.
        response = self.get('dashboard?action=students')
        assert_contains(
            'Google &gt; Dashboard &gt; Students', response.body)
        assert_contains('have not been calculated yet', response.body)

        compute_form = response.forms['gcb-compute-student-stats']
        response = self.submit(compute_form)
        assert_equals(response.status_int, 302)
        assert len(self.taskq.GetTasks('default')) == 1

        response = self.get('dashboard?action=students')
        assert_contains('is running', response.body)

        self.execute_all_deferred_tasks()

        response = self.get('dashboard?action=students')
        assert_contains('were last updated on', response.body)
        assert_contains('currently enrolled: 1', response.body)
        assert_contains('total: 1', response.body)

        # Tests assessment statistics.
        old_namespace = namespace_manager.get_namespace()
        namespace_manager.set_namespace(self.namespace)
        try:
            for i in range(5):
                student = models.Student(key_name='key-%s' % i)
                student.is_enrolled = True
                student.scores = transforms.dumps({'test-assessment': i})
                student.put()
        finally:
            namespace_manager.set_namespace(old_namespace)

        response = self.get('dashboard?action=students')
        compute_form = response.forms['gcb-compute-student-stats']
        response = self.submit(compute_form)

        self.execute_all_deferred_tasks()

        response = self.get('dashboard?action=students')
        assert_contains('currently enrolled: 6', response.body)
        assert_contains(
            'test-assessment: completed 5, average score 2.0', response.body)

    def test_trigger_sample_announcements(self):
        """Test course author can trigger adding sample announcements."""
        email = 'test_announcements@google.com'
        name = 'Test Announcements'

        actions.login(email, True)
        actions.register(self, name)

        response = actions.view_announcements(self)
        assert_contains('Example Announcement', response.body)
        assert_contains('Welcome to the final class!', response.body)
        assert_does_not_contain('No announcements yet.', response.body)

    def test_manage_announcements(self):
        """Test course author can manage announcements."""
        email = 'test_announcements@google.com'
        name = 'Test Announcements'

        actions.login(email, True)
        actions.register(self, name)

        # add new
        response = actions.view_announcements(self)
        add_form = response.forms['gcb-add-announcement']
        response = self.submit(add_form)
        assert_equals(response.status_int, 302)

        # check edit form rendering
        response = self.testapp.get(response.location)
        assert_equals(response.status_int, 200)
        assert_contains('/rest/announcements/item?key=', response.body)

        # check added
        response = actions.view_announcements(self)
        assert_contains('Sample Announcement (Draft)', response.body)

        # delete draft
        response = actions.view_announcements(self)
        delete_form = response.forms['gcb-delete-announcement-1']
        response = self.submit(delete_form)
        assert_equals(response.status_int, 302)

        # check deleted
        assert_does_not_contain('Welcome to the final class!', response.body)

    def test_announcements_rest(self):
        """Test REST access to announcements."""
        email = 'test_announcements_rest@google.com'
        name = 'Test Announcements Rest'

        actions.login(email, True)
        actions.register(self, name)

        response = actions.view_announcements(self)
        assert_does_not_contain('My Test Title', response.body)

        # REST GET existing item
        items = AnnouncementEntity.all().fetch(1)
        for item in items:
            response = self.get('rest/announcements/item?key=%s' % item.key())
            json_dict = transforms.loads(response.body)
            assert json_dict['status'] == 200
            assert 'message' in json_dict
            assert 'payload' in json_dict

            payload_dict = transforms.loads(json_dict['payload'])
            assert 'title' in payload_dict
            assert 'date' in payload_dict

            # REST PUT item
            payload_dict['title'] = u'My Test Title Мой заголовок теста'
            payload_dict['date'] = '2012/12/31'
            payload_dict['is_draft'] = True
            request = {}
            request['key'] = str(item.key())
            request['payload'] = transforms.dumps(payload_dict)

            # Check XSRF is required.
            response = self.put('rest/announcements/item?%s' % urllib.urlencode(
                {'request': transforms.dumps(request)}), {})
            assert_equals(response.status_int, 200)
            assert_contains('"status": 403', response.body)

            # Check PUT works.
            request['xsrf_token'] = json_dict['xsrf_token']
            response = self.put('rest/announcements/item?%s' % urllib.urlencode(
                {'request': transforms.dumps(request)}), {})
            assert_equals(response.status_int, 200)
            assert_contains('"status": 200', response.body)

            # Confirm change is visible on the page.
            response = self.get('announcements')
            assert_contains(
                u'My Test Title Мой заголовок теста (Draft)', response.body)

        # REST GET not-existing item
        response = self.get('rest/announcements/item?key=not_existent_key')
        json_dict = transforms.loads(response.body)
        assert json_dict['status'] == 404


class StudentAspectTest(actions.TestBase):
    """Test the site from the Student perspective."""

    def test_view_announcements(self):
        """Test student aspect of announcements."""

        email = 'test_announcements@google.com'
        name = 'Test Announcements'

        actions.login(email)
        actions.register(self, name)

        # Check no announcements yet.
        response = actions.view_announcements(self)
        assert_does_not_contain('Example Announcement', response.body)
        assert_does_not_contain('Welcome to the final class!', response.body)
        assert_contains('No announcements yet.', response.body)
        actions.logout()

        # Login as admin and add announcements.
        actions.login('admin@sample.com', True)
        actions.register(self, 'admin')
        response = actions.view_announcements(self)
        actions.logout()

        # Check we can see non-draft announcements.
        actions.login(email)
        response = actions.view_announcements(self)
        assert_contains('Example Announcement', response.body)
        assert_does_not_contain('Welcome to the final class!', response.body)
        assert_does_not_contain('No announcements yet.', response.body)

        # Check no access to access to draft announcements via REST handler.
        items = AnnouncementEntity.all().fetch(1000)
        for item in items:
            response = self.get('rest/announcements/item?key=%s' % item.key())
            if item.is_draft:
                json_dict = transforms.loads(response.body)
                assert json_dict['status'] == 401
            else:
                assert_equals(response.status_int, 200)

    def test_registration(self):
        """Test student registration."""
        email = 'test_registration@example.com'
        name1 = 'Test Student'
        name2 = 'John Smith'
        name3 = u'Pavel Simakov (тест данные)'

        actions.login(email)

        actions.register(self, name1)
        actions.check_profile(self, name1)

        actions.change_name(self, name2)
        actions.unregister(self)

        actions.register(self, name3)
        actions.check_profile(self, name3)

    def test_course_not_available(self):
        """Tests course is only accessible to author when incomplete."""

        email = 'test_course_not_available@example.com'
        name = 'Test Course Not Available'

        actions.login(email)
        actions.register(self, name)

        # Check preview and static resources are available.
        response = self.get('course')
        assert_equals(response.status_int, 200)
        response = self.get('assets/js/activity-1.4.js')
        assert_equals(response.status_int, 200)

        # Override course.yaml settings by patching app_context.
        get_environ_old = sites.ApplicationContext.get_environ

        def get_environ_new(self):
            environ = get_environ_old(self)
            environ['course']['now_available'] = False
            return environ

        sites.ApplicationContext.get_environ = get_environ_new

        # Check preview and static resources are not available to Student.
        response = self.get('course', expect_errors=True)
        assert_equals(response.status_int, 404)
        response = self.get('assets/js/activity-1.4.js', expect_errors=True)
        assert_equals(response.status_int, 404)

        # Check preview and static resources are still available to author.
        actions.login(email, True)
        response = self.get('course')
        assert_equals(response.status_int, 200)
        response = self.get('assets/js/activity-1.4.js')
        assert_equals(response.status_int, 200)

        # Clean up app_context.
        sites.ApplicationContext.get_environ = get_environ_old

    def test_registration_closed(self):
        """Test student registration when course is full."""

        email = 'test_registration_closed@example.com'
        name = 'Test Registration Closed'

        # Override course.yaml settings by patching app_context.
        get_environ_old = sites.ApplicationContext.get_environ

        def get_environ_new(self):
            environ = get_environ_old(self)
            environ['reg_form']['can_register'] = False
            return environ

        sites.ApplicationContext.get_environ = get_environ_new

        # Try to login and register.
        actions.login(email)
        try:
            actions.register(self, name)
            raise actions.ShouldHaveFailedByNow(
                'Expected to fail: new registrations should not be allowed '
                'when registration is closed.')
        except actions.ShouldHaveFailedByNow as e:
            raise e
        except:
            pass

        # Clean up app_context.
        sites.ApplicationContext.get_environ = get_environ_old

    def test_permissions(self):
        """Test student permissions, and which pages they can view."""
        email = 'test_permissions@example.com'
        name = 'Test Permissions'

        actions.login(email)

        actions.register(self, name)
        actions.Permissions.assert_enrolled(self)

        actions.unregister(self)
        actions.Permissions.assert_unenrolled(self)

        actions.register(self, name)
        actions.Permissions.assert_enrolled(self)

    def test_login_and_logout(self):
        """Test if login and logout behave as expected."""
        email = 'test_login_logout@example.com'

        actions.Permissions.assert_logged_out(self)
        actions.login(email)

        actions.Permissions.assert_unenrolled(self)

        actions.logout()
        actions.Permissions.assert_logged_out(self)

    def test_lesson_activity_navigation(self):
        """Test navigation between lesson/activity pages."""

        email = 'test_lesson_activity_navigation@example.com'
        name = 'Test Lesson Activity Navigation'

        actions.login(email)
        actions.register(self, name)

        response = self.get('unit?unit=1&lesson=1')
        assert_does_not_contain('Previous Page', response.body)
        assert_contains('Next Page', response.body)

        response = self.get('unit?unit=2&lesson=3')
        assert_contains('Previous Page', response.body)
        assert_contains('Next Page', response.body)

        response = self.get('unit?unit=3&lesson=5')
        assert_contains('Previous Page', response.body)
        assert_does_not_contain('Next Page', response.body)
        assert_contains('End', response.body)

    def test_attempt_activity_event(self):
        """Test activity attempt generates event."""

        email = 'test_attempt_activity_event@example.com'
        name = 'Test Attempt Activity Event'

        actions.login(email)
        actions.register(self, name)

        # Enable event recording.
        config.Registry.test_overrides[
            lessons.CAN_PERSIST_ACTIVITY_EVENTS.name] = True

        # Prepare event.
        request = {}
        request['source'] = 'test-source'
        request['payload'] = transforms.dumps({'Alice': u'Bob (тест данные)'})

        # Check XSRF token is required.
        response = self.post('rest/events?%s' % urllib.urlencode(
            {'request': transforms.dumps(request)}), {})
        assert_equals(response.status_int, 200)
        assert_contains('"status": 403', response.body)

        # Check PUT works.
        request['xsrf_token'] = XsrfTokenManager.create_xsrf_token(
            'event-post')
        response = self.post('rest/events?%s' % urllib.urlencode(
            {'request': transforms.dumps(request)}), {})
        assert_equals(response.status_int, 200)
        assert not response.body

        # Check event is properly recorded.
        old_namespace = namespace_manager.get_namespace()
        namespace_manager.set_namespace(self.namespace)
        try:
            events = models.EventEntity.all().fetch(1000)
            assert 1 == len(events)
            assert_contains(
                u'Bob (тест данные)',
                transforms.loads(events[0].data)['Alice'])
        finally:
            namespace_manager.set_namespace(old_namespace)

        # Clean up.
        config.Registry.test_overrides = {}

    def test_two_students_dont_see_each_other_pages(self):
        """Test a user can't see another user pages."""
        email1 = 'user1@foo.com'
        name1 = 'User 1'
        email2 = 'user2@foo.com'
        name2 = 'User 2'

        # Login as one user and view 'unit' and other pages, which are not
        # cached.
        actions.login(email1)
        actions.register(self, name1)
        actions.Permissions.assert_enrolled(self)
        response = actions.view_unit(self)
        assert_contains(email1, response.body)
        actions.logout()

        # Login as another user and check that 'unit' and other pages show
        # the correct new email.
        actions.login(email2)
        actions.register(self, name2)
        actions.Permissions.assert_enrolled(self)
        response = actions.view_unit(self)
        assert_contains(email2, response.body)
        actions.logout()

    def test_xsrf_defence(self):
        """Test defense against XSRF attack."""

        email = 'test_xsrf_defence@example.com'
        name = 'Test Xsrf Defence'

        actions.login(email)
        actions.register(self, name)

        response = self.get('student/home')
        response.form.set('name', 'My New Name')
        response.form.set('xsrf_token', 'bad token')

        response = response.form.submit(expect_errors=True)
        assert_equals(response.status_int, 403)

    def test_response_headers(self):
        """Test dynamically-generated responses use proper headers."""

        email = 'test_response_headers@example.com'
        name = 'Test Response Headers'

        actions.login(email)
        actions.register(self, name)

        response = self.get('student/home')
        assert_equals(response.status_int, 200)
        assert_contains('must-revalidate', response.headers['Cache-Control'])
        assert_contains('no-cache', response.headers['Cache-Control'])
        assert_contains('no-cache', response.headers['Pragma'])
        assert_contains('Mon, 01 Jan 1990', response.headers['Expires'])


class StaticHandlerTest(actions.TestBase):
    """Check serving of static resources."""

    def test_static_files_cache_control(self):
        """Test static/zip handlers use proper Cache-Control headers."""

        # Check static handler.
        response = self.get('/assets/css/main.css')
        assert_equals(response.status_int, 200)
        assert_contains('max-age=600', response.headers['Cache-Control'])
        assert_contains('public', response.headers['Cache-Control'])
        assert_does_not_contain('no-cache', response.headers['Cache-Control'])

        # Check zip file handler.
        response = self.testapp.get(
            '/static/inputex-3.1.0/src/inputex/assets/skins/sam/inputex.css')
        assert_equals(response.status_int, 200)
        assert_contains('max-age=600', response.headers['Cache-Control'])
        assert_contains('public', response.headers['Cache-Control'])
        assert_does_not_contain('no-cache', response.headers['Cache-Control'])


class ActivityTest(actions.TestBase):
    """Test for activities."""

    def get_activity(self, unit_id, lesson_id, args):
        """Retrieve the activity page for a given unit and lesson id."""

        response = self.get('activity?unit=%s&lesson=%s' % (unit_id, lesson_id))
        assert_equals(response.status_int, 200)
        assert_contains(
            '<script src="assets/lib/activity-generic-%s.%s.js"></script>' %
            (unit_id, lesson_id), response.body)

        js_response = self.get('assets/lib/activity-generic-1.2.js')
        assert_equals(js_response.status_int, 200)

        # Extract XSRF token from the page.
        match = re.search(r'eventXsrfToken = [\']([^\']+)', response.body)
        assert match
        xsrf_token = match.group(1)
        args['xsrf_token'] = xsrf_token

        return response, args

    def test_activities(self):
        """Test that activity submissions are handled and recorded correctly."""

        email = 'test_activities@google.com'
        name = 'Test Activities'
        unit_id = 1
        lesson_id = 2
        activity_submissions = {
            '1.2': {
                'index': 3,
                'type': 'activity-choice',
                'value': 3,
                'correct': True,
            },
        }

        # Register.
        actions.login(email)
        actions.register(self, name)

        # Enable event recording.
        config.Registry.test_overrides[
            lessons.CAN_PERSIST_ACTIVITY_EVENTS.name] = True

        # Navigate to the course overview page, and check that the unit shows
        # no progress yet.
        response = self.get('course')
        assert_equals(response.status_int, 200)
        assert_contains(u'id="progress-notstarted-%s"' % unit_id, response.body)

        old_namespace = namespace_manager.get_namespace()
        namespace_manager.set_namespace(self.namespace)
        try:
            response, args = self.get_activity(unit_id, lesson_id, {})

            # Check that the current activity shows no progress yet.
            assert_contains(
                u'id="progress-notstarted-%s"' % lesson_id, response.body)

            # Prepare activity submission event.
            args['source'] = 'attempt-activity'
            lesson_key = '%s.%s' % (unit_id, lesson_id)
            assert lesson_key in activity_submissions
            args['payload'] = activity_submissions[lesson_key]
            args['payload']['location'] = (
                'http://localhost:8080/activity?unit=%s&lesson=%s' %
                (unit_id, lesson_id))
            args['payload'] = transforms.dumps(args['payload'])

            # Submit the request to the backend.
            response = self.post('rest/events?%s' % urllib.urlencode(
                {'request': transforms.dumps(args)}), {})
            assert_equals(response.status_int, 200)
            assert not response.body

            # Check that the current activity shows partial progress.
            response, args = self.get_activity(unit_id, lesson_id, {})
            assert_contains(
                u'id="progress-inprogress-%s"' % lesson_id, response.body)

            # Navigate to the course overview page and check that the unit shows
            # partial progress.
            response = self.get('course')
            assert_equals(response.status_int, 200)
            assert_contains(
                u'id="progress-inprogress-%s"' % unit_id, response.body)
        finally:
            namespace_manager.set_namespace(old_namespace)

    def test_progress(self):
        """Test student activity progress in detail, using the sample course."""

        class FakeHandler(object):
            def __init__(self, app_context):
                self.app_context = app_context

        course = Course(FakeHandler(sites.get_all_courses()[0]))
        tracker = course.get_progress_tracker()
        student = models.Student(key_name='key-test-student')

        # Initially, all progress entries should be set to zero.
        unit_progress = tracker.get_unit_progress(student)
        for key in unit_progress:
            assert unit_progress[key] == 0
        lesson_progress = tracker.get_lesson_progress(student, 1)
        for key in lesson_progress:
            assert lesson_progress[key] == 0

        # The blocks in Lesson 1.2 with activities are blocks 3 and 6.
        # Submitting block 3 should trigger an in-progress update.
        tracker.put_block_completed(student, 1, 2, 3)
        assert tracker.get_unit_progress(student)['1'] == 1
        assert tracker.get_lesson_progress(student, 1)[2] == 1

        # Submitting block 6 should trigger a completion update for Lesson 1.2.
        tracker.put_block_completed(student, 1, 2, 6)
        assert tracker.get_unit_progress(student)['1'] == 1
        assert tracker.get_lesson_progress(student, 1)[2] == 2

        # Test a lesson with no interactive blocks in its activity. It should
        # change its status to 'completed' once it is accessed.
        tracker.put_activity_accessed(student, 2, 1)
        assert tracker.get_unit_progress(student)['2'] == 1
        assert tracker.get_lesson_progress(student, 2)[1] == 2

        # Test that a lesson without activities (Lesson 1.1) doesn't count.
        # Complete lessons 1.3, 1.4, 1.5 and 1.6; unit 1 should then be marked
        # as 'completed' even though we have no events associated with
        # Lesson 1.1.
        tracker.put_activity_completed(student, 1, 3)
        assert tracker.get_unit_progress(student)['1'] == 1
        tracker.put_activity_completed(student, 1, 4)
        assert tracker.get_unit_progress(student)['1'] == 1
        tracker.put_activity_completed(student, 1, 5)
        assert tracker.get_unit_progress(student)['1'] == 1
        tracker.put_activity_completed(student, 1, 6)
        assert tracker.get_unit_progress(student)['1'] == 2

        # Test that a unit is not completed until all activity pages have been,
        # at least, visited. Unit 6 has 3 lessons; the last one has no
        # activity block.
        tracker.put_activity_completed(student, 6, 1)
        tracker.put_activity_completed(student, 6, 2)
        assert tracker.get_unit_progress(student)['6'] == 1
        tracker.put_activity_accessed(student, 6, 3)
        assert tracker.get_unit_progress(student)['6'] == 2

        # Test assessment counters.
        pre_id = 'Pre'
        tracker.put_assessment_completed(student, pre_id)
        progress = tracker.get_or_create_progress(student)
        assert tracker.is_assessment_completed(progress, pre_id)
        assert tracker.get_assessment_status(progress, pre_id) == 1

        tracker.put_assessment_completed(student, pre_id)
        progress = tracker.get_or_create_progress(student)
        assert tracker.is_assessment_completed(progress, pre_id)
        assert tracker.get_assessment_status(progress, pre_id) == 2

        tracker.put_assessment_completed(student, pre_id)
        progress = tracker.get_or_create_progress(student)
        assert tracker.is_assessment_completed(progress, pre_id)
        assert tracker.get_assessment_status(progress, pre_id) == 3

        # Test that invalid keys do not lead to any updates.
        # Invalid assessment id.
        fake_id = 'asdf'
        tracker.put_assessment_completed(student, fake_id)
        progress = tracker.get_or_create_progress(student)
        assert not tracker.is_assessment_completed(progress, fake_id)
        assert tracker.get_assessment_status(progress, fake_id) is None
        # Invalid unit id.
        tracker.put_activity_completed(student, fake_id, 1)
        progress = tracker.get_or_create_progress(student)
        assert tracker.get_activity_status(progress, fake_id, 1) is None
        # Invalid lesson id.
        fake_numeric_id = 22
        tracker.put_activity_completed(student, 1, fake_numeric_id)
        progress = tracker.get_or_create_progress(student)
        assert tracker.get_activity_status(progress, 1, fake_numeric_id) is None
        # Invalid block id.
        tracker.put_block_completed(student, 5, 2, fake_numeric_id)
        progress = tracker.get_or_create_progress(student)
        assert not tracker.is_block_completed(
            progress, 5, 2, fake_numeric_id)


class AssessmentTest(actions.TestBase):
    """Test for assessments."""

    def test_course_pass(self):
        """Test student passing final exam."""
        email = 'test_pass@google.com'
        name = 'Test Pass'

        post = {'assessment_type': 'Fin', 'score': '100.00'}

        # Register.
        actions.login(email)
        actions.register(self, name)

        # Submit answer.
        response = actions.submit_assessment(self, 'Fin', post)
        assert_equals(response.status_int, 200)
        assert_contains('your overall course score of 70%', response.body)
        assert_contains('you have passed the course', response.body)

        # Check that the result shows up on the profile page.
        response = actions.check_profile(self, name)
        assert_contains('70', response.body)
        assert_contains('100', response.body)

    def test_assessments(self):
        """Test assessment scores are properly submitted and summarized."""

        course = courses.Course(None, app_context=sites.get_all_courses()[0])

        email = 'test_assessments@google.com'
        name = 'Test Assessments'

        pre_answers = [{'foo': 'bar'}, {'Alice': u'Bob (тест данные)'}]
        pre = {
            'assessment_type': 'Pre', 'score': '1.00',
            'answers': transforms.dumps(pre_answers)}
        mid = {'assessment_type': 'Mid', 'score': '2.00'}
        fin = {'assessment_type': 'Fin', 'score': '3.00'}
        second_mid = {'assessment_type': 'Mid', 'score': '1.00'}
        second_fin = {'assessment_type': 'Fin', 'score': '100000'}

        # Register.
        actions.login(email)
        actions.register(self, name)

        # Navigate to the course overview page.
        response = self.get('course')
        assert_equals(response.status_int, 200)
        assert_does_not_contain(u'id="progress-completed-Mid', response.body)
        assert_contains(u'id="progress-notstarted-Mid', response.body)

        old_namespace = namespace_manager.get_namespace()
        namespace_manager.set_namespace(self.namespace)
        try:
            student = models.Student.get_enrolled_student_by_email(email)

            # Check that three score objects (corresponding to the Pre, Mid and
            # Fin assessments) exist right now, and that they all have zero
            # score.
            student_scores = course.get_all_scores(student)
            assert len(student_scores) == 3
            for assessment in student_scores:
                assert assessment['score'] == 0

            # Submit assessments and check that the score is updated.
            actions.submit_assessment(self, 'Pre', pre)
            student = models.Student.get_enrolled_student_by_email(email)
            student_scores = course.get_all_scores(student)
            assert len(student_scores) == 3
            for assessment in student_scores:
                if assessment['id'] == 'Pre':
                    assert assessment['score'] > 0
                else:
                    assert assessment['score'] == 0

            actions.submit_assessment(self, 'Mid', mid)
            student = models.Student.get_enrolled_student_by_email(email)

            # Navigate to the course overview page.
            response = self.get('course')
            assert_equals(response.status_int, 200)
            assert_contains(u'id="progress-completed-Pre', response.body)
            assert_contains(u'id="progress-completed-Mid', response.body)
            assert_contains(u'id="progress-notstarted-Fin', response.body)

            # Submit the final assessment.
            actions.submit_assessment(self, 'Fin', fin)
            student = models.Student.get_enrolled_student_by_email(email)

            # Navigate to the course overview page.
            response = self.get('course')
            assert_equals(response.status_int, 200)
            assert_contains(u'id="progress-completed-Fin', response.body)

            # Check that the overall-score is non-zero.
            assert course.get_overall_score(student)

            # Check assessment answers.
            answers = transforms.loads(
                models.StudentAnswersEntity.get_by_key_name(
                    student.user_id).data)
            assert pre_answers == answers['Pre']

            # pylint: disable-msg=g-explicit-bool-comparison
            assert [] == answers['Mid']
            assert [] == answers['Fin']
            # pylint: enable-msg=g-explicit-bool-comparison

            # Check that scores are recorded properly.
            student = models.Student.get_enrolled_student_by_email(email)
            assert int(course.get_score(student, 'Pre')) == 1
            assert int(course.get_score(student, 'Mid')) == 2
            assert int(course.get_score(student, 'Fin')) == 3
            assert (int(course.get_overall_score(student)) ==
                    int((0.30 * 2) + (0.70 * 3)))

            # Try posting a new midcourse exam with a lower score;
            # nothing should change.
            actions.submit_assessment(self, 'Mid', second_mid)
            student = models.Student.get_enrolled_student_by_email(email)
            assert int(course.get_score(student, 'Pre')) == 1
            assert int(course.get_score(student, 'Mid')) == 2
            assert int(course.get_score(student, 'Fin')) == 3
            assert (int(course.get_overall_score(student)) ==
                    int((0.30 * 2) + (0.70 * 3)))

            # Now try posting a postcourse exam with a higher score and note
            # the changes.
            actions.submit_assessment(self, 'Fin', second_fin)
            student = models.Student.get_enrolled_student_by_email(email)
            assert int(course.get_score(student, 'Pre')) == 1
            assert int(course.get_score(student, 'Mid')) == 2
            assert int(course.get_score(student, 'Fin')) == 100000
            assert (int(course.get_overall_score(student)) ==
                    int((0.30 * 2) + (0.70 * 100000)))
        finally:
            namespace_manager.set_namespace(old_namespace)


def remove_dir(dir_name):
    """Delete a directory."""

    logging.info('removing folder: %s', dir_name)
    if os.path.exists(dir_name):
        shutil.rmtree(dir_name)
        if os.path.exists(dir_name):
            raise Exception('Failed to delete directory: %s' % dir_name)


def clean_dir(dir_name):
    """Clean a directory."""

    remove_dir(dir_name)

    logging.info('creating folder: %s', dir_name)
    os.makedirs(dir_name)
    if not os.path.exists(dir_name):
        raise Exception('Failed to create directory: %s' % dir_name)


def clone_canonical_course_data(src, dst):
    """Makes a copy of canonical course content."""
    clean_dir(dst)

    def copytree(name):
        shutil.copytree(
            os.path.join(src, name),
            os.path.join(dst, name))

    copytree('assets')
    copytree('data')
    copytree('views')

    shutil.copy(
        os.path.join(src, 'course.yaml'),
        os.path.join(dst, 'course.yaml'))

    # Make all files writable.
    for root, unused_dirs, files in os.walk(dst):
        for afile in files:
            fname = os.path.join(root, afile)
            os.chmod(fname, 0o777)


class GeneratedCourse(object):
    """A helper class for a dynamically generated course content."""

    @classmethod
    def set_data_home(cls, test):
        """All data for this test will be placed here."""
        cls.data_home = os.path.join(TEST_DATA_BASE, test.__class__.__name__)

    def __init__(self, ns):
        self.path = ns

    @property
    def namespace(self):
        return 'ns%s' % self.path

    @property
    def title(self):
        return u'Power Searching with Google title-%s (тест данные)' % self.path

    @property
    def unit_title(self):
        return u'Interpreting results unit-title-%s (тест данные)' % self.path

    @property
    def lesson_title(self):
        return u'Word order matters lesson-title-%s (тест данные)' % self.path

    @property
    def head(self):
        return '<!-- head-%s -->' % self.path

    @property
    def css(self):
        return '<!-- css-%s -->' % self.path

    @property
    def home(self):
        return os.path.join(self.data_home, 'data-%s' % self.path)

    @property
    def email(self):
        return 'walk_the_course_named_%s@google.com' % self.path

    @property
    def name(self):
        return 'Walk The Course Named %s' % self.path


class MultipleCoursesTestBase(actions.TestBase):
    """Configures several courses for running concurrently."""

    def modify_file(self, filename, find, replace):
        """Read, modify and write back the file."""

        text = open(filename, 'r').read().decode('utf-8')

        # Make sure target text is not in the file.
        assert not replace in text
        text = text.replace(find, replace)
        assert replace in text

        open(filename, 'w').write(text.encode('utf-8'))

    def modify_canonical_course_data(self, course):
        """Modify canonical content by adding unique bits to it."""

        self.modify_file(
            os.path.join(course.home, 'course.yaml'),
            'title: \'Power Searching with Google\'',
            'title: \'%s\'' % course.title)

        self.modify_file(
            os.path.join(course.home, 'data/unit.csv'),
            ',Interpreting results,',
            ',%s,' % course.unit_title)

        self.modify_file(
            os.path.join(course.home, 'data/lesson.csv'),
            ',Word order matters,',
            ',%s,' % course.lesson_title)

        self.modify_file(
            os.path.join(course.home, 'data/lesson.csv'),
            ',Interpreting results,',
            ',%s,' % course.unit_title)

        self.modify_file(
            os.path.join(course.home, 'views/base.html'),
            '<head>',
            '<head>\n%s' % course.head)

        self.modify_file(
            os.path.join(course.home, 'assets/css/main.css'),
            'html {',
            '%s\nhtml {' % course.css)

    def prepare_course_data(self, course):
        """Create unique course content for a course."""

        clone_canonical_course_data(self.bundle_root, course.home)
        self.modify_canonical_course_data(course)

    def setUp(self):  # pylint: disable-msg=g-bad-name
        """Configure the test."""

        super(MultipleCoursesTestBase, self).setUp()

        GeneratedCourse.set_data_home(self)

        self.course_a = GeneratedCourse('a')
        self.course_b = GeneratedCourse('b')
        self.course_ru = GeneratedCourse('ru')

        # Override BUNDLE_ROOT.
        self.bundle_root = appengine_config.BUNDLE_ROOT
        appengine_config.BUNDLE_ROOT = GeneratedCourse.data_home

        # Prepare course content.
        clean_dir(GeneratedCourse.data_home)
        self.prepare_course_data(self.course_a)
        self.prepare_course_data(self.course_b)
        self.prepare_course_data(self.course_ru)

        # Setup one course for I18N.
        self.modify_file(
            os.path.join(self.course_ru.home, 'course.yaml'),
            'locale: \'en_US\'',
            'locale: \'ru\'')

        # Configure courses.
        sites.setup_courses('%s, %s, %s' % (
            'course:/courses/a:/data-a:nsa',
            'course:/courses/b:/data-b:nsb',
            'course:/courses/ru:/data-ru:nsru'))

    def tearDown(self):  # pylint: disable-msg=g-bad-name
        """Clean up."""
        sites.reset_courses()
        appengine_config.BUNDLE_ROOT = self.bundle_root
        super(MultipleCoursesTestBase, self).tearDown()

    def walk_the_course(
        self, course, first_time=True, is_admin=False, logout=True):
        """Visit a course as a Student would."""

        # Check normal user has no access.
        actions.login(course.email, is_admin)

        # Test schedule.
        if first_time:
            response = self.testapp.get('/courses/%s/preview' % course.path)
        else:
            response = self.testapp.get('/courses/%s/course' % course.path)
        assert_contains(course.title, response.body)
        assert_contains(course.unit_title, response.body)
        assert_contains(course.head, response.body)

        # Tests static resource.
        response = self.testapp.get(
            '/courses/%s/assets/css/main.css' % course.path)
        assert_contains(course.css, response.body)

        if first_time:
            # Test registration.
            response = self.get('/courses/%s/register' % course.path)
            assert_contains(course.title, response.body)
            assert_contains(course.head, response.body)
            response.form.set('form01', course.name)
            response.form.action = '/courses/%s/register' % course.path
            response = self.submit(response.form)

            assert_contains(course.title, response.body)
            assert_contains(course.head, response.body)
            assert_contains(course.title, response.body)
            assert_contains(
                '//groups.google.com/group/My-Course-Announce', response.body)
            assert_contains(
                '//groups.google.com/group/My-Course', response.body)

        # Check lesson page.
        response = self.testapp.get(
            '/courses/%s/unit?unit=1&lesson=5' % course.path)
        assert_contains(course.title, response.body)
        assert_contains(course.lesson_title, response.body)
        assert_contains(course.head, response.body)

        # Check activity page.
        response = self.testapp.get(
            '/courses/%s/activity?unit=1&lesson=5' % course.path)
        assert_contains(course.title, response.body)
        assert_contains(course.lesson_title, response.body)
        assert_contains(course.head, response.body)

        if logout:
            actions.logout()


class MultipleCoursesTest(MultipleCoursesTestBase):
    """Test several courses running concurrently."""

    def test_courses_are_isolated(self):
        """Test each course serves its own assets, views and data."""

        # Pretend students visit courses.
        self.walk_the_course(self.course_a)
        self.walk_the_course(self.course_b)
        self.walk_the_course(self.course_a, False)
        self.walk_the_course(self.course_b, False)

        # Check course namespaced data.
        self.validate_course_data(self.course_a)
        self.validate_course_data(self.course_b)

        # Check default namespace.
        assert (
            namespace_manager.get_namespace() ==
            appengine_config.DEFAULT_NAMESPACE_NAME)

        assert not models.Student.all().fetch(1000)

    def validate_course_data(self, course):
        """Check course data is valid."""

        old_namespace = namespace_manager.get_namespace()
        namespace_manager.set_namespace(course.namespace)
        try:
            students = models.Student.all().fetch(1000)
            assert len(students) == 1
            for student in students:
                assert_equals(course.email, student.key().name())
                assert_equals(course.name, student.name)
        finally:
            namespace_manager.set_namespace(old_namespace)


class I18NTest(MultipleCoursesTestBase):
    """Test courses running in different locales and containing I18N content."""

    def test_csv_supports_utf8(self):
        """Test UTF-8 content in CSV file is handled correctly."""

        title_ru = u'Найди факты быстрее'

        csv_file = os.path.join(self.course_ru.home, 'data/unit.csv')
        self.modify_file(
            csv_file, ',Find facts faster,', ',%s,' % title_ru)
        self.modify_file(
            os.path.join(self.course_ru.home, 'data/lesson.csv'),
            ',Find facts faster,', ',%s,' % title_ru)

        rows = []
        for row in csv.reader(open(csv_file)):
            rows.append(row)
        assert title_ru == rows[6][3].decode('utf-8')

        response = self.get('/courses/%s/preview' % self.course_ru.path)
        assert_contains(title_ru, response.body)

        # Tests student perspective.
        self.walk_the_course(self.course_ru, first_time=True)
        self.walk_the_course(self.course_ru, first_time=False)

        # Test course author dashboard.
        self.walk_the_course(
            self.course_ru, first_time=False, is_admin=True, logout=False)

        def assert_page_contains(page_name, text_array):
            dashboard_url = '/courses/%s/dashboard' % self.course_ru.path
            response = self.get('%s?action=%s' % (dashboard_url, page_name))
            for text in text_array:
                assert_contains(text, response.body)

        assert_page_contains('', [
            title_ru, self.course_ru.unit_title, self.course_ru.lesson_title])
        assert_page_contains(
            'assets', [self.course_ru.title])
        assert_page_contains(
            'settings', [
                self.course_ru.title,
                vfs.AbstractFileSystem.normpath(self.course_ru.home)])

        # Clean up.
        actions.logout()

    def test_i18n(self):
        """Test course is properly internationalized."""
        response = self.get('/courses/%s/preview' % self.course_ru.path)
        assert_contains_all_of(
            [u'Войти', u'Регистрация', u'Расписание', u'Курс'], response.body)


class CourseUrlRewritingTestBase(actions.TestBase):
    """Prepare course for using rewrite rules and '/courses/pswg' base URL."""

    def setUp(self):  # pylint: disable-msg=g-bad-name
        super(CourseUrlRewritingTestBase, self).setUp()

        self.base = '/courses/pswg'
        self.namespace = 'gcb-courses-pswg-tests-ns'
        sites.setup_courses('course:%s:/:%s' % (self.base, self.namespace))

    def tearDown(self):  # pylint: disable-msg=g-bad-name
        sites.reset_courses()
        super(CourseUrlRewritingTestBase, self).tearDown()

    def canonicalize(self, href, response=None):
        """Canonicalize URL's using either <base> or self.base."""
        # Check if already canonicalized.
        if href.startswith(
                self.base) or utils.ApplicationHandler.is_absolute(href):
            pass
        else:
            # Look for <base> tag in the response to compute the canonical URL.
            if response:
                return super(CourseUrlRewritingTestBase, self).canonicalize(
                    href, response)

            # Prepend self.base to compute the canonical URL.
            if not href.startswith('/'):
                href = '/%s' % href
            href = '%s%s' % (self.base, href)

        self.audit_url(href)
        return href


class VirtualFileSystemTestBase(actions.TestBase):
    """Prepares a course running on a virtual local file system."""

    def setUp(self):  # pylint: disable-msg=g-bad-name
        """Configure the test."""

        super(VirtualFileSystemTestBase, self).setUp()

        GeneratedCourse.set_data_home(self)

        # Override BUNDLE_ROOT.
        self.bundle_root = appengine_config.BUNDLE_ROOT
        appengine_config.BUNDLE_ROOT = GeneratedCourse.data_home

        # Prepare course content.
        home_folder = os.path.join(GeneratedCourse.data_home, 'data-v')
        clone_canonical_course_data(self.bundle_root, home_folder)

        # Configure course.
        self.namespace = 'nsv'
        sites.setup_courses('course:/:/data-vfs:%s' % self.namespace)

        # Modify app_context filesystem to map /data-v to /data-vfs.
        def after_create(unused_cls, instance):
            # pylint: disable-msg=protected-access
            instance._fs = vfs.AbstractFileSystem(
                vfs.LocalReadOnlyFileSystem(
                    os.path.join(GeneratedCourse.data_home, 'data-vfs'),
                    home_folder))

        sites.ApplicationContext.after_create = after_create

    def tearDown(self):  # pylint: disable-msg=g-bad-name
        """Clean up."""
        sites.reset_courses()
        appengine_config.BUNDLE_ROOT = self.bundle_root
        super(VirtualFileSystemTestBase, self).tearDown()


class DatastoreBackedCourseTest(actions.TestBase):
    """Prepares an empty course running on datastore-backed file system."""

    def setUp(self):  # pylint: disable-msg=g-bad-name
        """Configure the test."""
        super(DatastoreBackedCourseTest, self).setUp()

        self.supports_editing = True
        self.namespace = 'dsbfs'
        sites.setup_courses('course:/::%s' % self.namespace)

        all_courses = sites.get_all_courses()
        assert len(all_courses) == 1
        self.app_context = all_courses[0]

    def tearDown(self):  # pylint: disable-msg=g-bad-name
        """Clean up."""
        sites.reset_courses()
        super(DatastoreBackedCourseTest, self).tearDown()

    def upload_all_in_dir(self, dir_name, files_added):
        """Uploads all files in a folder to vfs."""
        root_dir = os.path.join(appengine_config.BUNDLE_ROOT, dir_name)
        for root, unused_dirs, files in os.walk(root_dir):
            for afile in files:
                filename = os.path.join(root, afile)
                self.app_context.fs.put(filename, open(filename, 'rb'))
                files_added.append(filename)

    def init_course_data(self, upload_files):
        """Uploads required course data files into vfs."""
        files_added = []
        old_namespace = namespace_manager.get_namespace()
        try:
            namespace_manager.set_namespace(self.namespace)
            upload_files(files_added)

            # Normalize paths to be identical for Windows and Linux.
            files_added_normpath = []
            for file_added in files_added:
                files_added_normpath.append(
                    vfs.AbstractFileSystem.normpath(file_added))

            assert self.app_context.fs.list(
                appengine_config.BUNDLE_ROOT) == sorted(files_added_normpath)
        finally:
            namespace_manager.set_namespace(old_namespace)

    def upload_all_sample_course_files(self, files_added):
        """Uploads all sample course data files into vfs."""
        self.upload_all_in_dir('assets', files_added)
        self.upload_all_in_dir('views', files_added)
        self.upload_all_in_dir('data', files_added)

        course_yaml = os.path.join(
            appengine_config.BUNDLE_ROOT, 'course.yaml')
        self.app_context.fs.put(course_yaml, open(course_yaml, 'rb'))
        files_added.append(course_yaml)


class DatastoreBackedCustomCourseTest(DatastoreBackedCourseTest):
    """Prepares a sample course running on datastore-backed file system."""

    def test_course_import(self):
        """Test importing of the course."""

        # Setup courses.
        sites.setup_courses('course:/test::ns_test, course:/:/')
        self.namespace = 'ns_test'
        self.base = '/test'
        config.Registry.test_overrides[
            models.CAN_USE_MEMCACHE.name] = True

        # Format import payload and URL.
        payload_dict = {}
        payload_dict['course'] = 'course:/:/'
        request = {}
        request['payload'] = transforms.dumps(payload_dict)
        import_put_url = (
            '/test/rest/course/import?%s' % urllib.urlencode(
                {'request': transforms.dumps(request)}))

        # Check non-logged user has no rights.
        response = self.testapp.put(import_put_url, {}, expect_errors=True)
        assert_equals(404, response.status_int)

        # Login as admin.
        email = 'test_course_import@google.com'
        name = 'Test Course Import'
        actions.login(email, is_admin=True)

        # Check course is empty.
        response = self.get('/test/dashboard')
        assert_equals(200, response.status_int)
        assert_does_not_contain('Filter image results by color', response.body)

        # Import sample course.
        response = self.put(import_put_url, {})
        assert_equals(200, response.status_int)
        assert_contains('Imported.', response.body)

        # Check course is not empty.
        response = self.get('/test/dashboard')
        assert_contains('Filter image results by color', response.body)

        # Check assessment is copied.
        response = self.get('/test/assets/js/assessment-21.js')
        assert_equals(200, response.status_int)
        assert_contains('Humane Society website', response.body)

        # Check activity is copied.
        response = self.get('/test/assets/js/activity-37.js')
        assert_equals(200, response.status_int)
        assert_contains('explore ways to keep yourself updated', response.body)

        unit_2_title = 'Unit 2 - Interpreting results'
        lesson_2_1_title = '2.1 When search results suggest something new'
        lesson_2_2_title = '2.2 Thinking more deeply about your search'

        # Check units and lessons are indexed correctly.
        response = self.get('/test/preview')
        assert_contains(unit_2_title, response.body)
        actions.register(self, name)
        response = self.get('/test/course')
        assert_contains(unit_2_title, response.body)

        # Unit page.
        response = self.get('/test/unit?unit=9')
        assert_contains(  # A unit title.
            unit_2_title, response.body)
        assert_contains(  # First child lesson without link.
            lesson_2_1_title, response.body)
        assert_contains(  # Second child lesson with link.
            lesson_2_2_title, response.body)
        assert_contains_all_of(  # Breabcrubms.
            ['Unit 2</a></li>', 'Lesson 1</li>'], response.body)

        # Unit page.
        response = self.get('/test/activity?unit=9&lesson=10')
        assert_contains(  # A unit title.
            unit_2_title, response.body)
        assert_contains(  # An activity title.
            'Lesson 2.1 Activity', response.body)
        assert_contains(  # First child lesson without link.
            lesson_2_1_title, response.body)
        assert_contains(  # Second child lesson with link.
            lesson_2_2_title, response.body)
        assert_contains_all_of(  # Breabcrubms.
            ['Unit 2</a></li>', 'Lesson 2</a></li>'], response.body)

        # Clean up.
        sites.reset_courses()
        config.Registry.test_overrides = {}

    def test_get_put_file(self):
        """Test that one can put/get file via REST interface."""
        self.init_course_data(self.upload_all_sample_course_files)

        email = 'test_get_put_file@google.com'

        actions.login(email, True)
        response = self.get('dashboard?action=settings')

        # Check course.yaml edit form.
        compute_form = response.forms['edit_course_yaml']
        response = self.submit(compute_form)
        assert_equals(response.status_int, 302)
        assert_contains(
            'dashboard?action=edit_settings&key=%2Fcourse.yaml',
            response.location)
        response = self.get(response.location)
        assert_contains('rest/files/item?key=%2Fcourse.yaml', response.body)

        # Get text file.
        response = self.get('rest/files/item?key=%2Fcourse.yaml')
        assert_equals(response.status_int, 200)
        json_dict = transforms.loads(
            transforms.loads(response.body)['payload'])
        assert '/course.yaml' == json_dict['key']
        assert 'text/utf-8' == json_dict['encoding']
        assert (open(os.path.join(
            appengine_config.BUNDLE_ROOT, 'course.yaml')).read(
                ) == json_dict['content'])

    def test_empty_course(self):
        """Test course with no assets and the simlest possible course.yaml."""

        email = 'test_empty_course@google.com'
        actions.login(email, True)

        # Check minimal preview page comes up.
        response = self.get('preview')
        assert_contains('UNTITLED COURSE', response.body)
        assert_contains('Registration', response.body)

        # Check inheritable files are accessible.
        response = self.get('/assets/css/main.css')
        assert (open(os.path.join(
            appengine_config.BUNDLE_ROOT, 'assets/css/main.css')).read(
                ) == response.body)

        # Check non-inheritable files are not inherited.
        response = self.testapp.get(
            '/assets/js/activity-1.3.js', expect_errors=True)
        assert_equals(response.status_int, 404)

        # Login as admin.
        email = 'test_empty_course@google.com'
        actions.login(email, True)
        response = self.get('dashboard')

        # Add unit.
        compute_form = response.forms['add_unit']
        response = self.submit(compute_form)
        response = self.get('/rest/course/unit?key=1')
        assert_equals(response.status_int, 200)

        # Add lessons.
        response = self.get('dashboard')
        compute_form = response.forms['add_lesson']
        response = self.submit(compute_form)
        response = self.get('/rest/course/lesson?key=2')
        assert_equals(response.status_int, 200)

        # Add assessment.
        response = self.get('dashboard')
        compute_form = response.forms['add_assessment']
        response = self.submit(compute_form)
        response = self.get('/rest/course/assessment?key=3')
        assert_equals(response.status_int, 200)

        # Add link.
        response = self.get('dashboard')
        compute_form = response.forms['add_link']
        response = self.submit(compute_form)
        response = self.get('/rest/course/link?key=4')
        assert_equals(response.status_int, 200)

    def import_sample_course(self):
        """Imports a sample course."""
        # Setup courses.
        sites.setup_courses('course:/test::ns_test, course:/:/')

        # Import sample course.
        dst_app_context = sites.get_all_courses()[0]
        src_app_context = sites.get_all_courses()[1]
        dst_course = courses.Course(None, app_context=dst_app_context)

        errors = []
        src_course_out, dst_course_out = dst_course.import_from(
            src_app_context, errors)
        if errors:
            raise Exception(errors)
        assert len(
            src_course_out.get_units()) == len(dst_course_out.get_units())
        dst_course_out.save()

        # Clean up.
        sites.reset_courses()

    def test_imported_course_performace(self):
        """Tests various pages of the imported course."""
        self.import_sample_course()

        # Install a clone on the '/' so all the tests will treat it as normal
        # sample course.
        sites.setup_courses('course:/::ns_test')
        self.namespace = 'ns_test'

        # Enable memcache.
        config.Registry.test_overrides[
            models.CAN_USE_MEMCACHE.name] = True

        # Override course.yaml settings by patching app_context.
        get_environ_old = sites.ApplicationContext.get_environ

        def get_environ_new(self):
            environ = get_environ_old(self)
            environ['course']['now_available'] = True
            return environ

        sites.ApplicationContext.get_environ = get_environ_new

        def custom_inc(unused_increment=1, context=None):
            """A custom inc() function for cache miss counter."""
            self.keys.append(context)
            self.count += 1

        def assert_cached(url, assert_text, cache_miss_allowed=0):
            """Checks that specific URL supports caching."""
            memcache.flush_all()

            self.keys = []
            self.count = 0

            # Expect cache misses first time we load page.
            cache_miss_before = self.count
            response = self.get(url)
            assert_contains(assert_text, response.body)
            assert cache_miss_before != self.count

            # Expect no cache misses first time we load page.
            self.keys = []
            cache_miss_before = self.count
            response = self.get(url)
            assert_contains(assert_text, response.body)
            cache_miss_actual = self.count - cache_miss_before
            if cache_miss_actual != cache_miss_allowed:
                raise Exception(
                    'Expected %s cache misses, got %s. Keys are:\n%s' % (
                        cache_miss_allowed, cache_miss_actual,
                        '\n'.join(self.keys)))

        old_inc = models.CACHE_MISS.inc
        models.CACHE_MISS.inc = custom_inc

        # Walk the site.
        email = 'test_units_lessons@google.com'
        name = 'Test Units Lessons'

        assert_cached('preview', 'Putting it all together')
        actions.login(email, True)
        assert_cached('preview', 'Putting it all together')
        actions.register(self, name)
        assert_cached(
            'unit?unit=9', 'When search results suggest something new')
        assert_cached(
            'unit?unit=9&lesson=12', 'Understand options for different media')

        # Clean up.
        models.CACHE_MISS.inc = old_inc
        sites.ApplicationContext.get_environ = get_environ_old
        config.Registry.test_overrides = {}
        sites.reset_courses()

    def test_imported_course(self):
        """Tests various pages of the imported course."""
        # TODO(psimakov): Ideally, this test class should run all aspect tests
        # and they all should pass. However, the id's in the cloned course
        # do not match the id's of source sample course and we fetch pages
        # and assert page content using id's. For now, we will check the minimal
        # set of pages manually. Later, we have to make it run all known tests.

        self.import_sample_course()

        # Install a clone on the '/' so all the tests will treat it as normal
        # sample course.
        sites.setup_courses('course:/::ns_test')
        self.namespace = 'ns_test'

        email = 'test_units_lessons@google.com'
        name = 'Test Units Lessons'

        actions.login(email, True)

        response = self.get('preview')
        assert_contains('Putting it all together', response.body)

        actions.register(self, name)
        actions.check_profile(self, name)
        actions.view_announcements(self)

        # Check unit page without lesson specified.
        response = self.get('unit?unit=9')
        assert_contains('Interpreting results', response.body)
        assert_contains(
            'When search results suggest something new', response.body)

        # Check unit page with a lessons.
        response = self.get('unit?unit=9&lesson=12')
        assert_contains('Interpreting results', response.body)
        assert_contains(
            'Understand options for different media', response.body)

        # Check assesment page.
        response = self.get('assessment?name=21')
        assert_contains(
            '<script src="assets/js/assessment-21.js"></script>', response.body)

        # Check activity page.
        response = self.get('activity?unit=9&lesson=13')
        assert_contains(
            '<script src="assets/js/activity-13.js"></script>',
            response.body)

        # Clean up.
        sites.reset_courses()


class DatastoreBackedSampleCourseTest(DatastoreBackedCourseTest):
    """Run all existing tests using datastore-backed file system."""

    def setUp(self):  # pylint: disable-msg=g-bad-name
        super(DatastoreBackedSampleCourseTest, self).setUp()
        self.init_course_data(self.upload_all_sample_course_files)


class FakeEnvironment(object):
    """Temporary fake tools.etl.remote.Evironment.

    Bypasses making a remote_api connection because webtest can't handle it and
    we don't want to bring up a local server for our functional tests. When this
    fake is used, the in-process datastore stub will handle RPCs.

    TODO(johncox): find a way to make webtest successfully emulate the
    remote_api endpoint and get rid of this fake.
    """

    def __init__(self, application_id, server, path=None):
        self._appication_id = application_id
        self._path = path
        self._server = server

    def establish(self):
        pass


class EtlMainTestCase(DatastoreBackedCourseTest):
    """Tests tools/etl/etl.py's main()."""

    # Allow access to protected members under test.
    # pylint: disable-msg=protected-access
    def setUp(self):
        """Configures EtlMainTestCase."""
        super(EtlMainTestCase, self).setUp()
        self.test_environ = copy.deepcopy(os.environ)
        # In etl.main, use test auth scheme to avoid interactive login.
        self.test_environ['SERVER_SOFTWARE'] = remote.TEST_SERVER_SOFTWARE
        self.test_tempdir = os.path.join(TEST_DATA_BASE, 'EtlMainTestCase')
        self.archive_path = os.path.join(self.test_tempdir, 'archive.zip')
        self.new_course_title = 'New Course Title'
        self.sdk_path = os.environ.get('GOOGLE_APP_ENGINE_HOME')

        # Find App Engine SDK folder by navigating up four folders from well
        # known google.appengine.api.memcache.
        self.sdk_path = os.path.abspath(memcache.__file__).rsplit(os.sep, 5)[0]

        self.url_prefix = '/test'
        self.raw = 'course:%s::ns_test' % self.url_prefix
        self.swap(os, 'environ', self.test_environ)
        self.common_args = [
            etl._TYPES[0], self.url_prefix, 'myapp', 'localhost:8080',
            self.archive_path, '--sdk_path', self.sdk_path]
        self.download_args = etl._PARSER.parse_args(
            ['download'] + self.common_args)
        self.upload_args = etl._PARSER.parse_args(['upload'] + self.common_args)
        # Set up courses: version 1.3, version 1.2.
        sites.setup_courses(self.raw + ', course:/:/')
        self.reset_filesystem()

    def tearDown(self):
        self.reset_filesystem(remove_only=True)
        sites.reset_courses()
        super(EtlMainTestCase, self).tearDown()

    def create_archive(self):
        self.upload_all_sample_course_files([])
        self.import_sample_course()
        args = etl._PARSER.parse_args(['download'] + self.common_args)
        etl.main(args, environment_class=FakeEnvironment)
        sites.reset_courses()

    def create_empty_course(self, raw):
        sites.setup_courses(raw)
        context = etl._get_requested_context(
            sites.get_all_courses(), self.url_prefix)
        for entity in context.fs.impl.list(appengine_config.BUNDLE_ROOT):
            context.fs.impl.delete(entity)

    def import_sample_course(self):
        """Imports a sample course."""

        # Import sample course.
        dst_app_context = sites.get_all_courses()[0]
        src_app_context = sites.get_all_courses()[1]

        # Patch in a course.yaml.
        yaml = copy.deepcopy(courses.DEFAULT_COURSE_YAML_DICT)
        yaml['course']['title'] = self.new_course_title
        dst_app_context.fs.impl.put(
            os.path.join(appengine_config.BUNDLE_ROOT, etl._COURSE_YAML_PATH),
            etl._ReadWrapper(str(yaml)), is_draft=False)

        dst_course = courses.Course(None, app_context=dst_app_context)

        errors = []
        src_course_out, dst_course_out = dst_course.import_from(
            src_app_context, errors)
        if errors:
            raise Exception(errors)
        assert len(
            src_course_out.get_units()) == len(dst_course_out.get_units())
        dst_course_out.save()

    def reset_filesystem(self, remove_only=False):
        if os.path.exists(self.test_tempdir):
            shutil.rmtree(self.test_tempdir)
        if not remove_only:
            os.makedirs(self.test_tempdir)

    def test_download_creates_valid_archive(self):
        """Tests download of course data and archive creation."""
        self.upload_all_sample_course_files([])
        self.import_sample_course()
        etl.main(self.download_args, environment_class=FakeEnvironment)
        # Don't use Archive and Manifest here because we want to test the raw
        # structure of the emitted zipfile.
        zip_archive = zipfile.ZipFile(self.archive_path)
        manifest = transforms.loads(
            zip_archive.open(etl._MANIFEST_FILENAME).read())
        self.assertGreaterEqual(
            courses.COURSE_MODEL_VERSION_1_3, manifest['version'])
        self.assertEqual(
            'course:%s::ns_test' % self.url_prefix, manifest['raw'])
        for entity in manifest['entities']:
            self.assertTrue(entity.has_key('is_draft'))
            self.assertTrue(zip_archive.open(entity['path']))

    def test_download_errors_if_archive_path_exists_on_disk(self):
        self.upload_all_sample_course_files([])
        self.import_sample_course()
        etl.main(self.download_args, environment_class=FakeEnvironment)
        self.assertRaises(
            SystemExit, etl.main, self.download_args,
            environment_class=FakeEnvironment)

    def test_download_errors_if_course_url_prefix_does_not_exist(self):
        sites.reset_courses()
        self.assertRaises(
            SystemExit, etl.main, self.download_args,
            environment_class=FakeEnvironment)

    def test_download_errors_if_course_version_is_pre_1_3(self):
        args = etl._PARSER.parse_args(
            ['download', 'course', '/'] + self.common_args[2:])
        self.upload_all_sample_course_files([])
        self.import_sample_course()
        self.assertRaises(
            SystemExit, etl.main, args, environment_class=FakeEnvironment)

    def test_upload_fails_if_archive_cannot_be_opened(self):
        sites.setup_courses(self.raw)
        self.assertRaises(
            SystemExit, etl.main, self.upload_args,
            environment_class=FakeEnvironment)

    def test_upload_fails_if_archive_course_json_malformed(self):
        self.create_archive()
        self.create_empty_course(self.raw)
        zip_archive = zipfile.ZipFile(self.archive_path, 'a')
        zip_archive.writestr(etl._COURSE_JSON_PATH, 'garbage')
        zip_archive.close()
        self.assertRaises(
            SystemExit, etl.main, self.upload_args,
            environment_class=FakeEnvironment)

    def test_upload_fails_if_archive_course_yaml_malformed(self):
        self.create_archive()
        self.create_empty_course(self.raw)
        zip_archive = zipfile.ZipFile(self.archive_path, 'a')
        zip_archive.writestr(etl._COURSE_YAML_PATH, '{')
        zip_archive.close()
        self.assertRaises(
            SystemExit, etl.main, self.upload_args,
            environment_class=FakeEnvironment)

    def test_upload_fails_if_course_with_units_found(self):
        self.upload_all_sample_course_files([])
        self.import_sample_course()
        self.assertRaises(
            SystemExit, etl.main, self.upload_args,
            environment_class=FakeEnvironment)

    def test_upload_fails_if_no_course_with_url_prefix_found(self):
        self.create_archive()
        self.assertRaises(
            SystemExit, etl.main, self.upload_args,
            environment_class=FakeEnvironment)

    def test_upload_succeeds(self):
        """Tests upload of archive contents."""

        self.create_archive()
        self.create_empty_course(self.raw)
        context = etl._get_requested_context(
            sites.get_all_courses(), self.upload_args.course_url_prefix)
        self.assertNotEqual(self.new_course_title, context.get_title())
        etl.main(self.upload_args, environment_class=FakeEnvironment)
        archive = etl._Archive(self.archive_path)
        archive.open('r')
        context = etl._get_requested_context(
            sites.get_all_courses(), self.upload_args.course_url_prefix)
        filesystem_contents = context.fs.impl.list(appengine_config.BUNDLE_ROOT)
        self.assertEqual(
            len(archive.manifest.entities), len(filesystem_contents))
        self.assertEqual(self.new_course_title, context.get_title())
        units = etl._get_course_from(context).get_units()
        spot_check_single_unit = [u for u in units if u.unit_id == 9][0]
        self.assertEqual('Interpreting results', spot_check_single_unit.title)
        for unit in units:
            self.assertTrue(unit.title)
        for entity in archive.manifest.entities:
            full_path = os.path.join(appengine_config.BUNDLE_ROOT, entity.path)
            stream = context.fs.impl.get(full_path)
            self.assertEqual(entity.is_draft, stream.metadata.is_draft)


# TODO(johncox): re-enable these tests once we figure out how to make webtest
# play nice with remote_api.
class EtlRemoteEnvironmentTestCase(actions.TestBase):
    """Tests tools/etl/remote.py."""

    # Method name determined by superclass. pylint: disable-msg=g-bad-name
    def setUp(self):
        super(EtlRemoteEnvironmentTestCase, self).setUp()
        self.test_environ = copy.deepcopy(os.environ)

    # Allow access to protected members under test.
    # pylint: disable-msg=protected-access
    def disabled_test_can_establish_environment_in_dev_mode(self):
        # Stub the call that requires user input so the test runs unattended.
        self.swap(__builtin__, 'raw_input', lambda _: 'username')
        self.assertEqual(os.environ['SERVER_SOFTWARE'], remote.SERVER_SOFTWARE)
        # establish() performs RPC. If it doesn't throw, we're good.
        remote.Environment('mycourse', 'localhost:8080').establish()

    def disabled_test_can_establish_environment_in_test_mode(self):
        self.test_environ['SERVER_SOFTWARE'] = remote.TEST_SERVER_SOFTWARE
        self.swap(os, 'environ', self.test_environ)
        # establish() performs RPC. If it doesn't throw, we're good.
        remote.Environment('mycourse', 'localhost:8080').establish()


class CourseUrlRewritingTest(CourseUrlRewritingTestBase):
    """Run all existing tests using '/courses/pswg' base URL rewrite rules."""


class VirtualFileSystemTest(VirtualFileSystemTestBase):
    """Run all existing tests using virtual local file system."""


class MemcacheTestBase(actions.TestBase):
    """Executes all tests with memcache enabled."""

    def setUp(self):  # pylint: disable-msg=g-bad-name
        super(MemcacheTestBase, self).setUp()
        config.Registry.test_overrides = {models.CAN_USE_MEMCACHE.name: True}

    def tearDown(self):  # pylint: disable-msg=g-bad-name
        config.Registry.test_overrides = {}
        super(MemcacheTestBase, self).setUp()


class MemcacheTest(MemcacheTestBase):
    """Executes all tests with memcache enabled."""


ALL_COURSE_TESTS = (
    StudentAspectTest, AssessmentTest, CourseAuthorAspectTest,
    StaticHandlerTest, AdminAspectTest)

MemcacheTest.__bases__ += (InfrastructureTest,) + ALL_COURSE_TESTS
CourseUrlRewritingTest.__bases__ += ALL_COURSE_TESTS
VirtualFileSystemTest.__bases__ += ALL_COURSE_TESTS
DatastoreBackedSampleCourseTest.__bases__ += ALL_COURSE_TESTS
