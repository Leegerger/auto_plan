"""The test-only ASGI observer neither reads ahead nor replaces body messages."""
import asyncio
import hashlib
import json
from scripts.acceptance.upload_observer import UploadReceiveObserver


def test_unconsumed_tail_remains_incomplete_and_original_chunk_is_unchanged(tmp_path):
    first = {'type':'http.request','body':b'abc','more_body':True}
    tail = {'type':'http.request','body':b'tail','more_body':False}
    queued = [first, tail]
    delivered, sent = [], []
    async def receive():
        value = queued.pop(0)
        delivered.append(value)
        return value
    async def send(value):
        sent.append(value)
    async def app(scope, receive, send):
        assert await receive() is first
        await send({'type':'http.response.start','status':422,'headers':[]})
        await send({'type':'http.response.body','body':b'rejected'})
    path = tmp_path/'received.ndjson'
    observer = UploadReceiveObserver(app, path)
    asyncio.run(observer({'type':'http','method':'POST','path':'/planning/api/initialization/excel/preview',
        'query_string':b'base_revision=0&input_version=1&latest_adoption=0'}, receive, send))
    assert delivered == [first] and queued == [tail]
    observed = json.loads(path.read_text())
    assert observed['complete'] is False and observed['response_complete'] is True
    assert observed['length'] == 3 and observed['sha256'] == hashlib.sha256(b'abc').hexdigest()
    assert observed['status'] == 422
    assert dict(sent[0]['headers'])[b'x-auto-plan-test-upload'].decode() == observed['id']
    assert sent[1] == {'type':'http.response.body','body':b'rejected'}


def test_only_final_original_chunk_completes_receipt(tmp_path):
    chunks = [{'type':'http.request','body':b'\x00\xff','more_body':True},
        {'type':'http.request','body':b'\x80','more_body':False}]
    delivered = iter(chunks)
    async def receive(): return next(delivered)
    async def send(value): pass
    async def app(scope, receive, send):
        assert await receive() is chunks[0]
        assert await receive() is chunks[1]
        await send({'type':'http.response.start','status':400,'headers':[]})
        await send({'type':'http.response.body','body':b'invalid'})
    path = tmp_path/'received.ndjson'
    asyncio.run(UploadReceiveObserver(app,path)({'type':'http','method':'POST',
        'path':'/planning/api/initialization/excel/preview','query_string':b'input_version=1'},receive,send))
    observed = json.loads(path.read_text())
    assert observed['complete'] is True and observed['response_complete'] is True
    assert observed['length'] == 3 and observed['sha256'] == hashlib.sha256(b'\x00\xff\x80').hexdigest()
