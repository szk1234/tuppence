# Run Tuppence from source

For contributors and technical users on Windows, macOS or Linux.

1. Install [uv](https://docs.astral.sh/uv/) and Node.js 22 (Node is only needed to build the UI).
2. Get the code:
   ```bash
   git clone https://github.com/szk1234/tuppence && cd tuppence
   ```
3. Build the UI once and start Tuppence:
   ```bash
   uv sync
   npm --prefix web ci && npm --prefix web run build
   uv run tuppence serve
   ```
   It prints a sign-in link. Open it in your browser; the link works until Tuppence restarts,
   and a new one is printed each time you start it.
4. Your data lives in your user data folder (`~/.local/share/Tuppence` on Linux). Set
   `TUPPENCE_DATA_DIR` to keep it somewhere else, for example a test folder:
   `TUPPENCE_DATA_DIR=./try-tuppence uv run tuppence serve`.

To try it without any AI model, upload a CSV from one of the supported banks: importing,
rules, transfers and commitments all work without AI. Categories for anything your rules don't
cover wait until you choose a model in Settings › AI.

Updating: `git pull`, then repeat step 3. Tuppence backs up its database before every
migration (`backups/` in the data folder).
