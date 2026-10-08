# The unsigned desktop builds

The developer preview's desktop apps aren't code-signed yet (signing arrives with v1.0), so
your system will warn you. Only download them from this repository's Releases page.

**Check the download first.** Each release lists the files' SHA-256 checksums. Compare:
- Windows (PowerShell): `Get-FileHash .\Tuppence-v0.2.0-dev.1-windows.zip`
- macOS / Linux: `shasum -a 256 Tuppence-v0.2.0-dev.1-*`

**Windows:** unzip, open the `Tuppence` folder and run `Tuppence.exe`. If SmartScreen says
"Windows protected your PC", choose **More info → Run anyway**.

**macOS:** unzip and move `Tuppence.app` to Applications. The first time, right-click (or
Control-click) the app and choose **Open**, then **Open** again. If macOS still refuses, go to
System Settings › Privacy & Security and choose **Open Anyway**.

**Linux:** `tar xzf Tuppence-v0.2.0-dev.1-linux.tar.gz && ./Tuppence/Tuppence`. The window
needs GTK and WebKitGTK (`gir1.2-webkit2-4.1` on Debian and Ubuntu); without them Tuppence opens
in your browser instead.

The desktop app listens only on your own computer (127.0.0.1) and signs you in with a
one-time link, so nobody else on your network can reach it.
