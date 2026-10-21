"""Real HTTP process with a deliberately stalled external cleanup boundary."""
from contextlib import asynccontextmanager
from pathlib import Path
import sys,time
from fastapi import APIRouter
import uvicorn
from auto_plan.app import create_app
from auto_plan.serve import Server
from auto_plan.settings import Settings
from tests.support import MemoryAccessStore,CompanyAuthentication

port,marker=sys.argv[1:]
@asynccontextmanager
async def stuck_cleanup(app):
    yield
    Path(marker).write_text('cleanup started')
    time.sleep(90)
app=create_app(Settings(),store=MemoryAccessStore(),authenticator=CompanyAuthentication(),routers=(APIRouter(lifespan=stuck_cleanup),))
Server(uvicorn.Config(app,host='127.0.0.1',port=int(port),log_level='error')).run()
