#
# Copyright 2015-2017 Red Hat, Inc.
#
# This program is free software; you can redistribute it and/or modify
# it under the terms of the GNU General Public License as published by
# the Free Software Foundation; either version 2 of the License, or
# (at your option) any later version.
#
# This program is distributed in the hope that it will be useful,
# but WITHOUT ANY WARRANTY; without even the implied warranty of
# MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.  See the
# GNU General Public License for more details.
#
# You should have received a copy of the GNU General Public License
# along with this program; if not, write to the Free Software
# Foundation, Inc., 51 Franklin Street, Fifth Floor, Boston, MA 02110-1301 USA
#
# Refer to the README and COPYING files for full details of the license
#
import itertools
from collections import deque

from testlib import VdsmTestCase as TestCaseBase
from yajsonrpc.stomp import (
    AsyncDispatcher,
    Command,
    Frame,
    Headers,
    DEFAULT_INTERVAL
)


class FakeConnection(object):

    def __init__(self):
        self.closed = False

    def close(self):
        self.closed = True


class FakeFrameHandler(object):

    def __init__(self):
        self.handle_connect_called = False
        self._outbox = deque()

    def handle_connect(self, dispatcher):
        self.handle_connect_called = True

    def handle_frame(self, dispatcher, frame):
        self.queue_frame(frame)

    def handle_timeout(self, dispatcher):
        dispatcher.connection.close()

    def peek_message(self):
        return self._outbox[0]

    def pop_message(self):
        return self._outbox.popleft()

    @property
    def has_outgoing_messages(self):
        return (len(self._outbox) > 0)

    def queue_frame(self, frame):
        self._outbox.append(frame)


class FakeAsyncDispatcher(object):

    socket = None

    def __init__(self, data):
        self._data = data

    def recv(self, buffer_size):
        return self._data

    def send(self, data):
        return len(data)


class FakeTimeGen(object):

    def __init__(self, list):
        self._chain = itertools.chain(list)

    def get_fake_time(self):
        next_time = next(self._chain)
        print(next_time)
        return next_time


class AsyncDispatcherTest(TestCaseBase):

    def test_handle_connect(self):
        frame_handler = FakeFrameHandler()
        dispatcher = AsyncDispatcher(FakeConnection(), frame_handler)

        dispatcher.handle_connect(None)

        self.assertTrue(frame_handler.handle_connect_called)

    def test_handle_read(self):
        frame_handler = FakeFrameHandler()
        headers = {Headers.CONTENT_LENGTH: '78',
                   Headers.DESTINATION: 'jms.topic.vdsm_responses',
                   Headers.CONTENT_TYPE: 'application/json',
                   Headers.SUBSCRIPTION: 'ad052acb-a934-4e10-8ec3-00c7417ef8d'}
        body = ('{"jsonrpc": "2.0", "id": "e8a936a6-d886-4cfa-97b9-2d54209053f'
                'f", "result": []}')
        frame = Frame(command=Command.MESSAGE, headers=headers, body=body)
        dispatcher = AsyncDispatcher(FakeConnection(), frame_handler)

        dispatcher.handle_read(FakeAsyncDispatcher(frame.encode()))

        self.assertTrue(frame_handler.has_outgoing_messages)
        recv_frame = frame_handler.pop_message()
        self.assertEqual(Command.MESSAGE, recv_frame.command)
        self.assertEqual(body, recv_frame.body)

    def test_heartbeat_calc(self):
        dispatcher = AsyncDispatcher(
            FakeConnection(), FakeFrameHandler(),
            clock=FakeTimeGen([4000000.0, 4000002.0]).get_fake_time
        )
        dispatcher.setHeartBeat(8000, 0)

        self.assertEqual(6, dispatcher.next_check_interval())

    def test_heartbeat_exceeded(self):
        frame_handler = FakeFrameHandler()
        dispatcher = AsyncDispatcher(
            FakeConnection(), frame_handler,
            clock=FakeTimeGen([4000000.0, 4000012.0]).get_fake_time
        )
        dispatcher.setHeartBeat(8000, 0)

        self.assertTrue(dispatcher.writable(None))
        self.assertTrue(frame_handler.has_outgoing_messages)

    def test_incoming_heartbeat_exceeded(self):
        frame_handler = FakeFrameHandler()
        connection = FakeConnection()
        dispatcher = AsyncDispatcher(
            connection, frame_handler,
            clock=FakeTimeGen(
                [4000000.0, 4000003.0, 4000006.0, 4000009.0]).get_fake_time)

        dispatcher.setHeartBeat(12000, 4000)
        self.assertFalse(dispatcher.writable(None))
        self.assertFalse(frame_handler.has_outgoing_messages)
        self.assertTrue(connection.closed)

    def test_no_heartbeat(self):
        dispatcher = AsyncDispatcher(FakeConnection(), FakeFrameHandler())
        dispatcher.setHeartBeat(0, 0)

        self.assertEqual(dispatcher.next_check_interval(), DEFAULT_INTERVAL)

    def test_no_outgoing_heartbeat(self):
        dispatcher = AsyncDispatcher(FakeConnection(), FakeFrameHandler())
        dispatcher = AsyncDispatcher(
            FakeConnection(), FakeFrameHandler(),
            clock=FakeTimeGen([4000000.0, 4000002.0, 4000004.0,
                               4000006.0]).get_fake_time
        )
        dispatcher.setHeartBeat(0, 8000)
        self.assertEqual(dispatcher.next_check_interval(), DEFAULT_INTERVAL)

    def test_handle_write(self):
        headers = {Headers.CONTENT_LENGTH: '78',
                   Headers.DESTINATION: 'jms.topic.vdsm_responses',
                   Headers.CONTENT_TYPE: 'application/json',
                   Headers.SUBSCRIPTION: 'ad052acb-a934-4e10-8ec3-00c7417ef8d'}
        body = ('{"jsonrpc": "2.0", "id": "e8a936a6-d886-4cfa-97b9-2d54209053f'
                'f", "result": []}')
        frame = Frame(command=Command.MESSAGE, headers=headers, body=body)
        frame_handler = FakeFrameHandler()
        frame_handler.handle_frame(None, frame)

        dispatcher = AsyncDispatcher(FakeConnection(), frame_handler)
        self.assertTrue(dispatcher.writable(None))

        dispatcher.handle_write(FakeAsyncDispatcher(''))
        self.assertFalse(frame_handler.has_outgoing_messages)

    def test_handle_close(self):
        connection = FakeConnection()
        dispatcher = AsyncDispatcher(connection, FakeFrameHandler())

        dispatcher.handle_close(None)

        self.assertTrue(connection.closed)
