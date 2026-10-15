"""Foreground single-process entrypoint with signal-time admission closure."""
import asyncio
import logging
import os
from threading import Thread
import sys
import time
import uvicorn
from .app import create_app
from .settings import Settings


class Server(uvicorn.Server):
    def __init__(self,config):
        super().__init__(config)
        self.shutdown_deadline=None
        self.shutdown_started=None

    def application(self):
        app=getattr(self.config,'loaded_app',None)
        if app is None and not isinstance(self.config.app,str): app=self.config.app
        while not hasattr(app,'state') and hasattr(app,'app'): app=app.app
        return app if hasattr(app,'state') else None

    def begin_shutdown(self):
        if self.shutdown_deadline is None:
            self.shutdown_started=time.monotonic()
            self.shutdown_deadline=self.shutdown_started+30
            logging.getLogger('auto_plan.runtime').info('shutdown begin monotonic=%.6f deadline_seconds=30',self.shutdown_started)
            # Independent of the event loop and blocked Oracle/resource cleanup.
            Thread(target=self.enforce_deadline,daemon=True).start()
        app=self.application()
        if app is not None:
            app.state.readiness.deadline=self.shutdown_deadline
            app.state.readiness.begin_shutdown(app.state.job_controller)
        self.config.timeout_graceful_shutdown=max(0,self.shutdown_deadline-time.monotonic())
        return self.shutdown_deadline

    def enforce_deadline(self):
        # Reserve the last two seconds of the same deadline for group reaping
        # and container finalization, including synchronous blocked cleanup.
        # Stay armed through asyncio runner and interpreter finalization.
        time.sleep(max(0,self.shutdown_deadline-time.monotonic()-2))
        app=self.application()
        controller=getattr(app.state,'job_controller',None) if app else None
        # Do not wait on a DB thread/lock. Each child owns an isolated process group.
        if controller:
            for run in list(controller.runs.values()):
                run.process.signal_kill()
            for run in list(controller.runs.values()):
                run.process.reap(self.shutdown_deadline-1)
        print('ERROR auto_plan.runtime shutdown deadline guard elapsed_seconds=%.6f; startup will recover interrupted jobs' % (time.monotonic()-self.shutdown_started),flush=True)
        os._exit(0)

    def handle_exit(self,sig,frame):
        self.begin_shutdown()
        super().handle_exit(sig,frame)

    async def shutdown(self, sockets=None):
        deadline=self.begin_shutdown()
        try:
            async with asyncio.timeout(max(0,deadline-time.monotonic())):
                await super().shutdown(sockets)
        except TimeoutError:
            logging.getLogger('auto_plan.runtime').error('shutdown drain deadline reached')
        else:
            logging.getLogger('auto_plan.runtime').info('shutdown completed elapsed_seconds=%.6f',time.monotonic()-self.shutdown_started)


def main():
    logging.basicConfig(level=logging.INFO,stream=sys.stdout,format='%(levelname)s %(name)s %(message)s')
    try:
        settings=Settings()
        app=create_app(settings)
    except Exception:
        # Pydantic ValidationError may include the complete input environment.
        print('ERROR auto_plan.runtime configuration invalid; inspect operator configuration',flush=True)
        return 2
    config=uvicorn.Config(app,host='0.0.0.0',port=8000,workers=1,access_log=False,
        timeout_graceful_shutdown=30,log_config=None,proxy_headers=True,forwarded_allow_ips=settings.proxy_trusted_ips)
    Server(config).run()
    return 0


if __name__=='__main__': sys.exit(main())
