"""Mercury transport with exact sizing and durable, byte-preserved transactions.

Credentials are resolved only at the transport boundary and never recorded.
An observation failure never re-dispatches a generation implicitly.
"""
from pathlib import Path
from urllib import request, error
from urllib.parse import urlsplit
from datetime import datetime, timezone
import hashlib
import json
import os
import time
import uuid
import copy

from .compiler import canonical, strict_json, settings, RequestTooLarge


def sha(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def publish_file(temp, path):
    """Bounded Windows sharing retry for local publication, never HTTP retry."""
    for attempt in range(5):
        try:
            os.replace(temp, path)
            return
        except PermissionError:
            if attempt == 4: raise
            time.sleep(0.05 * 2**attempt)


def atomic(path, value):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    temp = path.with_name(uuid.uuid4().hex + '.tmp')
    with temp.open('w', encoding='utf-8', newline='\n') as stream:
        stream.write(canonical(value) + '\n')
        stream.flush()
        os.fsync(stream.fileno())
    publish_file(temp, path)


def atomic_bytes(path, raw):
    """Durably preserve immutable wire bytes before publishing a receipt."""
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    temp = path.with_name(uuid.uuid4().hex + '.tmp')
    with temp.open('xb') as stream:
        stream.write(raw)
        stream.flush()
        os.fsync(stream.fileno())
    publish_file(temp, path)


def utc():
    return datetime.now(timezone.utc).isoformat()


class NoRedirect(request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


class NativeError(RuntimeError):
    def __init__(self, message, folder=None):
        super().__init__(message)
        self.folder = folder


class BudgetExceeded(NativeError):
    """Local admission stopped before dispatch; other case budgets stay usable."""


class IncompleteGeneration(NativeError):
    """A received terminal length response; safe for bounded item repair."""


class Native:
    def __init__(self, provider_path, output, event=None, endpoint=None):
        self.provider_path = Path(provider_path).resolve()
        self.cfg = strict_json(self.provider_path.read_text(encoding='utf-8'))
        base = self.cfg.get('base_url') or endpoint or os.environ.get(self.cfg.get('base_url_env', 'SCHEMA_BASE_URL'))
        if not base:
            raise ValueError('Explicit endpoint or existing endpoint binding required')
        self.base = base.rstrip('/')
        parsed = urlsplit(self.base)
        if parsed.scheme not in {'http', 'https'} or not parsed.hostname or parsed.username or parsed.password or parsed.query or parsed.fragment or not self.base.endswith('/v1'):
            raise ValueError('Credential-free /v1 endpoint required')
        if bool(self.cfg.get('api_key_file')) == bool(self.cfg.get('api_key_env')):
            raise ValueError('Exactly one local credential reference required')
        self.output = Path(output)
        self.output.mkdir(parents=True, exist_ok=True)
        self.event = event or (lambda value: None)
        self.case_id = None
        from .run_policy import active, Admission
        policy = active()
        self.admission = Admission(self.output.parent, policy) if policy else None
        self.opener = request.build_opener(request.ProxyHandler({}), NoRedirect())

    def _secret(self):
        if self.cfg.get('api_key_file'):
            return (self.provider_path.parent / self.cfg['api_key_file']).resolve().read_text(encoding='utf-8').strip()
        return os.environ[self.cfg['api_key_env']].strip()

    def transact(self, operation, endpoint, payload=None, timeout=20):
        folder = self.output / (str(time.time_ns()) + '-' + operation + '-' + uuid.uuid4().hex[:8])
        folder.mkdir()
        admitted = False
        if operation == 'generation' and self.admission:
            try:
                # Fresh idle observation before each admission, never stop shared service.
                metrics, _ = self.transact('admission-metrics', self.base[:-3] + '/metrics')
                import re
                for metric in ['num_requests_running', 'num_requests_waiting']:
                    values = re.findall(r'^vllm:' + metric + r'(?:\{[^\n]*\})?\s+([0-9.eE+-]+)', metrics.decode('utf-8'), re.M)
                    if not values or any(float(v) != 0 for v in values):
                        raise NativeError('Service is not proven idle; no generation dispatched')
                timeout = min(timeout, self.admission.admit(self.case_id, folder))
                admitted = True
            except Exception as exc:
                if payload is not None:
                    atomic_bytes(folder / 'request.json', canonical(payload).encode('utf-8'))
                rejection = {'operation': operation, 'status': 'rejected_before_generation', 'model_generation': False,
                             'attempted_generation': True, 'error_type': type(exc).__name__, 'error': str(exc),
                             'folder': str(folder), 'finished_utc': utc()}
                atomic(folder / 'receipt.json', rejection)
                self.event(rejection)
                raise
        raw = canonical(payload).encode('utf-8') if payload is not None else None
        if raw is not None:
            atomic_bytes(folder / 'request.json', raw)
        receipt = {'operation': operation, 'endpoint': endpoint, 'started_utc': utc(),
                   'status': 'dispatched', 'model_generation': operation == 'generation',
                   'request_sha256': hashlib.sha256(raw).hexdigest() if raw else None,
                   'request_bytes': len(raw) if raw else 0, 'folder': str(folder)}
        atomic(folder / 'receipt.json', receipt)
        self.event(receipt.copy())
        tick = time.monotonic()
        try:
            headers = {'Content-Type': 'application/json', 'Authorization': 'Bearer ' + self._secret()}
            req = request.Request(endpoint, data=raw, headers=headers, method='POST' if raw is not None else 'GET')
            with self.opener.open(req, timeout=timeout) as response:
                body = response.read()
                receipt['http_status'] = response.status
            atomic_bytes(folder / 'response.raw', body)
            receipt.update(status='received', response_sha256=hashlib.sha256(body).hexdigest(), response_bytes=len(body))
            return body, folder
        except error.HTTPError as exc:
            body = exc.read()
            atomic_bytes(folder / 'response.raw', body)
            receipt.update(status='failed', http_status=exc.code, response_sha256=hashlib.sha256(body).hexdigest(), error='HTTP ' + str(exc.code))
            raise NativeError(receipt['error'], folder) from None
        except (OSError, UnicodeError) as exc:
            receipt.update(status='observation_failed', error=type(exc).__name__, disposition='Inspect service and transaction; never assume remote generation stopped')
            raise NativeError('Transport observation failed: ' + type(exc).__name__, folder) from None
        finally:
            receipt.update(seconds=time.monotonic() - tick, finished_utc=utc())
            atomic(folder / 'receipt.json', receipt)
            self.event(receipt.copy())
            if admitted:
                self.admission.finish(folder, receipt.copy())

    def probe(self):
        body, folder = self.transact('models', self.base + '/models')
        obj = strict_json(body.decode('utf-8'))
        ids = [entry['id'] for entry in obj.get('data', [])]
        if self.cfg['model'] not in ids:
            raise NativeError('Configured model absent from live service', folder)
        info = {'model': self.cfg['model'], 'models': ids, 'models_receipt': str(folder), 'probed_utc': utc()}
        for suffix in ['health', 'metrics']:
            body, observed = self.transact(suffix, self.base[:-3] + '/' + suffix)
            info[suffix + '_receipt'] = str(observed)
        return info

    def wire(self, prepared):
        source = prepared['wire'] if 'wire' in prepared else prepared
        messages = copy.deepcopy(source['messages'])
        contract = copy.deepcopy(source.get('response_contract'))
        if contract:
            # The decoder contract must also be visible to the model. A hidden
            # required key can otherwise trap decoding in whitespace before it.
            messages[0]['content'] += '\nReturn compact JSON without indentation. Include every required response key. Keep each navigation reason to one brief sentence.'
            try:
                payload = strict_json(messages[1]['content'])
            except (ValueError, TypeError):
                payload = {'original_user_text': messages[1]['content']}
            payload['native_response_contract'] = contract
            messages[1]['content'] = canonical(payload)
        value = {'model': self.cfg['model'], 'messages': messages,
                 'max_tokens': source['max_tokens'], 'temperature': 0,
                 'chat_template_kwargs': {'enable_thinking': False}}
        if contract:
            value['structured_outputs'] = {'json': contract,
                                           'disable_any_whitespace': True,
                                           'disable_additional_properties': True}
        else:
            value['response_format'] = {'type': 'json_object'}
        return value

    def complete(self, prepared):
        cfg = settings()
        if not cfg.get('native_generation_enabled'):
            raise NativeError('Native generation disabled')
        wire = self.wire(prepared)
        size = len(canonical(wire).encode('utf-8'))
        if size > cfg['max_wire_bytes']:
            raise RequestTooLarge('Translated native wire exceeds byte limit')
        tokenizer = {'model': wire['model'], 'messages': wire['messages'],
                     'add_generation_prompt': True, 'add_special_tokens': False,
                     'return_token_strs': False, 'chat_template_kwargs': wire['chat_template_kwargs']}
        body, token_folder = self.transact('tokenization', self.base[:-3] + '/tokenize', tokenizer)
        count = strict_json(body.decode('utf-8')).get('count')
        if type(count) is not int or count < 0:
            raise NativeError('Tokenizer count invalid', token_folder)
        if count + wire['max_tokens'] + cfg['safety_reserve_tokens'] > cfg['context_tokens']:
            raise RequestTooLarge('Exact tokenizer plus output/safety reserve exceeds context')
        atomic(token_folder / 'sizing.json', {'input_tokens': count, 'output_reserve': wire['max_tokens'],
                                              'safety_reserve': cfg['safety_reserve_tokens'],
                                              'context_tokens': cfg['context_tokens'], 'wire_bytes': size})
        body, folder = self.transact('generation', self.base + '/chat/completions', wire,
                                    timeout=cfg['request_timeout_seconds'])
        envelope = strict_json(body.decode('utf-8'))
        if envelope.get('model') != wire['model'] or len(envelope.get('choices', [])) != 1:
            raise NativeError('Native model/envelope identity differs', folder)
        choice = envelope['choices'][0]
        content = choice['message']['content']
        atomic_bytes(folder / 'content.raw.txt', content.encode('utf-8'))
        atomic(folder / 'generation.json', {'finish_reason': choice['finish_reason'], 'usage': envelope.get('usage'),
                                           'response_id': envelope.get('id'), 'tokenization_folder': str(token_folder),
                                           'prompt_tokens': count})
        if choice['finish_reason'] == 'length':
            raise IncompleteGeneration('Generation incomplete: length', folder)
        if choice['finish_reason'] != 'stop':
            raise NativeError('Generation incomplete: ' + str(choice['finish_reason']), folder)
        result = strict_json(content)
        if source_contract := (prepared.get('wire', prepared).get('response_contract')):
            import jsonschema
            jsonschema.validate(result, source_contract)
        atomic(folder / 'parsed.json', result)
        return result, {'folder': str(folder), 'tokenization_folder': str(token_folder),
                        'usage': envelope.get('usage'), 'prompt_tokens': count}
