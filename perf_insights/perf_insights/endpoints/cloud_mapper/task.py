# Copyright (c) 2015 The Chromium Authors. All rights reserved.
# Use of this source code is governed by a BSD-style license that can be
# found in the LICENSE file.
import json
import logging
import os
import time
import urllib
import uuid
import webapp2

from google.appengine.api import urlfetch
from perf_insights.endpoints.cloud_mapper import cloud_helper
from perf_insights.endpoints.cloud_mapper import job_info


def _is_devserver():
  return os.environ.get('SERVER_SOFTWARE','').startswith('Development')


_DEFAULT_TARGET = 'prod'
if _is_devserver():
  _DEFAULT_TARGET = 'test'

_DEFAULT_BUCKET_NAME = 'performance-insights-cloud-mapper'
_DEFAULT_BUCKET_PATH = 'gs://%s/%s/jobs/' % (
    _DEFAULT_BUCKET_NAME, _DEFAULT_TARGET)
_PERFORMANCE_INSIGHTS_URL = 'https://performance-insights.appspot.com'

_STARTUP_SCRIPT = \
"""#!/bin/bash
cd /catapult
git pull
git checkout {revision}

# WARNING: A lot of hardcoded things in here.
mkdir /tmp/perf-insights
gsutil cp gs://performance-insights-cloud-mapper/download_traces.py\
 /catapult/perf_insights/bin
python perf_insights/bin/download_traces.py --dir=/tmp/perf-insights --gcs={gcs}
perf_insights/bin/map_traces -c local-directory\
 --trace_directory=/tmp/perf-insights\
 perf_insights/perf_insights/mappers/task_info_map_function.html\
 --output /tmp/output.txt -j 32
gsutil cp /tmp/output.txt\
 gs://performance-insights-cloud-mapper/test/jobs/{gcs}.result
"""


class TaskPage(webapp2.RequestHandler):

  def _QueryForTraces(self, query):
    payload = urllib.urlencode({'q': query})
    query_url = '%s/query?%s' % (_PERFORMANCE_INSIGHTS_URL, payload)
    result = urlfetch.fetch(url=query_url,
                            payload=payload,
                            method=urlfetch.GET,
                            follow_redirects=False,
                            deadline=10)
    logging.info(result.content)

    return json.loads(result.content)

  def _DispatchTracesAndWaitForResult(self, job, traces, num_instances):
    def _slice_it(li, cols=2):
      start = 0
      for i in xrange(cols):
          stop = start + len(li[i::cols])
          yield li[start:stop]
          start = stop

    tasks = []

    helper = cloud_helper.CloudHelper()

    # Split the traces up into N buckets.
    for current_traces in _slice_it(traces, num_instances):
      taskid = str(uuid.uuid4())
      current_task = {
          'id': taskid,
          'gce_name': 'mr-%s' % taskid
      }

      helper.WriteGCS(_DEFAULT_BUCKET_PATH + taskid,
                            json.dumps(current_traces))

      startup_script = _STARTUP_SCRIPT.format(
          revision=job.revision, gcs=taskid)

      result = helper.CreateGCE(current_task['gce_name'], startup_script)

      logging.info('Call to instances().insert response:\n')
      for k, v in sorted(result.iteritems()):
        logging.info(' %s: %s' % (k, v))

      tasks.append(current_task)

    return self._WaitOnResults(helper, tasks)

  def _WaitOnResults(self, helper, tasks):
    # TODO: There's no reducer yet, so we can't actually collapse multiple
    # results into one results file.
    results = None
    while tasks:
      for current_task in tasks:
        task_results_path = '%s%s.result' % (
            _DEFAULT_BUCKET_PATH, current_task['id'])
        has_result = helper.ListGCS(task_results_path)
        if has_result.get('items'):
          logging.info(str(has_result))
          results = task_results_path
          helper.DeleteGCE(current_task['gce_name'])
          tasks.remove(current_task)
      time.sleep(1)
    logging.info("Finished all tasks.")
    return results

  def _RunMappers(self, job):
    # TODO(simonhatch): Figure out the optimal # of instances to spawn.
    num_instances = 1

    # TODO(simonhatch): Query is hardcoded, grab it from job.
    query = 'MAX_TRACE_HANDLES=10'

    # Get all the traces to process
    traces = self._QueryForTraces(query)

    return self._DispatchTracesAndWaitForResult(job, traces, num_instances)

  def post(self):
    self.response.headers['Content-Type'] = 'text/plain'

    jobid = self.request.get('jobid')
    job = job_info.JobInfo.get_by_id(jobid)
    if not job:
      return

    if job.status != 'QUEUED':
      return

    job.status = 'IN_PROGRESS'
    job.put()

    try:
      results_url = self._RunMappers(job)

      # TODO(simonhatch): Cleanup instances.

      job.status = 'COMPLETE'
      job.results = results_url
      job.put()
    except Exception as e:
      job.status = 'ERROR'
      job.put()
      raise e


app = webapp2.WSGIApplication([('/cloud_mapper/task', TaskPage)])