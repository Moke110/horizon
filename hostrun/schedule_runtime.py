"""Desktop scheduler runtime. Persistent claims own task identity; workers own processes."""
import queue
import threading
import time
from hostrun import agent_scheduling as schedules
from hostrun.desktop_worker import DesktopWorker
from hostrun import voyager_late_fulltext

class ScheduleRuntime:
    def __init__(self, service, token, resolved, version_provider, *, materials=None):
        self.service,self.token,self.resolved,self.version_provider=service,token,resolved,version_provider
        self.materials = materials
        self.workers={}
        self.stopped=False

    def task_ids(self):
        scheduled = {p['active_run'].get('task_id') for p in schedules.list_schedules(self.service,token=self.token) if p['active_run']}
        if self.materials is None:
            return scheduled
        with self.service._connect() as db:
            voyager_late_fulltext.initialize(db)
            return scheduled | {row[0] for row in db.execute("SELECT task_id FROM voyager_late_fulltext WHERE state='claimed'")}

    def step(self, *, now=None, foreground_busy=False):
        changed=False
        for identity,(worker,thread) in list(self.workers.items()):
            while not worker.events.empty():
                worker.events.get_nowait();changed=True
            if thread.is_alive():continue
            del self.workers[identity];changed=True
        plans=schedules.list_schedules(self.service,token=self.token)
        turns={t['turn_id']:t for t in self.service.list_turns(token=self.token)}
        for plan in plans:
            run=plan['active_run']
            if not run:continue
            turn=turns.get(run.get('turn_id'))
            if turn and turn['status']=='completed':
                coverage=schedules.execution_coverage(run,turn['result'])
                if coverage is None or coverage==run['from_at']:
                    schedules.hold_coverage(self.service,run['run_id'],token=self.token)
                    changed |= run.get('status')!='coverage_pending'
                else:
                    schedules.finish_run(self.service,run['run_id'],covered_until=coverage,token=self.token,defer_remainder=coverage<run['until_at'])
                    changed=True
            elif turn and turn['status']=='cancelled':
                schedules.hold_coverage(self.service,run['run_id'],token=self.token)
                changed |= run.get('status')!='coverage_pending'
        if self.stopped:return changed
        # Account for the interactive worker in the same main-task budget.
        schedules.tick(self.service,now=int(time.time()) if now is None else now,token=self.token,occupied=int(foreground_busy))
        limit=schedules.get_limits(self.service,token=self.token)['main']
        for plan in schedules.list_schedules(self.service,token=self.token):
            run=plan['active_run']
            if not run or run['run_id'] in self.workers:continue
            turn=turns.get(run.get('turn_id'))
            if turn is None and plan['status']!='active':continue
            if turn is None:turn=schedules.bind_run(self.service,run,token=self.token)
            if turn['status']!='running' or len(self.workers)+int(foreground_busy)>=limit:continue
            self._start_worker(run['run_id'], turn)
            changed = True
        if self.materials is not None:
            for identity, turn in voyager_late_fulltext.claim(
                    self.service, self.materials, token=self.token, occupied=int(foreground_busy)):
                if identity not in self.workers and len(self.workers)+int(foreground_busy)<limit:
                    self._start_worker(identity, turn)
                    changed = True
        return changed

    def _start_worker(self, identity, turn):
        worker=DesktopWorker();worker.home=self.service.home;worker.resolved=self.resolved
        worker.events=queue.Queue();worker.answers=queue.Queue();worker.closed=False;worker.cancelled=threading.Event();worker.process=None
        worker.service=self.service;worker.service_token=self.token;worker.version_provider=self.version_provider
        worker.task_id=turn['task_id'];worker.input_version=turn['input_version'];worker.turn=turn
        thread=threading.Thread(target=worker.run_agent,args=(turn['text'],turn['session_id']),daemon=True)
        self.workers[identity]=(worker,thread);thread.start()

    def stop(self):
        self.stopped=True
        for worker,thread in list(self.workers.values()):worker.stop()
