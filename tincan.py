# Copyright 2012 Salbox Services LLC. All Rights Reserved.
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


import httplib, urllib, logging, base64, os
import webapp2
import client
from controllers import tincan_extras


class StatementsHandler(webapp2.RequestHandler):
    def post(self):
        run_example()
        statements_json = self.request.body
        connection = httplib.HTTPConnection(os.environ['LRS_DOMAIN'])
        connection.request('POST', os.environ['STATEMENTS_PATH']+'/write', statements_json, {
            'x-experience-api-version': '0.95',
            'Authorization': 'Basic ' + base64.b64encode('%(LRS_USERNAME)s:%(LRS_PASSWORD)s' % os.environ),
            'content-type': 'application/json',

        })
        response = connection.getresponse()
        connection.close()
        logging.info(os.environ['STATEMENTS_PATH']+'/write')
        return webapp2.Response(status='%s %s' % (response.status, response.reason), body=response.read())


urls = [
  ('/tincan/statements', StatementsHandler),
  ('/tincan/check_answers', tincan_extras.CheckAnswerHandler),
  ('/tincan/watch_video', tincan_extras.WatchVideoHandler),
  ('/tincan/assessment_check', tincan_extras.AssessmentCheckHandler)
  ]

app = webapp2.WSGIApplication(urls, debug=True)
