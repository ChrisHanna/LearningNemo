"""Closed private invoice API proxy; no browser-selected origins or SQL."""

import asyncio
import re

import httpx
from pydantic import BaseModel, ConfigDict, Field
from typing import Literal


class InvoiceJobRequest(BaseModel):
    model_config = ConfigDict(extra='forbid')
    job_id: str = Field(pattern=r'^[a-f0-9]{32}$')
    kind: Literal['planning', 'execution']
    target_id: str = Field(pattern=r'^[a-f0-9]{32}$')
    plan_hash: str | None = Field(default=None, pattern=r'^[a-f0-9]{64}$')


class InvoicePlanRequest(BaseModel):
    model_config = ConfigDict(extra='forbid')
    plan_hash: str = Field(pattern=r'^[a-f0-9]{64}$')


class InvoiceDecisionRequest(InvoicePlanRequest):
    decision: Literal['approve', 'reject']


class InvoiceScenarioRequest(BaseModel):
    model_config = ConfigDict(extra='forbid')
    scenario_id: str = Field(pattern=r'^[a-f0-9]{32}$')
    variant: Literal['lost-acknowledgement','healthy']


class InvoiceChallengeRequest(BaseModel):
    model_config = ConfigDict(extra='forbid')
    challenge_id: str = Field(pattern=r'^[a-f0-9]{32}$')
    kind: Literal['planning','execution','query-draft','allowed-file','denied-file','sql-denied','write-app-denied','approved-api','external-api-denied','symlink-escape-denied']


class InvoiceSandboxTestRequest(BaseModel):
    model_config = ConfigDict(extra='forbid')
    sandbox_id: str = Field(pattern=r'^[a-f0-9]{32}$')


class InvoiceDemoSessionRequest(BaseModel):
    model_config = ConfigDict(extra='forbid')
    session_id: str = Field(pattern=r'^[a-f0-9]{32}$')


class InvoiceRemoteError(RuntimeError):
    def __init__(self, status_code, message):
        self.status_code = status_code
        super().__init__(message)


class RemoteInvoiceService:
    investigation_mode = 'openshell'

    def __init__(self, operator_origin, review_origin, *, broker_credential=None, broker_audience=None):
        domain = '.internal.jollybeach-503c7ed1.eastus.azurecontainerapps.io'
        if operator_origin != 'https://ca-nemo-invoice-operator-dev' + domain or review_origin != 'https://ca-nemo-invoice-review-dev' + domain:
            raise ValueError('fixed private invoice origins required')
        self.operator_origin, self.review_origin = operator_origin, review_origin
        self.broker_credential, self.broker_audience = broker_credential, broker_audience

    async def request(self, method, path, token, body=None, *, review=False, after=0, guest_subject=None, guest_persona=None):
        allowed = (method == 'GET' and (path in ('/invoices/plans','/invoices/sandboxes','/invoices/demo-session') or re.fullmatch(r'/invoices/scenarios/[a-f0-9]{32}', path) or re.fullmatch(r'/invoices/jobs/[a-f0-9]{32}(/events)?', path) or re.fullmatch(r'/invoices/challenges/[a-f0-9]{32}|/invoices/plans/[a-f0-9]{32}/evidence', path))) or (
            method == 'POST' and (path in ('/invoices/jobs','/invoices/scenarios','/invoices/challenges','/invoices/demo-session/start','/invoices/demo-session/end') or re.fullmatch(r'/invoices/sandboxes/[a-f0-9]{32}/delete', path) or re.fullmatch(r'/invoices/jobs/[a-f0-9]{32}/sandbox-test', path) or re.fullmatch(r'/invoices/plans/[a-f0-9]{32}/(submit|complete|decision|reconcile)', path)))
        if not allowed or type(after) is not int or after < 0:
            raise ValueError('invoice proxy route denied')
        headers = {'Authorization': 'Bearer ' + token}
        if guest_subject is not None or guest_persona is not None:
            if (
                self.broker_credential is None
                or not self.broker_audience
                or not re.fullmatch(r'[a-f0-9]{64}', guest_subject or '')
                or guest_persona not in ('operator', 'approver')
            ):
                raise InvoiceRemoteError(403, 'Public demo identity is unavailable')
            broker = await asyncio.to_thread(self.broker_credential.get_token, self.broker_audience + '/.default')
            headers = {
                'Authorization': 'Bearer ' + broker.token,
                'X-LearningNeMo-Guest-Subject': guest_subject,
                'X-LearningNeMo-Guest-Persona': guest_persona,
            }
        try:
            async with httpx.AsyncClient(timeout=150 if path.startswith('/invoices/sandboxes') or path == '/invoices/scenarios' or method == 'GET' and (path == '/invoices/plans' or path.startswith('/invoices/scenarios/')) else 75 if path.endswith(('/reconcile','/evidence')) else 30, follow_redirects=False) as client:
                response = await client.request(method, (self.review_origin if review else self.operator_origin) + path,
                    headers=headers, json=body, params={'after': after} if path.endswith('/events') else None)
            if response.status_code not in (200, 201, 202):
                if response.status_code == 503 and len(response.content) <= 4096:
                    try:
                        detail = response.json()
                    except ValueError:
                        detail = None
                    if detail == {'detail': 'Invoice service lease expired'}:
                        raise InvoiceRemoteError(503, 'Invoice service lease expired. This request was rejected before execution. The demo operator must renew the service window.')
                messages = {401: 'Session expired; sign in again', 403: 'Invoice operation denied for this identity',
                            409: 'Plan or run state changed. Refresh persisted status before continuing'}
                raise InvoiceRemoteError(response.status_code if response.status_code in messages else 503,
                                         messages.get(response.status_code, 'Invoice service unavailable; no successful outcome assumed'))
            if len(response.content) > 262144:
                raise ValueError('invoice response exceeds budget')
            result = response.json()
            if not isinstance(result, dict):
                raise ValueError('invalid invoice response')
            return result
        except (httpx.HTTPError, ValueError):
            raise InvoiceRemoteError(503, 'Invoice response unconfirmed; keep the request ID and inspect status') from None