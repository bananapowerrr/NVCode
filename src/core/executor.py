# -*- coding: utf-8 -*-
"""Запуск CLI-воркеров: stream + LoopGuard + silence watchdog."""
from __future__ import annotations

import json
import os
import re
import subprocess
import sys
import threading
import time
from dataclasses import dataclass
from pathlib import Path
from shutil import which
from typing import Callable

from .config import AIDER_PATH, OPENCODE_PATH, AIDER_PYTHON, AIDER_MODEL, OLLAMA_PATH
from safety.loopguard import LoopGuard
from .workers import Worker

# Silence watchdog: нет строк N сек → пульс; не убиваем до timeout
_SILENCE_PULSE_SEC = 45.0
_SILENCE_WARN_SEC = 120.0


def _soft_log(where: str, err: BaseException) -> None:
    """Non-fatal executor errors — never silent; never raise."""
    try:
        import logging

        logging.getLogger("agentbus.executor").warning(
            "executor.%s: %s: %s", where, type(err).__name__, err
        )
    except Exception:
        try:
            sys.stderr.write(f"[agentbus.executor] {where}: {type(err).__name__}: {err}\n")
        except Exception:
            pass


# Absolute ceiling: worker.timeout cannot exceed this (hung Ollama must not block forever)
def _exec_hard_cap() -> int:
    try:
        from core.timeout_policy import EXEC_HARD_CAP

        return int(EXEC_HARD_CAP)
    except Exception:
        try:
            return max(60, int(os.getenv("AGENTBUS_EXEC_HARD_CAP", "1800") or "1800"))
        except (TypeError, ValueError):
            return 1800


def _clamp_timeout(timeout: int) -> int:
    try:
        from core.timeout_policy import clamp_exec_timeout

        return clamp_exec_timeout(timeout)
    except Exception:
        cap = _exec_hard_cap()
        try:
            t = int(timeout)
        except (TypeError, ValueError):
            t = 300
        return max(15, min(t, cap))


@dataclass
class ExecutionResult:
    """Unified subprocess / worker execution outcome (P0 hardening contract)."""

    ok: bool
    timed_out: bool = False
    code: int | None = None
    stdout: str = ""
    stderr: str = ""
    latency: float = 0.0
    billing_error: bool = False
    rate_limit_error: bool = False
    loop_error: bool = False
    error: str = ""  # short machine-readable reason
    cancelled: bool = False

    @property
    def success(self) -> bool:
        """Alias: only True when process exited 0 and not timed out/cancelled/loop."""
        return bool(self.ok) and not self.timed_out and not self.cancelled and not self.loop_error

    @property
    def exit_code(self) -> int | None:
        return self.code

    @property
    def duration(self) -> float:
        return float(self.latency or 0.0)

    def to_dict(self) -> dict:
        return {
            "ok": self.ok,
            "success": self.success,
            "timed_out": self.timed_out,
            "cancelled": self.cancelled,
            "code": self.code,
            "exit_code": self.code,
            "stdout": self.stdout[-4000:],
            "stderr": self.stderr[-2000:],
            "latency": round(self.latency, 3),
            "error": self.error or (
                "timeout" if self.timed_out else
                "loop" if self.loop_error else
                "cancelled" if self.cancelled else
                ("" if self.ok else "failed")
            ),
            "billing_error": self.billing_error,
            "rate_limit_error": self.rate_limit_error,
            "loop_error": self.loop_error,
        }


_BILLING_ERROR_MARKERS = (
    "insufficient credits", "billing required", "payment required",
    "quota exceeded", "402", "balance insufficient", "no credits",
)
_RATE_LIMIT_MARKERS = (
    "429", "rate limit", "rate_limit", "too many requests",
    "retry-after", "retry after", "rpm limit", "requests per minute",
)


def is_billing_error(text: str) -> bool:
    haystack = (text or "").lower()
    return any(m in haystack for m in _BILLING_ERROR_MARKERS)


def is_rate_limit_error(text: str) -> bool:
    if is_billing_error(text):
        return False
    haystack = (text or "").lower()
    if re.search(r"\b429\b", haystack):
        return True
    return any(m in haystack for m in _RATE_LIMIT_MARKERS)


_OLLAMA_OK_UNTIL = 0.0
_AIDER_DEPS_CHECKED = False
_AIDER_DEPS_OK = False
_AIDER_DEPS_LOCK = threading.Lock()

_GITPYTHON_SNIPPET = (
    "import json\n"
    "d={'ok':False,'path':'','version':'','exc':'','exc_obj':'','reason':''}\n"
    "try:\n"
    "    import git\n"
    "    d['path']=getattr(git,'__file__','')\n"
    "    d['version']=getattr(git,'__version__','')\n"
    "    import git.exc\n"
    "    g=git.exc.GitCommandError('cmd','status')\n"
    "    d['exc_obj']=type(g).__name__\n"
    "    d['ok']=True\n"
    "except Exception as e:\n"
    "    d['exc']=type(e).__name__\n"
    "    d['reason']=str(e)[:300]\n"
    "print(json.dumps(d,ensure_ascii=False))\n"
)


class Executor:
    """Runs harness CLI (aider/opencode/…) with stream + timeout + loop guard."""

    def __init__(self, aider: str | None = None, opencode: str | None = None,
                 ollama: str | None = None, aider_python: str | None = None,
                 aider_model: str | None = None) -> None:
        self.paths = {
            "{aider}": aider or os.getenv("AIDER_PATH", AIDER_PATH),
            "{opencode}": opencode or os.getenv("OPENCODE_PATH", OPENCODE_PATH),
            "{ollama}": ollama or os.getenv("OLLAMA_PATH", OLLAMA_PATH),
            "{aider_python}": aider_python or os.getenv("AIDER_PYTHON", AIDER_PYTHON),
            "{aider_model}": aider_model or os.getenv("AIDER_MODEL", AIDER_MODEL),
        }
        self.on_line: Callable[[str], None] | None = None

    @staticmethod
    def _relativize(path: str, project: str | os.PathLike[str] | None) -> str:
        """Путь относительно корня проекта.

        Абсолютный путь в `--file` заставляет aider развернуть в чат весь
        проект: наблюдалось «estimated chat context of 234,471 tokens exceeds
        the 32,768 token limit», дальше aider печатает «- Use /drop to remove
        unneeded files», модель повторяет эту строку, n-gram-guard даёт
        LOOP_ERROR (c-long-040046). С относительным путём aider добавляет
        только целевой файл («Added test_manual.py to the chat.»).
        Вне проекта (или без project) путь не трогаем.
        """
        if not project:
            return path
        try:
            base = Path(os.path.abspath(str(project)))
            target = Path(os.path.abspath(path))
            rel = os.path.relpath(str(target), str(base))
        except (OSError, ValueError):
            return path
        if rel.startswith(os.pardir):
            return path
        return rel

    def _args(self, worker: Worker, message: str, files: list[str],
              model: str | None = None, project: str | os.PathLike[str] | None = None) -> list[str]:
        result: list[str] = []
        for token in worker.command:
            if token in self.paths:
                result.append(self.paths[token])
            elif token == "{message}":
                result.append(message)
            elif token == "{files}":
                for f in files:
                    result += ["--file", self._relativize(f, project)]
            elif token == "{yes}":
                result.append("--yes")
            elif token == "{model}":
                result.append(model if model is not None else self.paths.get("{aider_model}", ""))
            else:
                result.append(token)
        # Aider сам декодирует файлы модели и печатает
        # "Use --encoding to set the unicode encoding." при не-UTF-8 содержимом.
        # Модель затем повторяет эту строку, ngram-guard срабатывает и задача
        # уходит в LOOP_ERROR -> health_unavailable -> cooldown (c-long-040046).
        # Флаг задаётся здесь, на уровне harness, чтобы покрыть сразу все
        # aider-воркеры (aider_local, aider_openrouter, ...) без дублирования
        # в workers.yaml.
        if result and not any(t == "--encoding" for t in result):
            # aider вызывается как `{aider_python} -m aider ...`, поэтому
            # искать «aider» только в имени исполняемого файла нельзя:
            # result[0] — это python.exe.
            #
            # Флаг должен стоять ПОСЛЕ `-m aider`: иначе его получает
            # интерпретатор (`python --encoding utf-8 -m aider` — валидная
            # опция python для кодировки stdio), а aider продолжает читать
            # файлы репозитория своей кодировкой. Именно это приводило к
            # "Use --encoding to set the unicode encoding." -> повтор строки
            # моделью -> ngram-guard -> LOOP_ERROR (c-long-040046).
            idx = next(
                (
                    i
                    for i, t in enumerate(result[:3])
                    if "aider" in str(t).lower()
                ),
                None,
            )
            if idx is not None:
                result.insert(idx + 1, "--encoding")
                result.insert(idx + 2, "utf-8")
        return result

    def _kill_tree(self, pid: int) -> None:
        """Force-kill worker process and children (Windows job / Unix process group)."""
        if os.name == "nt":
            try:
                subprocess.run(
                    ["taskkill", "/PID", str(pid), "/T", "/F"],
                    capture_output=True,
                    timeout=10,
                )
            except OSError as e:
                _soft_log("kill_tree.taskkill", e)
            return
        # Unix: kill process group first (requires start_new_session on Popen)
        try:
            os.killpg(pid, 9)
        except (ProcessLookupError, PermissionError, OSError) as e:
            _soft_log("kill_tree.killpg", e)
            try:
                os.kill(pid, 9)
            except ProcessLookupError as e2:
                _soft_log("kill_tree.kill", e2)

    def _ollama_alive(self) -> bool:
        global _OLLAMA_OK_UNTIL
        if time.time() < _OLLAMA_OK_UNTIL:
            return True
        base = os.getenv("OLLAMA_API_BASE", "http://127.0.0.1:11434").rstrip("/")
        try:
            import urllib.request
            with urllib.request.urlopen(base + "/api/tags", timeout=3):
                _OLLAMA_OK_UNTIL = time.time() + 60
            return True
        except Exception as e:
            _soft_log("ollama_alive", e)
            return False

    def _env(self, worker: Worker | None = None) -> dict[str, str]:
        env = os.environ.copy()
        env["PYTHONIOENCODING"] = "utf-8"
        env["PYTHONUTF8"] = "1"
        env["TERM"] = "dumb"
        env["NO_COLOR"] = "1"
        env.setdefault("OLLAMA_API_BASE", "http://127.0.0.1:11434")
        env.setdefault("OLLAMA_NUM_CTX", "8192")
        env["GCM_INTERACTIVE"] = "0"
        env["GIT_TERMINAL_PROMPT"] = "0"
        env["GIT_ASKPASS"] = ""
        env["GIT_PYTHON_REFRESH"] = "quiet"
        env.setdefault("LC_ALL", "C.UTF-8")
        env.setdefault("LANG", "C.UTF-8")
        if worker is not None:
            if worker.api_base:
                env["OPENAI_API_BASE"] = worker.api_base.rstrip("/")
            if worker.api_key_env:
                key = os.getenv(worker.api_key_env, "")
                if key:
                    env["OPENAI_API_KEY"] = key
        return env

    def _foreign_env(self, provider, worker: Worker | None = None) -> dict[str, str]:
        env = self._env(worker)
        base = getattr(provider, "base_url", "") or ""
        key = getattr(provider, "api_key", "") or ""
        key_env = getattr(provider, "api_key_env", "") or ""
        if worker is not None:
            if worker.api_base:
                base = worker.api_base
            if worker.api_key_env:
                key_env = worker.api_key_env
        if key_env:
            key = os.getenv(key_env, "") or key
        if base:
            env["OPENAI_API_BASE"] = base.rstrip("/")
        if key:
            env["OPENAI_API_KEY"] = key
        env.setdefault("OPENAI_API_TYPE", "open_ai")
        return env

    def _run_model(self, provider, worker) -> str:
        ptype = getattr(provider, "type", "") or "openai_compatible"
        wmodel = (worker.model or "").strip()
        if (not wmodel or wmodel == "auto") and getattr(provider, "models", None):
            wmodel = provider.models[0]
        if ptype == "openai_compatible" and wmodel and not wmodel.startswith("openai/"):
            return "openai/" + wmodel
        return wmodel or "openai/unknown"

    def run_foreign(self, worker: Worker, provider, project: str, message: str,
                    timeout: int, files: list[str] | None = None) -> ExecutionResult:
        files = files or []
        args = self._args(worker, message, files,
                          model=self._run_model(provider, worker), project=project)
        # Финальный argv перед subprocess: единственное место, где видно,
        # дошёл ли --encoding utf-8 до aider на самом деле.
        try:
            import logging as _lg
            _lg.getLogger("agentbus.executor").info(
                "EXECUTOR: final argv (%s) = %s", worker.name, " ".join(map(str, args)))
        except Exception:
            pass
        return self._run_with_env(args, project, timeout, self._foreign_env(provider, worker))

    def _classify(self, text: str) -> tuple[bool, bool]:
        billing = is_billing_error(text)
        rate = (not billing) and is_rate_limit_error(text)
        return billing, rate

    def _run_with_env(self, args: list[str], project: str, timeout: int,
                      env: dict[str, str] | None = None,
                      *, disable_loop_guard: bool = False) -> ExecutionResult:
        timeout = _clamp_timeout(timeout)
        if not args:
            return ExecutionResult(False, stderr="пустая команда исполнителя")
        executable = args[0].strip('"')
        if not Path(executable).is_file() and which(executable) is None:
            return ExecutionResult(False, stderr=f"исполнитель не найден: {executable}")
        env = env or self._env()
        started = time.monotonic()
        guard = LoopGuard()
        stdout_buf: list[str] = []
        stderr_buf: list[str] = []
        loop_hit = None
        stop_flag = threading.Event()
        last_activity = [time.monotonic()]
        last_silence_emit = [0.0]

        def _reader(pipe, buf: list[str], _is_err: bool = False) -> None:
            nonlocal loop_hit
            try:
                for line in iter(pipe.readline, ""):
                    if stop_flag.is_set():
                        break
                    buf.append(line)
                    # hard cap buffer lines (~ keep last 50k lines worth of churn control)
                    if len(buf) > 50000:
                        del buf[:-20000]
                    last_activity[0] = time.monotonic()
                    if self.on_line:
                        try:
                            self.on_line(line)
                        except Exception as cb_err:
                            _soft_log("on_line", cb_err)
                    if not disable_loop_guard:
                        hit = guard.feed_line(line)
                        if hit and loop_hit is None:
                            loop_hit = hit
                            stop_flag.set()
                            break
            except Exception as read_err:
                _soft_log("pipe_reader", read_err)
            finally:
                try:
                    pipe.close()
                except Exception as close_err:
                    _soft_log("pipe_close", close_err)

        try:
            popen_kwargs = dict(
                args=args, cwd=project, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                text=True, encoding="utf-8", errors="replace", env=env, shell=False,
                bufsize=1,
            )
            if os.name == "nt":
                popen_kwargs["creationflags"] = subprocess.CREATE_NEW_PROCESS_GROUP
            else:
                popen_kwargs["start_new_session"] = True  # own PGID → killpg on timeout
            proc = subprocess.Popen(**popen_kwargs)
            t_out = threading.Thread(target=_reader, args=(proc.stdout, stdout_buf), daemon=True)
            t_err = threading.Thread(target=_reader, args=(proc.stderr, stderr_buf, True), daemon=True)
            t_out.start()
            t_err.start()

            deadline = started + timeout
            while proc.poll() is None:
                if stop_flag.is_set() or loop_hit is not None:
                    self._kill_tree(proc.pid)
                    break
                now = time.monotonic()
                if now > deadline:
                    self._kill_tree(proc.pid)
                    t_out.join(timeout=2)
                    t_err.join(timeout=2)
                    stdout = "".join(stdout_buf)[-20000:]
                    stderr = "".join(stderr_buf)[-5000:]
                    text = stdout + "\n" + stderr
                    billing, rate = self._classify(text)
                    return ExecutionResult(
                        False, True, None, stdout,
                        "тайм-аут исполнителя; процесс остановлен\n" + stderr,
                        latency=now - started,
                        billing_error=billing, rate_limit_error=rate,
                        error="timeout",
                    )
                # silence watchdog — процесс жив, но молчит
                silent = now - last_activity[0]
                if silent >= _SILENCE_PULSE_SEC and (now - last_silence_emit[0]) >= _SILENCE_PULSE_SEC:
                    last_silence_emit[0] = now
                    if silent >= _SILENCE_WARN_SEC:
                        msg = f"[тишина {int(silent)}с — модель молчит, процесс ещё жив]\n"
                    else:
                        msg = f"[тишина {int(silent)}с — жду вывод]\n"
                    if self.on_line:
                        try:
                            self.on_line(msg)
                        except Exception as cb_err:
                            _soft_log("on_line.silence", cb_err)
                time.sleep(0.15)

            t_out.join(timeout=3)
            t_err.join(timeout=3)
            try:
                proc.wait(timeout=2)
            except Exception as wait_err:
                _soft_log("proc.wait", wait_err)

            stdout = "".join(stdout_buf)[-20000:]
            stderr = "".join(stderr_buf)[-20000:]
            text = stdout + "\n" + stderr
            billing, rate = self._classify(text)

            if loop_hit is not None:
                msg = f"зацикливание ({loop_hit.kind}): {loop_hit.detail}"
                return ExecutionResult(
                    False, False, proc.returncode, stdout,
                    (stderr + "\n" + msg).strip(),
                    latency=time.monotonic() - started,
                    billing_error=billing, rate_limit_error=rate, loop_error=True,
                )

            return ExecutionResult(
                proc.returncode == 0, False, proc.returncode, stdout, stderr,
                latency=time.monotonic() - started,
                billing_error=billing, rate_limit_error=rate,
            )
        except (OSError, ValueError) as exc:
            text = f"не удалось запустить исполнителя: {type(exc).__name__}: {exc}"
            billing, rate = self._classify(text)
            return ExecutionResult(
                False, stderr=text, latency=time.monotonic() - started,
                billing_error=billing, rate_limit_error=rate,
            )

    def _run(self, args: list[str], project: str, timeout: int,
             worker: Worker | None = None) -> ExecutionResult:
        return self._run_with_env(args, project, timeout, self._env(worker))

    def run_command(
        self,
        args: list[str],
        *,
        cwd: str | None = None,
        timeout: int = 60,
        env: dict[str, str] | None = None,
    ) -> ExecutionResult:
        """Public low-level runner for offline tests and simple shell jobs.

        Always returns ExecutionResult; never raises on non-zero exit / timeout.
        """
        project = cwd or os.getcwd()
        return self._run_with_env(
            list(args), project, timeout, env, disable_loop_guard=True
        )

    def _repair_aider_gitpython(self, python: str, timeout: int = 180) -> ExecutionResult:
        python = python if Path(python).exists() else sys.executable
        self._run([python, "-m", "pip", "uninstall", "-y", "git"],
                  str(Path(python).parent), timeout)
        install = self._run(
            [python, "-m", "pip", "install", "--upgrade", "--force-reinstall", "GitPython"],
            str(Path(python).parent), timeout)
        return install

    def _gitpython_report(self, python: str, env: dict[str, str] | None = None) -> dict:
        _CRASH = (3221225477, 0xC0000005, -1073741819)
        last_exc = ""
        for attempt in range(6):
            try:
                p = subprocess.run([python, "-c", _GITPYTHON_SNIPPET],
                                   capture_output=True, text=True, encoding="utf-8",
                                   errors="replace", timeout=60, env=env, shell=False)
                if p.returncode in _CRASH:
                    time.sleep(0.4 + attempt * 0.3)
                    continue
                out = (p.stdout or "").strip()
                if p.returncode != 0 or not out:
                    return {"ok": False, "path": "", "version": "", "exc": "",
                            "exc_obj": "", "reason": (p.stderr or "").strip()[:300]}
                return json.loads(out.splitlines()[-1])
            except subprocess.TimeoutExpired:
                return {"ok": False, "path": "", "version": "", "exc": "Timeout",
                        "exc_obj": "", "reason": "GitPython probe timeout"}
            except Exception as exc:
                last_exc = f"{type(exc).__name__}: {exc}"
                time.sleep(0.4 + attempt * 0.3)
        return {"ok": False, "path": "", "version": "", "exc": "SpawnFailed",
                "exc_obj": "", "reason": last_exc[:300] or "spawn failed"}

    def _ensure_aider_deps(self) -> ExecutionResult:
        global _AIDER_DEPS_CHECKED, _AIDER_DEPS_OK
        with _AIDER_DEPS_LOCK:
            if _AIDER_DEPS_CHECKED:
                return ExecutionResult(_AIDER_DEPS_OK)
            python = self.paths["{aider_python}"] if Path(self.paths["{aider_python}"]).exists() else sys.executable
            report = self._gitpython_report(python, self._env())
            if report.get("ok"):
                _AIDER_DEPS_OK = True
            else:
                res = self._repair_aider_gitpython(python)
                if res.ok:
                    report2 = self._gitpython_report(python, self._env())
                    _AIDER_DEPS_OK = bool(report2.get("ok"))
                    if not _AIDER_DEPS_OK:
                        return ExecutionResult(
                            False,
                            stderr=("GitPython несовместим после repair: "
                                    + (report2.get("reason") or ""))[:2000],
                        )
                else:
                    _AIDER_DEPS_OK = False
                    return res
            _AIDER_DEPS_CHECKED = True
            return ExecutionResult(_AIDER_DEPS_OK)

    def try_native_fallback(
        self,
        worker: Worker,
        message: str,
        files: list[str] | None = None,
        cwd: str = "",
    ) -> ExecutionResult | None:
        """If Aider CLI is missing or fails, try OpenAI-compatible native tools."""
        try:
            from core.native_backend import native_backend_enabled, run_native_tool_round
            use = native_backend_enabled(worker) or (
                os.getenv("AGENTBUS_NATIVE_FALLBACK", "1").strip().lower()
                in ("1", "true", "yes", "on")
            )
            if not use:
                return None
            api_base = str(
                getattr(worker, "api_base", None)
                or os.getenv("OPENAI_API_BASE")
                or os.getenv("OLLAMA_HOST")
                or "http://127.0.0.1:11434/v1"
            )
            if "11434" in api_base and not api_base.rstrip("/").endswith("/v1"):
                api_base = api_base.rstrip("/") + "/v1"
            api_key = str(
                getattr(worker, "api_key", None)
                or os.getenv("OPENAI_API_KEY")
                or "ollama"
            )
            model = str(
                getattr(worker, "model", None)
                or os.getenv("CODER_MODEL")
                or "qwen2.5-coder:7b"
            )
            tools: list = []
            try:
                from core.tool_registry import tools_for_worker
                tools = tools_for_worker(worker) or []
            except Exception as reg_err:
                _soft_log("tools_for_worker", reg_err)
                tools = []
            out = run_native_tool_round(
                api_base=api_base,
                api_key=api_key,
                model=model,
                messages=[{"role": "user", "content": message}],
                tools=tools,
                timeout=int(os.getenv("AGENTBUS_NATIVE_TIMEOUT", "180") or 180),
            )
            ok = bool(out.get("ok"))
            content = str(out.get("content") or out.get("error") or "")
            return ExecutionResult(ok=ok, stdout=content, stderr="" if ok else content)
        except Exception as exp:
            _soft_log("native_fallback", exp)
            return ExecutionResult(ok=False, stdout="", stderr=f"native_fallback: {exp}")

    @staticmethod
    def _ensure_target_files(project: str, files: list[str]) -> list[str]:
        """TD-027 (C): не создавать пустые заглушки.

        Returns list of relative paths that are missing (not created).
        """
        missing: list[str] = []
        if not project or not files:
            return missing
        root = Path(project)
        try:
            root_resolved = root.resolve()
        except OSError:
            return missing
        for rel in files:
            try:
                name = str(rel or "").strip()
                if not name:
                    continue
                target = (root / name).resolve()
                try:
                    target.relative_to(root_resolved)
                except ValueError:
                    continue
                if target.is_file():
                    continue
                missing.append(name)
            except OSError:
                missing.append(str(rel or "").strip() or "?")
        return missing

    def run(self, worker: Worker, project: str, message: str, timeout: int,
            files: list[str] | None = None) -> ExecutionResult:
        files = files or []
        missing = self._ensure_target_files(project, files)
        if missing and files:
            existing = []
            root = Path(project) if project else None
            for rel in files:
                try:
                    if root and (root / str(rel)).is_file():
                        existing.append(rel)
                except OSError:
                    pass
            if not existing:
                return ExecutionResult(
                    ok=False,
                    stdout="",
                    stderr=(
                        "TD-027: target files missing (empty stubs disabled): "
                        + ", ".join(missing[:12])
                    ),
                )
        # Native-first when profile says so
        try:
            from core.native_backend import native_backend_enabled
            from core.model_profiles import profile_for_worker
            prefer_native = native_backend_enabled(worker)
            try:
                prefer_native = prefer_native or profile_for_worker(worker).is_native_tools
            except Exception as pe:
                _soft_log("profile_for_worker", pe)
            if prefer_native:
                nf = self.try_native_fallback(worker, message, cwd=project)
                if nf is not None and nf.ok:
                    return nf
        except Exception as run_pref:
            _soft_log("prefer_native", run_pref)
        args = self._args(worker, message, files, project=project)
        if worker.harness == "aider":
            if worker.provider == "ollama" and not self._ollama_alive():
                return ExecutionResult(False, stderr="ollama недоступен (boot-check)")
            deps = self._ensure_aider_deps()
            if not deps.ok:
                return ExecutionResult(False, stderr=deps.stderr[:2000],
                                       billing_error=deps.billing_error,
                                       rate_limit_error=deps.rate_limit_error)
        result = self._run(args, project, timeout, worker)
        git_error = "module 'git' has no attribute 'exc'"
        if worker.harness == "aider" and not result.ok and git_error in (result.stderr + result.stdout):
            python = self.paths.get("{aider_python}", sys.executable)
            repair = self._repair_aider_gitpython(python)
            if repair.ok:
                result = self._run(args, project, timeout, worker)
            else:
                result.stderr = (result.stderr + "\nGitPython repair fail:\n" + repair.stderr)[-20000:]
        text = (result.stdout or "") + "\n" + (result.stderr or "")
        billing, rate = self._classify(text)
        result.billing_error = result.billing_error or billing
        result.rate_limit_error = result.rate_limit_error or rate
        if "зацикливание" in text.lower():
            result.loop_error = True
        return result
