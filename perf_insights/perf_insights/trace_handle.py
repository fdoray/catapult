# Copyright (c) 2015 The Chromium Authors. All rights reserved.
# Use of this source code is governed by a BSD-style license that can be
# found in the LICENSE file.
import uuid

class TraceHandle(object):
  def __init__(self, run_info, metadata):
    self.run_info = run_info
    self.metadata = metadata

  def Open():
    # Returns a with-able object containing a name.
    raise NotImplemented()
