"""
Tamil IME powered by Claude Code.

Type Tanglish (Tamil in Latin letters) into any text box, press Ctrl+Alt+T,
and the text is replaced in place with Tamil script. If some text is
highlighted, only that part is converted; otherwise the whole box is.
Ctrl+Alt+Q quits.

Transliteration runs through a long-lived headless Claude Code process
(`claude -p --input-format stream-json`), so it uses your existing Claude Code
login and pays the ~20s startup cost once, at launch, not per keypress.

Run:  uv run tamil_ime.py          (or start_tamil_ime.vbs for no console window)
Log:  tamil_ime.log next to this file
"""

from __future__ import annotations

import json
import logging
import os
import queue
import shutil
import subprocess
import sys
import threading
import time
import winsound
from pathlib import Path

import pyperclip
from pynput import keyboard

HOTKEY_CONVERT = "<ctrl>+<alt>+t"
HOTKEY_QUIT = "<ctrl>+<alt>+q"
MODEL = "haiku"
REPLY_TIMEOUT_S = 60
TURNS_PER_WORKER = 40  # recycle the Claude process so its context never grows unbounded
WARMUP_TEXT = "vanakkam"  # sent once at startup so the first real keypress is fast

SYSTEM_PROMPT = """You are a Tanglish-to-Tamil transliteration engine. Each user message is Tamil written in Latin letters, mixed with English words. Rewrite it so that Tamil words appear in Tamil script and everything else is left exactly as typed.

Decide word by word:
- If the word is an English word (testing, meeting, leave, call, report, doubt, ok, pls, thanks, Teams, file, update, mail, send, team, project, deadline, etc.), keep it EXACTLY as typed in Latin letters. Never transliterate English into Tamil script, even if Tamil speakers commonly say it.
- If the word is Tamil spelled in Latin letters (seigiren, paakalam, irukku, enakku, vanakkam, naalaiku, pannunga, etc.), write it in Tamil script.
- Short Tamil words and particles are Tamil too and must be converted: naan, nee, da, di, la, ku, ah, um, illa, aama, seri, enna, eppo, inga, anga, oru, romba. Never leave these in Latin letters.
- If an English word has a Tamil suffix attached (meetingla, callku, fileoda, Teams la), keep the English part in Latin letters and write only the suffix in Tamil, joined with a hyphen: meeting-ல, call-க்கு, file-ஓட, Teams-ல.
- Names, numbers, times, URLs, punctuation, emoji, and line breaks stay exactly as typed.
- People type Tamil the way it sounds in fast speech, dropping vowels and syllables. Do NOT transliterate the clipped spelling letter by letter. Recover the full standard spoken word and write that: therla -> தெரியலை (not தெரல), purila -> புரியலை, mudila -> முடியலை, epdi -> எப்படி, apdi -> அப்படி, ipo -> இப்போ, apo -> அப்போ, sapten -> சாப்பிட்டேன், saptiya -> சாப்பிட்டியா, paathen -> பார்த்தேன், pathiya -> பார்த்தியா, venam -> வேணாம், varala -> வரலை, pannala -> பண்ணலை, ethuku -> எதுக்கு, yen -> ஏன். Use the everyday spoken form, not the literary form (தெரியலை, never தெரியவில்லை).

Examples:
testing seigiren -> testing செய்கிறேன்
naalaiku meeting la paakalam -> நாளைக்கு meeting-ல பாக்கலாம்
inniku leave edukuren -> இன்னிக்கு leave எடுக்குறேன்
enakku oru doubt irukku, call pannunga -> எனக்கு ஒரு doubt இருக்கு, call பண்ணுங்க
report anuppitten, check pannunga pls -> report அனுப்பிட்டேன், check பண்ணுங்க pls
ok da, sapittu varen -> ok டா, சாப்பிட்டு வரேன்
naan 3pm ku join panren -> நான் 3pm-க்கு join பண்றேன்
seri, update pannitu sollu -> சரி, update பண்ணிட்டு சொல்லு
romba thanks da -> ரொம்ப thanks டா
saptiya? -> சாப்பிட்டியா?
enakku therla da -> எனக்கு தெரியலை டா
epdi irukka? ipo office la than irukken -> எப்படி இருக்க? இப்போ office-ல தான் இருக்கேன்
antha file purila, mudila -> அந்த file புரியலை, முடியலை

Treat every message independently. Output ONLY the rewritten text: no quotes, no explanation, no preamble."""

HERE = Path(__file__).resolve().parent
LOG = HERE / "tamil_ime.log"
CLAUDE_LOG = HERE / "claude_worker.log"  # stderr of the hidden Claude Code process
PIDFILE = HERE / "tamil_ime.pid"
PROXY_ENV = HERE / "proxy.env"
CLAUDE = shutil.which("claude") or "claude"


def worker_env() -> dict[str, str]:
    """Environment for the Claude process.

    When launched from Explorer / Startup, the process does not inherit the
    proxy variables that a corporate network needs, so Claude Code cannot reach
    Anthropic and exits. proxy.env (KEY=VALUE lines) fills them in.
    """
    env = dict(os.environ)
    if PROXY_ENV.exists():
        for line in PROXY_ENV.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if line and not line.startswith("#") and "=" in line:
                k, v = line.split("=", 1)
                env.setdefault(k.strip(), v.strip())
    return env

logging.basicConfig(
    filename=LOG, level=logging.INFO, encoding="utf-8",
    format="%(asctime)s %(levelname)s %(message)s",
)

kb = keyboard.Controller()
busy = threading.Lock()


# --------------------------------------------------------------------------- #
# Claude worker
# --------------------------------------------------------------------------- #


class ClaudeWorker:
    """One persistent headless Claude Code process speaking newline-delimited JSON."""

    def __init__(self, warm: bool = True) -> None:
        cmd = [
            CLAUDE, "-p",
            "--model", MODEL,
            "--tools", "",
            "--strict-mcp-config",
            "--no-session-persistence",
            "--setting-sources", "",
            "--input-format", "stream-json",
            "--output-format", "stream-json",
            "--verbose",
            "--system-prompt", SYSTEM_PROMPT,
        ]
        # No shell=True: launching the .exe directly means terminate()/taskkill hit
        # the real process instead of an intermediate cmd.exe, so no orphans.
        self.stderr_file = open(CLAUDE_LOG, "a", encoding="utf-8")
        self.proc = subprocess.Popen(
            cmd, stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=self.stderr_file,
            text=True, encoding="utf-8", bufsize=1, env=worker_env(),
            creationflags=subprocess.CREATE_NO_WINDOW,
        )
        self.events: queue.Queue[str] = queue.Queue()
        self.io_lock = threading.Lock()
        self.turns = 0
        self.pending_results = 0  # 'result' events still owed from early-returned turns
        self.warm = threading.Event()
        threading.Thread(target=self._pump, daemon=True).start()
        if warm:
            threading.Thread(target=self._warmup, daemon=True).start()
        else:
            self.warm.set()

    def _pump(self) -> None:
        for line in self.proc.stdout:
            self.events.put(line)

    def _warmup(self) -> None:
        t0 = time.time()
        try:
            self.ask(WARMUP_TEXT, count=False)
            logging.info("worker warm in %.1fs", time.time() - t0)
        except Exception as e:
            logging.warning("warmup failed: %s", e)
        finally:
            self.warm.set()

    def alive(self) -> bool:
        return self.proc.poll() is None

    def _drain(self) -> None:
        """Discard events left over from the previous turn (its trailing 'result')."""
        while True:
            try:
                line = self.events.get_nowait()
            except queue.Empty:
                return
            try:
                if json.loads(line).get("type") == "result" and self.pending_results > 0:
                    self.pending_results -= 1
            except json.JSONDecodeError:
                pass

    @staticmethod
    def _clean(out: str) -> str:
        out = out.strip()
        if len(out) >= 2 and out[0] == out[-1] and out[0] in "\"'":
            out = out[1:-1]
        return out

    def ask(self, text: str, count: bool = True) -> str:
        with self.io_lock:  # one request in flight per process
            self._drain()
            msg = {"type": "user", "message": {"role": "user", "content": text}}
            self.proc.stdin.write(json.dumps(msg) + "\n")
            self.proc.stdin.flush()
            deadline = time.time() + REPLY_TIMEOUT_S
            while time.time() < deadline:
                try:
                    ev = json.loads(self.events.get(timeout=max(0.1, deadline - time.time())))
                except (queue.Empty, json.JSONDecodeError):
                    if not self.alive():
                        raise RuntimeError("claude process exited")
                    continue
                kind = ev.get("type")
                # Events arrive strictly in order: assistant_1, result_1, assistant_2, ...
                # The assistant message carries the full answer ~2s before the
                # 'result' bookkeeping event, so we return on it and remember that
                # one 'result' is still owed; that stale result is skipped here.
                if kind == "result":
                    if self.pending_results > 0:
                        self.pending_results -= 1
                        continue
                    if count:
                        self.turns += 1
                    if ev.get("is_error"):
                        raise RuntimeError(ev.get("result") or "claude returned an error")
                    return self._clean(ev.get("result") or "")
                if kind == "assistant":
                    blocks = ev.get("message", {}).get("content", [])
                    text_out = "".join(b.get("text", "") for b in blocks if b.get("type") == "text")
                    if text_out.strip():
                        if count:
                            self.turns += 1
                        self.pending_results += 1
                        return self._clean(text_out)
            raise TimeoutError("no reply from claude")

    def close(self) -> None:
        try:
            self.proc.stdin.close()  # EOF ends the stream-json session gracefully
            self.proc.wait(timeout=3)
        except Exception:
            pass
        if self.alive():
            subprocess.run(
                ["taskkill", "/PID", str(self.proc.pid), "/T", "/F"],
                capture_output=True, creationflags=subprocess.CREATE_NO_WINDOW,
            )
        try:
            self.stderr_file.close()
        except Exception:
            pass


class WorkerPool:
    """Keeps one live worker and pre-warms its replacement before recycling."""

    def __init__(self) -> None:
        self.current = ClaudeWorker()
        self.next: ClaudeWorker | None = None
        self.lock = threading.Lock()

    def _rotate(self) -> None:
        old = self.current
        self.current, self.next = self.next, None
        threading.Thread(target=old.close, daemon=True).start()

    def ask(self, text: str) -> str:
        with self.lock:
            w = self.current
            if not w.alive():
                logging.warning("worker exited (rc=%s); replacing", w.proc.returncode)
                if self.next is not None:
                    self._rotate()
                else:
                    self.current = ClaudeWorker(warm=False)
                w = self.current
            elif w.turns >= TURNS_PER_WORKER - 5 and self.next is None:
                self.next = ClaudeWorker()
            elif w.turns >= TURNS_PER_WORKER and self.next is not None and self.next.warm.is_set():
                self._rotate()
                w = self.current
        return w.ask(text)

    def close(self) -> None:
        for w in (self.current, self.next):
            if w is not None:
                w.close()


pool: WorkerPool | None = None
CACHE_SIZE = 500
cache: dict[str, str] = {}  # repeated phrases ("vanakkam") convert instantly


# --------------------------------------------------------------------------- #
# Keyboard / clipboard plumbing
# --------------------------------------------------------------------------- #


def beep(ok: bool) -> None:
    winsound.Beep(880 if ok else 220, 120)


def release_hotkey_keys() -> None:
    for k in (keyboard.Key.ctrl_l, keyboard.Key.ctrl_r, keyboard.Key.alt_l,
              keyboard.Key.alt_r, keyboard.KeyCode.from_char("t")):
        try:
            kb.release(k)
        except Exception:
            pass


def chord(*keys) -> None:
    for k in keys:
        kb.press(k)
    for k in reversed(keys):
        kb.release(k)


def clip_get(retries: int = 5) -> str:
    """Read the clipboard, retrying briefly if another app has it locked."""
    for _ in range(retries):
        try:
            return pyperclip.paste() or ""
        except pyperclip.PyperclipException:
            time.sleep(0.05)
    return ""


def clip_set(text: str, retries: int = 5) -> None:
    for _ in range(retries):
        try:
            pyperclip.copy(text)
            return
        except pyperclip.PyperclipException:
            time.sleep(0.05)


def copy_and_wait(timeout: float = 0.6) -> str:
    """Press Ctrl+C and wait until the clipboard actually changes (apps copy async)."""
    clip_set("")
    chord(keyboard.Key.ctrl, "c")
    deadline = time.time() + timeout
    while time.time() < deadline:
        time.sleep(0.03)
        got = clip_get()
        if got:
            return got
    return ""


def grab_source_text() -> str:
    """Highlighted text if any, otherwise everything in the focused box."""
    text = copy_and_wait(timeout=0.3)
    if text.strip():
        return text
    chord(keyboard.Key.ctrl, "a")
    return copy_and_wait()


def convert_active_field() -> None:
    if not busy.acquire(blocking=False):
        return
    saved = None
    try:
        release_hotkey_keys()
        time.sleep(0.1)
        saved = clip_get()

        source = grab_source_text()
        if not source.strip():
            logging.warning("nothing to convert")
            beep(False)
            return

        t0 = time.time()
        key = " ".join(source.split()).lower()
        tamil = cache.get(key)
        if tamil is None:
            tamil = pool.ask(source)
            if tamil:
                cache[key] = tamil
                if len(cache) > CACHE_SIZE:
                    cache.pop(next(iter(cache)))
        logging.info("%.1fs  %s -> %s", time.time() - t0, source[:80].replace("\n", " / "), tamil[:80].replace("\n", " / "))
        if not tamil:
            beep(False)
            return

        clip_set(tamil)
        chord(keyboard.Key.ctrl, "v")
        time.sleep(0.25)  # let the app finish reading the clipboard before we restore it
        beep(True)
    except Exception as e:
        logging.exception("conversion failed: %s", e)
        beep(False)
    finally:
        if saved is not None:
            clip_set(saved)
        busy.release()


def on_convert() -> None:
    threading.Thread(target=convert_active_field, daemon=True).start()


def on_quit() -> None:
    logging.info("quit via hotkey")
    if pool:
        pool.close()
    PIDFILE.unlink(missing_ok=True)
    os._exit(0)


def stop_previous_instance() -> None:
    """If an older copy is still running (from a previous launch), stop it first."""
    try:
        old = int(PIDFILE.read_text().strip())
    except (FileNotFoundError, ValueError):
        return
    if old != os.getpid():
        subprocess.run(["taskkill", "/PID", str(old), "/T", "/F"],
                       capture_output=True, creationflags=subprocess.CREATE_NO_WINDOW)
        logging.info("stopped previous instance pid %s", old)


if __name__ == "__main__":
    stop_previous_instance()
    PIDFILE.write_text(str(os.getpid()))
    logging.info("tamil-ime starting (pid %s); %s = convert, %s = quit", os.getpid(), HOTKEY_CONVERT, HOTKEY_QUIT)
    pool = WorkerPool()
    try:
        with keyboard.GlobalHotKeys({HOTKEY_CONVERT: on_convert, HOTKEY_QUIT: on_quit}) as h:
            h.join()
    finally:
        pool.close()
        PIDFILE.unlink(missing_ok=True)
