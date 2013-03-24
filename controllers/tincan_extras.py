
import logging, json, datetime, os, uuid

from google.appengine.api import users, memcache, taskqueue

from utils import StudentEditStudentHandler
from models.models import Student

class WatchVideoHandler(StudentEditStudentHandler):
    def post(self):
        student = self.getStudent()
        video = self.request.get('video')
        duration = int(float(self.request.get('duration')))
        completion = self.request.get('completion') == 'true';
        if student and video and duration:
          tincan_actor = {
            'mbox': 'mailto:' + student.key().name()
          }
          tincan_timestamp = datetime.datetime.utcnow().isoformat() + 'Z'
          #TODO: change as appropriate for course
          tincan_course_activity = {
            'id': self.request.host_url,
            'definition': {
              'name': {'en': os.environ['COURSE_NAME']},
              'description': {
                'en': os.environ['COURSE_DESCRIPTION']
              }
            }
          }

          #default to totally random when impossible to determine
          #TODO: verify this is working in this context
          tincan_parent_uri = os.environ.get('HTTP_REFERER', 'urn:uuid:' + str(uuid.uuid4()))
          tincan_parent_activity = {
            'id': tincan_parent_uri,
            #TODO: get the name from somewhere?
            # 'definition': {
            #   'name': {
            #     'en': ''
            #   }
            # }
          }

          verb = {
              #TODO: consider options here
              'id': 'http://saltbox.com/verbs/assessment#watched',
              'display': {
                'en': 'watched'
              }
            }

          tincan_watch_video_statement = {
            'timestamp': tincan_timestamp,
            'id': str(uuid.uuid4()),
            'actor': tincan_actor,
            'verb': verb,
            'object': {
                'id': video
            },
            'result': {
              'completion': completion,
              'duration': 'PT%sS' % duration
            },
            'context': {
              'contextActivities': {
                'parent': tincan_parent_activity,
                'other': tincan_course_activity
              }
            }
          }
          statements = [tincan_watch_video_statement]

          taskqueue.add(url='/tincan/statements', method='POST', payload=json.dumps(statements))


class AssessmentCheckHandler(StudentEditStudentHandler):
    def post(self):
        student = self.getStudent()
        score = float(self.request.get('score'))
        if student:
          tincan_actor = {
            'mbox': 'mailto:' + student.key().name()
          }
          tincan_timestamp = datetime.datetime.utcnow().isoformat() + 'Z'
          #TODO: change as appropriate for course
          tincan_course_activity = {
            'id': self.request.host_url,
            'definition': {
              'name': {'en': os.environ['COURSE_NAME']},
              'description': {
                'en': os.environ['COURSE_DESCRIPTION']
              }
            }
          }

          #default to totally random when impossible to determine
          #TODO: verify this is working in this context
          tincan_activity_uri = os.environ.get('HTTP_REFERER', 'urn:uuid:' + str(uuid.uuid4()))
          tincan_activity = {
            'id': tincan_activity_uri,
            #TODO: get the name from somewhere?
            # 'definition': {
            #   'name': {
            #     'en': ''
            #   }
            # }
          }
          verb = {
              #TODO: consider options here
              'id': 'http://saltbox.com/verbs/assessment#checked_assessment',
              'display': {
                'en': 'checked assessment'
              }
            }

          tincan_assessment_check_statement = {
            'timestamp': tincan_timestamp,
            'id': str(uuid.uuid4()),
            'actor': tincan_actor,
            'verb': verb,
            'object': tincan_activity,
            'result': {
              'completion': False,
              'score': {
                'scaled': score
              }
            },
            'context': {
              'contextActivities': {
                'other': tincan_course_activity
              }
            }
          }
          statements = [tincan_assessment_check_statement]

          taskqueue.add(url='/tincan/statements', method='POST', payload=json.dumps(statements))


class CheckAnswerHandler(StudentEditStudentHandler):
    def post(self):
        student = self.getStudent()
        question = self.request.get('question')
        success = self.request.get('success') == 'true'
        skipped = self.request.get('skipped') == 'true'
        if student and question:
          tincan_actor = {
            'mbox': 'mailto:' + student.key().name()
          }
          tincan_timestamp = datetime.datetime.utcnow().isoformat() + 'Z'
          #TODO: change as appropriate for course
          tincan_course_activity = {
            'id': self.request.host_url,
            'definition': {
              'name': {'en': os.environ['COURSE_NAME']},
              'description': {
                'en': os.environ['COURSE_DESCRIPTION']
              }
            }
          }

          #default to totally random when impossible to determine
          #TODO: verify this is working in this context
          tincan_parent_uri = os.environ.get('HTTP_REFERER', 'urn:uuid:' + str(uuid.uuid4()))
          tincan_parent_activity = {
            'id': tincan_parent_uri,
            #TODO: get the name from somewhere?
            # 'definition': {
            #   'name': {
            #     'en': ''
            #   }
            # }
          }
          verb_uri = 'http://saltbox.com/verbs/assessment#skipped_answer' if skipped else 'http://saltbox.com/verbs/assessment#checked_answer'
          verb_display = 'skipped' if skipped else 'checked answer for'
          verb = {
              #TODO: consider options here
              'id': verb_uri,
              'display': {
                'en': verb_display
              }
            }

          tincan_check_answer_statement = {
            'timestamp': tincan_timestamp,
            'id': str(uuid.uuid4()),
            'actor': tincan_actor,
            'verb': verb,
            'object': {
                'id': tincan_parent_uri + '#' + question,
                'definition': {
                  'name': {
                    'en': tincan_parent_uri + ' question tag #' + question
                  }
                }
            },
            'result': {
              'completion': False,
              'success': success
            },
            'context': {
              'contextActivities': {
                'parent': tincan_parent_activity,
                'other': tincan_course_activity
              }
            }
          }
          statements = [tincan_check_answer_statement]

          taskqueue.add(url='/tincan/statements', method='POST', payload=json.dumps(statements))
