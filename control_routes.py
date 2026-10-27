"""
Control centre routes: command queue (web -> MT5 VPS), logs view requests,
files produced by commands, and the AI-review / taught-answers store.

Registered from app.py via init_control(app, is_admin, push_secret). The page
and its JSON APIs need the admin login; the /control/agent/* endpoints are
used only by the VPS and authenticate with the X-Control-Secret header (same
secret as /control/ingest). Nothing here opens any port on the VPS: the VPS
polls this app and posts results back.
"""
import base64
import hmac
import json
import os
import re
import time
from datetime import datetime, timedelta

from flask import jsonify, request, redirect, url_for, Response
from flask_login import login_required, current_user

from models import db, ControlCommand, ControlFile, AIReview, AITaughtRule

MAX_FILE_BYTES = 12 * 1024 * 1024
MAX_FILES_PER_COMMAND = 12
PENDING_EXPIRY_MIN = 10
RUNNING_EXPIRY_MIN = 20
SAFE_MIMES = {'image/png', 'image/jpeg', 'image/gif', 'image/webp', 'text/plain', 'text/csv'}
VALID_VERDICTS = ('full_close', 'breakeven', 'unclear', 'statement')

# Services offered on the Logs tab. 'key' is what the VPS maps to a folder/file.
LOG_SERVICES = [
    ('monitor',  'MT5 monitor'),
    ('tray',     'MT5 tray'),
    ('tof',      'Tipster / TOF'),
    ('phyllis',  'Phyllis Court booking bot'),
    ('deploy',   'Auto-deploy'),
    ('poller',   'Run Now poller'),
]
LOG_SERVICE_KEYS = {k for k, _ in LOG_SERVICES}


def _now():
    return datetime.now().strftime('%Y-%m-%d %H:%M:%S')


def norm_text(t):
    """Normalisation used for exact-match taught rules (must match the VPS)."""
    t = (t or '').replace('’', "'").replace('‘', "'").replace(' ', ' ')
    return ' '.join(t.lower().split())


PAGE_VERSION = 'v2026.10.27-1' + (' · ' + os.environ['RAILWAY_GIT_COMMIT_SHA'][:7] if os.environ.get('RAILWAY_GIT_COMMIT_SHA') else '')


def parse_web_command(text):
    """Whitelist + validate a typed/clicked command. Returns (command, risk) or raises ValueError."""
    t = (text or '').strip()
    if not t.startswith('/'):
        t = '/' + t
    low = t.lower()
    if re.fullmatch(r'/report(\s+\d{4}-\d{2}-\d{2})?', low):
        return ' '.join(low.split()), 'read'
    if re.fullmatch(r'/status(\s+week)?(\s+\d{4}-\d{2}-\d{2})?', low):
        return ' '.join(low.split()), 'read'
    m = re.fullmatch(r'/prices(\s+([a-z0-9._]{3,12}))?', low)
    if m:
        return '/prices' + (' ' + m.group(2).upper() if m.group(2) else ''), 'read'
    if low == '/restart':
        return '/restart', 'control'
    if low == '/close':
        return '/close', 'trade'
    m = re.fullmatch(r'/fill[\s_]*t?([1-5])\s*(\d+(?:\.\d+)?)?', low)
    if m:
        return '/fill T%s%s' % (m.group(1), (' ' + m.group(2)) if m.group(2) else ''), 'trade'
    raise ValueError('Command not allowed here. Available: /report [YYYY-MM-DD], /status [week] [YYYY-MM-DD], /prices [SYMBOL], '
                     '/restart, /close, /fill T1-T5 [entry price]')


def _cmd_json(c, with_files=True):
    d = {
        'id': c.id, 'kind': c.kind, 'command': c.command, 'risk': c.risk, 'status': c.status,
        'created_at': c.created_at, 'started_at': c.started_at, 'finished_at': c.finished_at,
        'result_text': c.result_text or '',
        'params': json.loads(c.params) if c.params else {},
    }
    if with_files:
        files = ControlFile.query.filter_by(command_id=c.id).order_by(ControlFile.id).all()
        d['files'] = [{'id': f.id, 'name': f.name, 'mime': f.mime, 'size': f.size} for f in files]
    return d


def _expire_stale():
    now = datetime.now()
    changed = False
    for c in ControlCommand.query.filter(ControlCommand.status.in_(('pending', 'running'))).all():
        try:
            created = datetime.strptime(c.created_at, '%Y-%m-%d %H:%M:%S')
        except Exception:
            continue
        age = now - created
        if c.status == 'pending' and age > timedelta(minutes=PENDING_EXPIRY_MIN):
            c.status, c.finished_at = 'expired', _now()
            c.result_text = 'Not picked up by the VPS within %d minutes (is the monitor running?).' % PENDING_EXPIRY_MIN
            changed = True
        elif c.status == 'running' and age > timedelta(minutes=RUNNING_EXPIRY_MIN):
            c.status, c.finished_at = 'failed', _now()
            c.result_text = (c.result_text or '') + '\nNo result received from the VPS.'
            changed = True
    if changed:
        db.session.commit()


def init_control(app, is_admin, push_secret):

    def admin_json():
        """Returns an error response if the caller is not the admin, else None."""
        if not current_user.is_authenticated or not is_admin():
            return jsonify({'error': 'Forbidden'}), 403
        if request.method != 'GET' and request.headers.get('X-Requested-With') != 'control-page':
            return jsonify({'error': 'Bad request'}), 400  # CSRF guard for state-changing calls
        return None

    def agent_ok():
        secret = request.headers.get('X-Control-Secret', '')
        return bool(push_secret) and hmac.compare_digest(secret, push_secret)

    # ── queue a command ────────────────────────────────────────────────────────
    @app.route('/control/api/command', methods=['POST'])
    @login_required
    def control_api_command():
        err = admin_json()
        if err: return err
        body = request.get_json(silent=True) or {}
        try:
            command, risk = parse_web_command(body.get('command'))
        except ValueError as e:
            return jsonify({'error': str(e)}), 400
        if risk in ('control', 'trade'):
            if not body.get('confirm'):
                return jsonify({'error': 'Please tick the confirmation box.'}), 400
            if not current_user.check_password(body.get('password') or ''):
                time.sleep(1.5)
                return jsonify({'error': 'Password incorrect.'}), 403
        _expire_stale()
        busy = ControlCommand.query.filter(ControlCommand.kind == 'command',
                                           ControlCommand.command == command,
                                           ControlCommand.status.in_(('pending', 'running'))).first()
        if busy and risk != 'read':
            return jsonify({'error': 'That command is already waiting or running (#%d).' % busy.id}), 409
        c = ControlCommand(kind='command', command=command, risk=risk, status='pending', created_at=_now())
        db.session.add(c)
        db.session.commit()
        return jsonify({'id': c.id, 'command': command, 'risk': risk})

    @app.route('/control/api/logs', methods=['POST'])
    @login_required
    def control_api_logs():
        err = admin_json()
        if err: return err
        body = request.get_json(silent=True) or {}
        service = (body.get('service') or '').strip()
        date = (body.get('date') or '').strip()
        q = (body.get('q') or '').strip()[:100]
        if service not in LOG_SERVICE_KEYS:
            return jsonify({'error': 'Unknown service'}), 400
        if not re.fullmatch(r'\d{4}-\d{2}-\d{2}', date):
            return jsonify({'error': 'Pick a date'}), 400
        _expire_stale()
        c = ControlCommand(kind='logs', command='logs %s %s' % (service, date), risk='read', status='pending',
                           created_at=_now(), params=json.dumps({'service': service, 'date': date, 'q': q}))
        db.session.add(c)
        db.session.commit()
        return jsonify({'id': c.id})

    @app.route('/control/api/command/<int:cid>')
    @login_required
    def control_api_command_get(cid):
        err = admin_json()
        if err: return err
        _expire_stale()
        c = ControlCommand.query.get(cid)
        if not c:
            return jsonify({'error': 'not found'}), 404
        return jsonify(_cmd_json(c))

    @app.route('/control/api/commands')
    @login_required
    def control_api_commands():
        err = admin_json()
        if err: return err
        _expire_stale()
        kind = request.args.get('kind', '')
        q = ControlCommand.query
        if kind in ('command', 'logs'):
            q = q.filter(ControlCommand.kind == kind)
        rows = q.order_by(ControlCommand.id.desc()).limit(30).all()
        return jsonify({'commands': [_cmd_json(c, with_files=False) for c in rows],
                        'services': [{'key': k, 'label': l} for k, l in LOG_SERVICES]})

    @app.route('/control/file/<int:fid>')
    @login_required
    def control_file(fid):
        if not is_admin():
            return redirect(url_for('index'))
        f = ControlFile.query.get(fid)
        if not f:
            return 'Not found', 404
        mime = f.mime if f.mime in SAFE_MIMES else 'application/octet-stream'
        disp = 'inline' if mime.startswith('image/') and request.args.get('dl') != '1' else 'attachment'
        safe = re.sub(r'[^A-Za-z0-9._-]', '_', f.name or 'file')
        return Response(f.data, mimetype=mime, headers={
            'Content-Disposition': '%s; filename="%s"' % (disp, safe),
            'X-Content-Type-Options': 'nosniff', 'Cache-Control': 'private, max-age=3600'})

    # ── VPS agent endpoints ────────────────────────────────────────────────────
    @app.route('/control/agent/poll', methods=['POST'])
    def control_agent_poll():
        if not agent_ok():
            return jsonify({'error': 'Forbidden'}), 403
        _expire_stale()
        rows = (ControlCommand.query.filter_by(status='pending')
                .order_by(ControlCommand.id).limit(5).all())
        now = _now()
        out = []
        for c in rows:
            c.status, c.started_at = 'running', now
            out.append({'id': c.id, 'kind': c.kind, 'command': c.command,
                        'params': json.loads(c.params) if c.params else {}})
        db.session.commit()
        recent = ControlCommand.query.filter(ControlCommand.created_at >= (datetime.now() - timedelta(minutes=3)).strftime('%Y-%m-%d %H:%M:%S')).count()
        rules = AITaughtRule.query.filter_by(enabled=True).order_by(AITaughtRule.id).limit(300).all()
        # AI flags already answered on the page, so the VPS can mark them resolved
        # there too (idempotent on the VPS) and stop listing them in /unresolved
        # and the daily digest.
        answered = [r.natural_key for r in AIReview.query.filter(
            AIReview.resolved.is_(True), AIReview.natural_key.like('ai:%'),
            ~AIReview.natural_key.like('ai:s:%')).order_by(AIReview.id.desc()).limit(200).all()]
        resolved_ids = []
        for k in answered:
            try: resolved_ids.append(int(k.split(':', 1)[1]))
            except ValueError: pass
        return jsonify({
            'commands': out,
            'ai_resolved_ids': resolved_ids,
            'next_poll_seconds': 3 if (recent or out) else 15,
            'rules': [{'id': r.id, 'trader': r.trader, 'text_raw': r.text_raw, 'text_norm': r.text_norm,
                       'verdict': r.verdict, 'note': r.note or ''} for r in rules],
        })

    @app.route('/control/agent/result', methods=['POST'])
    def control_agent_result():
        if not agent_ok():
            return jsonify({'error': 'Forbidden'}), 403
        body = request.get_json(silent=True) or {}
        c = ControlCommand.query.get(body.get('id'))
        if not c:
            return jsonify({'error': 'unknown command'}), 404
        status = body.get('status') if body.get('status') in ('done', 'failed') else 'done'
        c.status, c.finished_at = status, _now()
        c.result_text = (body.get('text') or '')[:400000]
        saved, skipped = 0, 0
        for f in (body.get('files') or [])[:MAX_FILES_PER_COMMAND]:
            try:
                raw = base64.b64decode(f.get('b64') or '', validate=True)
            except Exception:
                skipped += 1
                continue
            if not raw or len(raw) > MAX_FILE_BYTES:
                skipped += 1
                continue
            db.session.add(ControlFile(command_id=c.id, name=(f.get('name') or 'file')[:200],
                                       mime=(f.get('mime') or 'application/octet-stream')[:80],
                                       size=len(raw), data=raw, created_at=_now()))
            saved += 1
        if skipped:
            c.result_text = (c.result_text or '') + '\n(%d file(s) skipped: empty, invalid or over 12 MB)' % skipped
        db.session.commit()
        return jsonify({'status': 'ok', 'files_saved': saved})

    @app.route('/control/agent/ai', methods=['POST'])
    def control_agent_ai():
        if not agent_ok():
            return jsonify({'error': 'Forbidden'}), 403
        body = request.get_json(silent=True) or {}
        reviews = body.get('reviews') or []
        if len(reviews) > 500:
            return jsonify({'error': 'max 500 per request'}), 400
        created = updated = 0
        for r in reviews:
            key = (r.get('natural_key') or '').strip()
            if not key:
                continue
            row = AIReview.query.filter_by(natural_key=key).first()
            is_new = row is None
            if row is None:
                row = AIReview(natural_key=key)
                db.session.add(row)
                created += 1
            else:
                updated += 1
            row.flagged_at = (r.get('flagged_at') or row.flagged_at or _now())[:30]
            row.trader = (r.get('trader') or row.trader or '')[:30]
            row.symbol = (r.get('symbol') or row.symbol or '')[:20]
            row.raw_text = r.get('raw_text') if r.get('raw_text') is not None else row.raw_text
            row.verdict = (r.get('verdict') or row.verdict or '')[:12]
            row.confidence = (r.get('confidence') or row.confidence or '')[:8]
            row.reasoning = r.get('reasoning') if r.get('reasoning') is not None else row.reasoning
            row.auto_executed = bool(r.get('auto_executed', row.auto_executed))
            row.action_result = r.get('action_result') if r.get('action_result') is not None else row.action_result
            row.source = (r.get('source') or row.source or 'ai')[:12]
            if r.get('rule_id'):
                row.rule_id = r['rule_id']
                rule = AITaughtRule.query.get(r['rule_id'])
                if rule and is_new:  # first sight of a message answered by this rule
                    rule.use_count = (rule.use_count or 0) + 1
                    rule.last_used_at = _now()
            if r.get('resolved'):
                row.resolved = True
        db.session.commit()
        return jsonify({'status': 'ok', 'created': created, 'updated': updated})

    # ── AI review page APIs ────────────────────────────────────────────────────
    def _review_json(r):
        return {'id': r.id, 'flagged_at': r.flagged_at, 'trader': r.trader, 'symbol': r.symbol,
                'raw_text': r.raw_text, 'verdict': r.verdict, 'confidence': r.confidence,
                'reasoning': r.reasoning, 'auto_executed': bool(r.auto_executed),
                'action_result': r.action_result, 'source': r.source, 'rule_id': r.rule_id,
                'resolved': bool(r.resolved), 'taught_verdict': r.taught_verdict,
                'taught_note': r.taught_note, 'taught_at': r.taught_at}

    @app.route('/control/api/ai')
    @login_required
    def control_api_ai():
        err = admin_json()
        if err: return err
        state = request.args.get('state', 'open')
        q = request.args.get('q', '').strip()
        trader = request.args.get('trader', '').strip()
        offset = max(request.args.get('offset', 0, type=int), 0)
        query = AIReview.query
        if state == 'open':
            query = query.filter(AIReview.taught_verdict.is_(None), AIReview.resolved.is_(False),
                                 AIReview.verdict != 'statement')
        elif state == 'taught':
            query = query.filter(AIReview.taught_verdict.isnot(None))
        if trader:
            query = query.filter(AIReview.trader == trader)
        for word in q.split():
            like = '%' + word.replace('\\', '\\\\').replace('%', r'\%').replace('_', r'\_') + '%'
            query = query.filter(AIReview.raw_text.ilike(like, escape='\\'))
        total = query.count()
        rows = query.order_by(AIReview.flagged_at.desc(), AIReview.id.desc()).offset(offset).limit(51).all()
        traders = [t[0] for t in db.session.query(AIReview.trader).distinct().order_by(AIReview.trader).all() if t[0]]
        return jsonify({'total': total, 'has_more': len(rows) > 50, 'traders': traders,
                        'reviews': [_review_json(r) for r in rows[:50]]})

    @app.route('/control/api/ai/teach', methods=['POST'])
    @login_required
    def control_api_ai_teach():
        err = admin_json()
        if err: return err
        body = request.get_json(silent=True) or {}
        r = AIReview.query.get(body.get('review_id'))
        verdict = body.get('verdict')
        if not r or verdict not in VALID_VERDICTS:
            return jsonify({'error': 'bad request'}), 400
        if verdict in ('full_close', 'breakeven') and not current_user.check_password(body.get('password') or ''):
            time.sleep(1.5)
            return jsonify({'error': 'Password incorrect (needed because a close/breakeven answer can act on live trades).'}), 403
        note = (body.get('note') or '').strip()[:500]
        text_norm = norm_text(r.raw_text)
        rule = AITaughtRule.query.filter_by(trader=r.trader, text_norm=text_norm).first()
        if rule is None:
            rule = AITaughtRule(trader=r.trader, text_raw=r.raw_text, text_norm=text_norm, created_at=_now(), use_count=0)
            db.session.add(rule)
        rule.verdict, rule.note, rule.enabled = verdict, note, True
        db.session.flush()
        r.taught_verdict, r.taught_note, r.taught_at, r.resolved = verdict, note, _now(), True
        db.session.commit()
        return jsonify({'status': 'ok', 'rule_id': rule.id})

    @app.route('/control/api/ai/rules')
    @login_required
    def control_api_ai_rules():
        err = admin_json()
        if err: return err
        rows = AITaughtRule.query.order_by(AITaughtRule.id.desc()).limit(300).all()
        return jsonify({'rules': [{'id': x.id, 'trader': x.trader, 'text_raw': x.text_raw, 'verdict': x.verdict,
                                   'note': x.note, 'enabled': bool(x.enabled), 'created_at': x.created_at,
                                   'use_count': x.use_count or 0, 'last_used_at': x.last_used_at} for x in rows]})

    @app.route('/control/api/ai/rules/<int:rid>', methods=['POST', 'DELETE'])
    @login_required
    def control_api_ai_rule_change(rid):
        err = admin_json()
        if err: return err
        rule = AITaughtRule.query.get(rid)
        if not rule:
            return jsonify({'error': 'not found'}), 404
        if request.method == 'DELETE':
            db.session.delete(rule)
        else:
            enabled = (request.get_json(silent=True) or {}).get('enabled')
            if enabled is True and rule.verdict in ('full_close', 'breakeven') and not rule.enabled:
                pw = (request.get_json(silent=True) or {}).get('password') or ''
                if not current_user.check_password(pw):
                    time.sleep(1.5)
                    return jsonify({'error': 'Password incorrect.'}), 403
            rule.enabled = bool(enabled)
        db.session.commit()
        return jsonify({'status': 'ok'})
