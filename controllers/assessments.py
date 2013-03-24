# Copyright 2012 Google Inc. All Rights Reserved.
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

"""Classes and methods to manage all aspects of student assessments."""

__author__ = 'pgbovine@google.com (Philip Guo)'

import datetime
import logging
import json, os, uuid
from models import models
from models import transforms
from models import utils
from models.models import Student
from models.models import StudentAnswersEntity
from tools import verify
from utils import BaseHandler
from google.appengine.ext import db
from google.appengine.api import users, memcache, taskqueue


def store_score(course, student, assessment_type, score):
    """Stores a student's score on a particular assessment.

    Args:
        course: the course containing the assessment.
        student: the student whose data is stored.
        assessment_type: the type of the assessment.
        score: the student's score on this assessment.

    Returns:
        the result of the assessment, if appropriate.
    """
    # FIXME: Course creators can edit this code to implement custom
    # assessment scoring and storage behavior
    # TODO(pgbovine): Note that the latest version of answers are always saved,
    # but scores are only saved if they're higher than the previous attempt.
    # This can lead to unexpected analytics behavior. Resolve this.
    existing_score = course.get_score(student, assessment_type)
    # remember to cast to int for comparison
    if (existing_score is None) or (score > int(existing_score)):
        utils.set_score(student, assessment_type, score)

    return course.get_overall_result(student)


class AnswerHandler(BaseHandler):
    """Handler for saving assessment answers."""
    def tincan(self, course_score=None, course_success=None):
        tincan_actor = {
          'mbox': 'mailto:' + self.personalize_page_and_get_enrolled().key().name()
        }
        tincan_timestamp = datetime.datetime.utcnow().isoformat() + 'Z'
        tincan_course_activity = {
          'id': self.request.host_url,
          'definition': {
            'name': {'en': os.environ['COURSE_NAME']},
            'description': {
              'en': os.environ['COURSE_DESCRIPTION']
            }
          }
        }

        assessment_type = self.request.get('assessment_type')
        #default to totally random when impossible to determine
        tincan_assessment_uri = os.environ.get('HTTP_REFERER', 'urn:uuid:' + str(uuid.uuid4()))
        tincan_assessment_activity = {
          'id': tincan_assessment_uri,
          'definition': {
            'name': {
              'en':  assessment_type + ' assessment'
            }
          }
        }

        tincan_assessment_statement = {
          'timestamp': tincan_timestamp,
          'id': str(uuid.uuid4()),
          'actor': tincan_actor,
          'verb': {
            'id': 'http://adlnet.gov/expapi/verbs/completed',
            'display': {
              'en': 'completed'
            }
          },
          'object': tincan_assessment_activity,
          'result': {
            'score': {
              'scaled': float(self.request.get('score', 0)) / 100
            },
            'completion': True
          },
          'context': {
            'contextActivities': {
              'parent': tincan_course_activity
            }
          }
        }
        statements = [tincan_assessment_statement]


        if course_score is not None and course_success is not None:
          statements.append({
            'timestamp': tincan_timestamp,
            'id': str(uuid.uuid4()),
            'actor': tincan_actor,
            'verb': {
              'id': 'http://adlnet.gov/expapi/verbs/completed',
              'display': {
                'en': 'completed'
              }
            },
            'object': tincan_course_activity,
            'result': { #to be filled in during processing below
              'score': {
                'scaled': course_score
              },
              'success': course_success,
              'completion': True
            }
          })

        question_identifier = 0
        while(str(question_identifier) in self.request.POST):
          question = str(question_identifier)
          tincan_question_statement = {
            'timestamp': tincan_timestamp,
            'id': str(uuid.uuid4()),
            'actor': tincan_actor,
              'verb': {
              'id': 'http://adlnet.gov/expapi/verbs/answered',
              'display': {
                'en': 'answered'
              }
            },
            'object': {
              'id': tincan_assessment_uri + '#' + question,
              'definition': {
                'name': {
                  'en': assessment_type + ' question #' + question
                }
              }
            },
            'result': {
              'completion': True,
              'success': self.request.POST[question] == 'true'
            },
            'context': {
              'contextActivities': {
                'parent': tincan_assessment_activity,
                'other': tincan_course_activity
              }
            }
          }
          statements.append(tincan_question_statement)
          question_identifier += 1

        taskqueue.add(url='/tincan/statements', method='POST', payload=json.dumps(statements))

    # Find student entity and save answers
    @db.transactional(xg=True)
    def update_assessment_transaction(
        self, email, assessment_type, new_answers, score):
        """Stores answer and updates user scores.

        Args:
            email: the student's email address.
            assessment_type: the type of the assessment (as stated in unit.csv).
            new_answers: the latest set of answers supplied by the student.
            score: the numerical assessment score.

        Returns:
            the result of the assessment, if appropriate.
        """
        student = Student.get_enrolled_student_by_email(email)
        course = self.get_course()

        # It may be that old Student entities don't have user_id set; fix it.
        if not student.user_id:
            student.user_id = self.get_user().user_id()

        answers = StudentAnswersEntity.get_by_key_name(student.user_id)
        if not answers:
            answers = StudentAnswersEntity(key_name=student.user_id)
        answers.updated_on = datetime.datetime.now()

        utils.set_answer(answers, assessment_type, new_answers)

        result = store_score(course, student, assessment_type, score)

        student.put()
        answers.put()

        # Also record the event, which is useful for tracking multiple
        # submissions and history.
        models.EventEntity.record(
            'submit-assessment', self.get_user(), transforms.dumps({
                'type': 'assessment-%s' % assessment_type,
                'values': new_answers, 'location': 'AnswerHandler'}))

        return student, result

    def post(self):
        """Handles POST requests."""
        student = self.personalize_page_and_get_enrolled()
        if not student:
            return

        if not self.assert_xsrf_token_or_fail(self.request, 'assessment-post'):
            return

        course = self.get_course()
        assessment_type = self.request.get('assessment_type')
        unit = course.find_unit_by_id(assessment_type)
        if not assessment_type:
            logging.error('No assessment type supplied.')
            return
        if unit is None or not unit.type == verify.UNIT_TYPE_ASSESSMENT:
            logging.error('No assessment named %s exists.', assessment_type)
            return

        # Convert answers from JSON to dict.
        answers = self.request.get('answers')
        if answers:
            answers = transforms.loads(answers)
        else:
            answers = []

        # TODO(pgbovine): consider storing as float for better precision
        score = int(round(float(self.request.get('score'))))

        # Record score.
        student, result = self.update_assessment_transaction(
            student.key().name(), assessment_type, answers, score)

        # Record completion event in progress tracker.
        course.get_progress_tracker().put_assessment_completed(
            student, assessment_type)

        self.template_value['navbar'] = {'course': True}
        self.template_value['assessment'] = assessment_type
        self.template_value['result'] = result
        self.template_value['score'] = score

        self.template_value['assessment_name'] = unit.title
        self.template_value['is_last_assessment'] = (
            course.is_last_assessment(unit))

        self.template_value['overall_score'] = course.get_overall_score(student)

        #call tincan
        
        if assessment_type == 'postcourse_pass':
            self.tincan(course.get_overall_score(student) / 100.0, True)
        elif assessment_type == 'postcourse_fail':
            self.tincan(course.get_overall_score(student) / 100.0, False)
        else:
            self.tincan()

        self.render('test_confirmation.html')
