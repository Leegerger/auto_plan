"""TEST-ONLY passive receipt evidence for the disposable browser adapter."""
import hashlib
import json
import os
from urllib.parse import parse_qsl
from uuid import uuid4


class UploadReceiveObserver:
    def __init__(self, app, output):
        self.app, self.output = app, output

    async def __call__(self, scope, receive, send):
        if scope['type'] != 'http' or scope['method'] != 'POST' or scope['path'] != '/planning/api/initialization/excel/preview':
            return await self.app(scope, receive, send)
        digest, length, complete, status = hashlib.sha256(), 0, False, None
        identifier, response_complete = uuid4().hex, False

        async def observed_receive():
            nonlocal length, complete
            message = await receive()
            if message['type'] == 'http.request':
                chunk = message.get('body', b'')
                digest.update(chunk)
                length += len(chunk)
                if not message.get('more_body', False):
                    complete = True
            return message

        async def observed_send(message):
            nonlocal status, response_complete
            if message['type'] == 'http.response.start':
                status = message['status']
                message = message | {'headers': [*message.get('headers', []), (b'x-auto-plan-test-upload', identifier.encode())]}
            await send(message)
            if message['type'] == 'http.response.body' and not message.get('more_body', False):
                response_complete = True

        try:
            await self.app(scope, observed_receive, observed_send)
        finally:
            record = {'id': identifier, 'instrumentation': 'test-only passive ASGI receive',
                'method': scope['method'], 'path': scope['path'],
                'query': dict(parse_qsl(scope.get('query_string', b'').decode('ascii'))),
                'status': status, 'response_complete': response_complete,
                'length': length, 'sha256': digest.hexdigest(), 'complete': complete}
            # One append after the real handler/response. No body copy or read-ahead.
            descriptor = os.open(self.output, os.O_WRONLY | os.O_CREAT | os.O_APPEND, 0o600)
            try:
                os.write(descriptor, (json.dumps(record, separators=(',', ':'))+'\n').encode())
            finally:
                os.close(descriptor)
