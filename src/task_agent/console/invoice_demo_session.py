"""Process-local cost gate; restarts never reactivate a demo or replay work."""

from contextlib import contextmanager
from datetime import UTC, datetime, timedelta
from uuid import uuid4

from task_agent.control.operations import OperationDeniedError


async def acquire_remote_demo(client, token):
    origin = 'https://ca-nemo-invoice-operator-dev.internal.jollybeach-503c7ed1.eastus.azurecontainerapps.io/invoices/demo-session'
    identifier = uuid4().hex
    headers = {'Authorization': 'Bearer ' + token}
    response = await client.post(origin + '/lease', headers=headers, json={'session_id': identifier}, timeout=15)
    if response.status_code != 200 or response.json() != {'lease_id': identifier}:
        raise OperationDeniedError('Demo admission unconfirmed; no Review SQL request started')
    async def release():
        response = await client.post(origin + '/release', headers=headers, json={'session_id': identifier}, timeout=15)
        if response.status_code != 200 or response.json() != {'lease_id': identifier, 'released': True}:
            raise OperationDeniedError('Review completion unconfirmed; inspect demo status, do not replay')
    return release


class InvoiceDemoSession:
    def __init__(self, *, clock=lambda: datetime.now(UTC), notify=lambda: None):
        self.clock, self.notify = clock, notify
        self.identifier = None
        self.owner = None
        self.expires_at = None
        self.state = 'idle'
        self.inflight = 0
        self.cleanup_claimed = False
        self.cleanup_result = None
        self.seen = set()
        self.review_leases = {}

    def status(self):
        if self.state == 'active' and self.clock() >= self.expires_at:
            self.state = 'ending'
            self.notify()
        return {'source': 'invoice-demo-session', 'session_id': self.identifier, 'state': self.state,
                'expires_at': self.expires_at.isoformat() if self.expires_at else None,
                'inflight': self.inflight, 'cleanup': self.cleanup_result}

    def start(self, identifier, owner):
        self.status()
        if self.identifier == identifier:
            if self.owner != owner:
                raise OperationDeniedError('Demo session belongs to another Operator')
            return self.status()
        if identifier in self.seen:
            raise OperationDeniedError('Completed demo request cannot be restarted')
        if self.state in ('active', 'ending') or self.inflight:
            raise OperationDeniedError('End the current demo before starting another')
        self.identifier, self.owner = identifier, owner
        self.seen.add(identifier)
        self.expires_at = self.clock() + timedelta(hours=4)
        self.state, self.cleanup_claimed, self.cleanup_result = 'active', False, None
        self.notify()
        return self.status()

    def end(self, identifier, owner):
        if self.identifier != identifier or self.owner != owner:
            raise OperationDeniedError('Exact owned demo session required')
        if self.state == 'active':
            self.state = 'ending'
            self.notify()
        return self.status()

    def require_active(self):
        if self.status()['state'] != 'active':
            raise OperationDeniedError('Demo is idle or ending. Start a demo before accessing SQL')

    def acquire(self):
        self.require_active()
        self.inflight += 1

    def release(self):
        self.inflight -= 1
        self.notify()

    def acquire_review(self, identifier, owner):
        if identifier in self.review_leases:
            raise OperationDeniedError('Review admission cannot be replayed')
        self.acquire()
        self.review_leases[identifier] = {'owner': owner, 'released': False}
        return {'lease_id': identifier}

    def release_review(self, identifier, owner):
        lease = self.review_leases.get(identifier)
        if lease is None or lease['owner'] != owner:
            raise OperationDeniedError('Owned Review admission required')
        if not lease['released']:
            lease['released'] = True
            self.release()
        return {'lease_id': identifier, 'released': True}

    @contextmanager
    def admitted(self):
        self.acquire()
        try:
            yield
        finally:
            self.release()

    def claim_final_cleanup(self):
        self.status()
        if self.state != 'ending' or self.inflight or self.cleanup_claimed:
            return False
        self.cleanup_claimed = True
        return True

    def finish_cleanup(self, confirmed):
        self.cleanup_result = 'confirmed' if confirmed else 'unconfirmed'
        self.state = 'idle'

    async def run_admitted(self, action, *args):
        try:
            await action(*args)
        finally:
            self.release()