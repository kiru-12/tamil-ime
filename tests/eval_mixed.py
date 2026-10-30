"""Mixed Tanglish/English eval: English words must survive; Tamil words must convert."""
import re, sys, time
sys.path.insert(0, r"C:\Users\sk6\Downloads\tamil-ime")
import tamil_ime as t

# (input, English words that must remain, Tanglish words that must NOT remain)
CASES = [
    ("testing seigiren", ["testing"], ["seigiren"]),
    ("naalaiku meeting la paakalam", ["meeting"], ["naalaiku", "la", "paakalam"]),
    ("inniku leave edukuren", ["leave"], ["inniku", "edukuren"]),
    ("Teams la message anupunga pls", ["Teams", "message", "pls"], ["la", "anupunga"]),
    ("enakku oru doubt irukku, call pannunga", ["doubt", "call"], ["enakku", "oru", "irukku", "pannunga"]),
    ("report anuppitten, check pannunga", ["report", "check"], ["anuppitten", "pannunga"]),
    ("file update pannitten, deadline naalaiku", ["file", "update", "deadline"], ["pannitten", "naalaiku"]),
    ("ok da, sapittu varen", ["ok"], ["da", "sapittu", "varen"]),
    ("project status enna?", ["project", "status"], ["enna"]),
    ("mail anupunga, naan paakkiren", ["mail"], ["anupunga", "naan", "paakkiren"]),
    ("vanakkam, eppadi irukkinga?", [], ["vanakkam", "eppadi", "irukkinga"]),
    ("naan 3pm ku join panren", ["3pm", "join"], ["naan", "ku", "panren"]),
    ("thanks, naalai paakalam", ["thanks"], ["naalai", "paakalam"]),
    ("server down aagiduchu", ["server", "down"], ["aagiduchu"]),
    ("seri, lunch ku poitu varen", ["lunch"], ["seri", "ku", "poitu", "varen"]),
    ("nee eppo office varuva?", ["office"], ["nee", "eppo", "varuva"]),
    ("romba thanks da", ["thanks"], ["romba", "da"]),
]

# (input, Tamil word that must appear, clipped spelling that must NOT appear)
COLLOQUIAL = [
    ("enakku therla da", "தெரியலை", "தெரல"),
    ("antha file purila", "புரியலை", "புரில"),
    ("ipo mudila, apram paakalam", "முடியலை", "முடில"),
    ("epdi irukka?", "எப்படி", "எப்டி"),
    ("sapten, ipo varen", "சாப்பிட்டேன்", "சாப்டேன்"),
    ("yen varala?", "வரலை", "வரல"),
]

def word_in(word, text):
    return re.search(rf"(?<![A-Za-z]){re.escape(word)}(?![A-Za-z])", text, re.I) is not None

w = t.ClaudeWorker()
w.warm.wait(120)
fails = 0
for src, keep, convert in CASES:
    t0 = time.time()
    out = w.ask(src)
    dt = time.time() - t0
    missing = [k for k in keep if not word_in(k, out)]
    leftover = [k for k in convert if word_in(k, out)]
    ok = not missing and not leftover
    fails += not ok
    note = (f"  english_lost={missing}" if missing else "") + (f"  tamil_left={leftover}" if leftover else "")
    print(f"{'OK  ' if ok else 'FAIL'} {dt:4.1f}s  {src!r:45s} -> {out!r}{note}")
for src, want, bad in COLLOQUIAL:
    t0 = time.time()
    out = w.ask(src)
    ok = want in out and not re.search(re.escape(bad) + r"(?![஀-௿])", out)
    fails += not ok
    print(f"{'OK  ' if ok else 'FAIL'} {time.time()-t0:4.1f}s  {src!r:45s} -> {out!r}{'' if ok else f'  want={want} bad={bad}'}")
total = len(CASES) + len(COLLOQUIAL)
print()
print(f"{total - fails}/{total} passed")
w.close()
