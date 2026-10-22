"""Fresh, credential-free compute executable. Oracle is parent-only."""
import json
from pathlib import Path
import struct
import sys
from .job_codec import document, PAYLOAD_LIMIT
from .stored_snapshots import decode_stored_frozen_solve


def emit(value):
    payload=document(value)
    sys.stdout.buffer.write(struct.pack('!I',len(payload)))
    sys.stdout.buffer.write(payload)
    sys.stdout.buffer.flush()


def main():
    raw=sys.stdin.buffer.read(PAYLOAD_LIMIT+1)
    if len(raw)>PAYLOAD_LIMIT: raise ValueError('Frozen input too large')
    data=json.loads(raw)
    frozen=decode_stored_frozen_solve(data['frozen'])
    cancel=Path(data['cancel_file'])
    from .solver import solve
    result=solve(frozen,emit,lambda:cancel.exists())
    for candidate in result.candidates:
        emit({'kind':'candidate','candidate':candidate.model_dump(mode='json')})
    from .job_codec import digest
    metadata=result.model_dump(mode='json',exclude={'candidates'})
    emit({'kind':'result','result':metadata,'manifest':[digest(c.model_dump(mode='json')) for c in result.candidates]})


if __name__=='__main__':
    main()
