# Tamil IME (Claude-powered)

Type Tanglish in any text box (Teams, Outlook, browser...), press **Ctrl+Alt+T**,
and the text is replaced in place with Tamil script. You still press Enter yourself.

| Key | Action |
|---|---|
| Ctrl+Alt+T | Convert the highlighted text, or the whole focused input box if nothing is highlighted |
| Ctrl+Alt+Q | Quit the IME |

Feedback: a high beep means converted, a low beep means nothing to convert or an error.
Details are in `tamil_ime.log`.

## How it works

```
Teams compose box --Ctrl+A, Ctrl+C--> script --stdin JSON--> headless Claude Code --> Tamil --Ctrl+V--> Teams
```

The script keeps one `claude -p` process alive in streaming JSON mode and sends each
conversion as a message. This uses your Claude Code login (no API key) and pays the
~15 s startup once. Conversions then take about 2 seconds. The process is recycled
every 40 conversions, with its replacement pre-warmed, so context never piles up.

Where the time goes, measured: about 1 s is the model, about 1 s is the request round
trip. Claude Code also does ~2 s of bookkeeping after each answer; the script returns
as soon as the answer text arrives rather than waiting for that. Phrases you have
converted before are served from an in-memory cache instantly.

## Run

Hidden, no console window:

```
cscript //nologo start_tamil_ime.vbs
```

With a console (useful for debugging):

```
uv run tamil_ime.py
```

## Start at login

Press Win+R, type `shell:startup`, press Enter, and copy `start_tamil_ime.vbs` into
the folder that opens. Delete it from there to undo.

## Corporate proxy

If your network needs a proxy, copy `proxy.env.example` to `proxy.env` and fill in the
values. Programs started from Explorer or the Startup folder do not inherit proxy settings
that only exist in a terminal, so without this file the hidden Claude Code process cannot
reach Anthropic and every conversion fails with a low beep. Claude's own error output is
written to `claude_worker.log`.

## Notes

- Text you convert is sent to Anthropic through Claude Code. Fine for casual Tamil chat;
  keep company data policy in mind for anything sensitive.
- Claude Code warms up for ~15 s after launch. Conversions during that window wait for it.
- Relaunching the .vbs while a copy is already running stops the old copy automatically.
- If conversions start failing with a low beep, your Claude Code login may have expired.
  Run `claude /login` in a terminal, then Ctrl+Alt+Q and relaunch.
- To change the hotkey or model, edit the constants at the top of `tamil_ime.py`.
