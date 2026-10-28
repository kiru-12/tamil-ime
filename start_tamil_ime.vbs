' Launches the Tamil IME hidden (no console window).
' To run it at every login, copy this file into your Startup folder:
'   Win+R  ->  shell:startup  ->  paste this file there.
Set sh = CreateObject("WScript.Shell")
sh.Run """C:\Users\sk6\.local\bin\uvw.exe"" run --project ""C:\Users\sk6\Downloads\tamil-ime"" ""C:\Users\sk6\Downloads\tamil-ime\tamil_ime.py""", 0, False
