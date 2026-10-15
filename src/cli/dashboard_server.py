"""Minimal local Web UI — stdlib only (http.server).

  python dispatcher.py --dashboard
  → http://127.0.0.1:8337

Queues + recipe + policy + doctor snapshot. No cloud, no telemetry.
"""
from __future__ import annotations
import json
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any
from urllib.parse import parse_qs, urlparse

def _root() -> Path:
    '''"""Internal: _root()."""'''
    try:
        from core.config import BASE_DIR
        return Path(BASE_DIR)
    except Exception:
        return Path(__file__).resolve().parents[2]

def _count_channel(channel: str='gpt') -> dict[str, int]:
    '''"""Internal: _count_channel(channel)."""'''
    root = _root()
    base = root / 'channels' / channel
    out: dict[str, int] = {}
    for name in ('incoming', 'processing', 'done', 'errors', 'deferred', 'quarantine'):
        d = base / name
        out[name] = len(list(d.glob('*.json'))) if d.is_dir() else 0
    return out

def _snapshot() -> dict[str, Any]:
    '''"""Internal: _snapshot()."""'''
    data: dict[str, Any] = {'queues': _count_channel(), 'recipes': [], 'policy': {}, 'runtimes': []}
    try:
        from core.local_queue import get_local_queue
        data['desktop_queue'] = get_local_queue(_root()).size()
        data['queues'] = dict(data['queues'])
        data['queues']['desktop'] = data['desktop_queue']
    except Exception as exc:
        data['desktop_queue'] = 0
        data['desktop_error'] = str(exc)
    try:
        from cli.recipes import list_recipes
        data['recipes'] = [{'id': (r.get('metadata') or {}).get('recipe') or r.get('id'), 'message': (r.get('message') or '')[:120]} for r in list_recipes()]
    except Exception as exc:
        data['recipes_error'] = str(exc)
    try:
        from core.policy import load_policy
        p = load_policy()
        data['policy'] = {'name': p.name, 'allow_cloud': p.allow_cloud, 'prefer_local': p.prefer_local, 'privacy': p.privacy}
    except Exception as exc:
        data['policy_error'] = str(exc)
    try:
        from cli.init_wizard import discover_local_stack
        disc = discover_local_stack()
        data['runtimes'] = [{'id': r.get('id'), 'ok': r.get('ok'), 'models': (r.get('models') or [])[:5]} for r in disc.get('runtimes') or []]
        data['tools'] = {k: disc.get(k) for k in ('git', 'pytest', 'aider')}
    except Exception as exc:
        data['runtime_error'] = str(exc)
    return data
_HTML = '<!DOCTYPE html>\n<html lang="ru">\n<head>\n<meta charset="utf-8"/>\n<meta name="viewport" content="width=device-width, initial-scale=1"/>\n<title>NVCode</title>\n<style>\n  :root { --bg:#1e1e1e; --fg:#ddd; --dim:#888; --acc:#0078d4; --card:#2a2a2a; }\n  * { box-sizing: border-box; }\n  body { font-family: system-ui, sans-serif; background: var(--bg); color: var(--fg);\n         margin: 0; padding: 16px; max-width: 900px; margin-inline: auto; }\n  h1 { font-size: 1.25rem; margin: 0 0 12px; }\n  .row { display: flex; flex-wrap: wrap; gap: 8px; margin-bottom: 16px; }\n  .card { background: var(--card); border-radius: 8px; padding: 12px 14px; min-width: 120px; }\n  .card b { display: block; font-size: 1.4rem; color: var(--acc); }\n  .dim { color: var(--dim); font-size: 0.85rem; }\n  label { display: block; margin: 8px 0 4px; color: var(--dim); font-size: 0.85rem; }\n  input, select, button, textarea {\n    background: #333; color: var(--fg); border: 1px solid #444; border-radius: 6px;\n    padding: 8px 10px; width: 100%;\n  }\n  button { background: var(--acc); border: none; cursor: pointer; font-weight: 600; width: auto; }\n  button:hover { filter: brightness(1.1); }\n  textarea { min-height: 80px; font-family: inherit; }\n  #log { white-space: pre-wrap; font-size: 0.8rem; color: var(--dim); margin-top: 12px; }\n  a { color: var(--acc); }\n</style>\n</head>\n<body>\n  <h1>NVCode · локальная панель</h1>\n  <p class="dim">Только localhost · без телеметрии · <span id="policy"></span></p>\n\n  <p class="dim" id="deskline"></p>\n  <div class="row" id="queues"></div>\n\n  <div class="card" style="width:100%">\n    <div class="dim">Рuntimes</div>\n    <div id="runtimes" class="dim"></div>\n  </div>\n\n  <h2 style="font-size:1rem;margin-top:20px">Новая задача</h2>\n  <label>Сообщение</label>\n  <textarea id="msg" placeholder="Что сделать с кодом…"></textarea>\n  <label>Рецепт (опционально)</label>\n  <select id="recipe"><option value="">—</option></select>\n  <label>Файл / target</label>\n  <input id="target" placeholder="src/module.py"/>\n  <label>Проект (имя из PROJECT_*)</label>\n  <input id="project" placeholder=""/>\n  <div class="row" style="margin-top:12px">\n    <button type="button" onclick="submitTask()">Отправить</button>\n    <button type="button" onclick="refresh()" style="background:#444">Обновить</button>\n  </div>\n  <div id="log"></div>\n\n<script>\nasync function refresh() {\n  const r = await fetch(\'/api/status\');\n  const d = await r.json();\n  const q = d.queues || {};\n  document.getElementById(\'deskline\').textContent =\n    \'Очередь чата ПК (desktop): \' + (d.desktop_queue ?? q.desktop ?? 0);\n  const el = document.getElementById(\'queues\');\n  el.innerHTML = \'\';\n  for (const [k,v] of Object.entries(q)) {\n    if (k === \'desktop\') continue;\n    el.innerHTML += `<div class="card"><b>${v}</b><span class="dim">${k}</span></div>`;\n  }\n  const pol = d.policy || {};\n  document.getElementById(\'policy\').textContent =\n    pol.name ? `policy: ${pol.name} · cloud=${pol.allow_cloud}` : \'\';\n  document.getElementById(\'runtimes\').textContent =\n    (d.runtimes||[]).map(x => `${x.ok?\'✓\':\'✗\'} ${x.id}`).join(\' · \');\n  const sel = document.getElementById(\'recipe\');\n  const cur = sel.value;\n  sel.innerHTML = \'<option value="">—</option>\';\n  for (const rec of (d.recipes||[])) {\n    const o = document.createElement(\'option\');\n    o.value = rec.id || \'\';\n    o.textContent = rec.id || \'?\';\n    sel.appendChild(o);\n  }\n  sel.value = cur;\n}\nasync function submitTask() {\n  const body = {\n    message: document.getElementById(\'msg\').value,\n    recipe: document.getElementById(\'recipe\').value,\n    target: document.getElementById(\'target\').value,\n    project: document.getElementById(\'project\').value,\n  };\n  const r = await fetch(\'/api/task\', {\n    method: \'POST\', headers: {\'Content-Type\': \'application/json\'},\n    body: JSON.stringify(body),\n  });\n  const d = await r.json();\n  document.getElementById(\'log\').textContent = JSON.stringify(d, null, 2);\n  refresh();\n}\nrefresh();\nsetInterval(refresh, 5000);\n</script>\n</body>\n</html>\n'

class _Handler(BaseHTTPRequestHandler):
    '''"""_Handler."""'''

    def log_message(self, fmt: str, *args: Any) -> None:
        '''"""log_message(fmt).

Returns:
    Result of log_message.
"""'''
        pass

    def _json(self, code: int, obj: Any) -> None:
        '''"""Internal: _json(code, obj)."""'''
        raw = json.dumps(obj, ensure_ascii=False).encode('utf-8')
        self.send_response(code)
        self.send_header('Content-Type', 'application/json; charset=utf-8')
        self.send_header('Content-Length', str(len(raw)))
        self.end_headers()
        self.wfile.write(raw)

    def _html(self, code: int, text: str) -> None:
        '''"""Internal: _html(code, text)."""'''
        raw = text.encode('utf-8')
        self.send_response(code)
        self.send_header('Content-Type', 'text/html; charset=utf-8')
        self.send_header('Content-Length', str(len(raw)))
        self.end_headers()
        self.wfile.write(raw)

    def do_GET(self) -> None:
        '''"""do_GET()."""'''
        path = urlparse(self.path).path
        if path in ('/', '/index.html'):
            self._html(200, _HTML)
            return
        if path == '/api/status':
            self._json(200, _snapshot())
            return
        self._json(404, {'error': 'not found'})

    def do_POST(self) -> None:
        '''"""do_POST()."""'''
        path = urlparse(self.path).path
        length = int(self.headers.get('Content-Length') or 0)
        body = self.rfile.read(length) if length else b'{}'
        try:
            data = json.loads(body.decode('utf-8') or '{}')
        except Exception:
            data = {}
        if path != '/api/task':
            self._json(404, {'error': 'not found'})
            return
        recipe = str(data.get('recipe') or '').strip()
        message = str(data.get('message') or '').strip()
        target = str(data.get('target') or '').strip() or None
        project = str(data.get('project') or '').strip()
        try:
            if recipe:
                from cli.recipes import emit_recipe
                p = emit_recipe(recipe, target=target, project=project)
                self._json(200, {'ok': True, 'path': str(p), 'via': 'recipe'})
                return
            if not message:
                self._json(400, {'ok': False, 'error': 'message or recipe required'})
                return
            import uuid
            root = _root()
            task_id = f'web-{uuid.uuid4().hex[:10]}'
            payload = {
                'id': task_id,
                'project': project,
                'message': message,
                'files': [target] if target else [],
                'channel': 'desktop',
                'status': 'PENDING',
                'metadata': {
                    'source': 'web_dashboard',
                    'prefer_local': True,
                    'intake_source': 'web_dashboard',
                    'primary_channel': 'desktop',
                },
            }
            try:
                from core.task_service import submit_payload
                tid, err = submit_payload(payload, source='web_dashboard', root=root)
                if err or not tid:
                    self._json(400, {'ok': False, 'error': err or 'queue failed'})
                    return
                self._json(200, {'ok': True, 'id': tid, 'via': 'desktop_queue'})
            except Exception as exc:
                self._json(500, {'ok': False, 'error': str(exc)})
        except Exception as exc:
            self._json(500, {'ok': False, 'error': str(exc)})

def run_dashboard(host: str='127.0.0.1', port: int=8337) -> int:
    '''"""run_dashboard(host, port).

Returns:
    Result of run_dashboard.
"""'''
    server = ThreadingHTTPServer((host, port), _Handler)
    print(f'NVCode dashboard  http://{host}:{port}')
    print('  Ctrl+C — стоп. Диспетчер запускайте отдельно: python dispatcher.py')
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print('\ndashboard stopped')
    finally:
        server.server_close()
    return 0

def run_dashboard_background(host: str='127.0.0.1', port: int=8337) -> ThreadingHTTPServer:
    '''"""run_dashboard_background(host, port).

Returns:
    Result of run_dashboard_background.
"""'''
    server = ThreadingHTTPServer((host, port), _Handler)
    th = threading.Thread(target=server.serve_forever, daemon=True)
    th.start()
    return server
