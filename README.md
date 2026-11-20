# Lehmus Chat

A small local chat interface for the Lehmus AI models (ConfidentialMind Model Gateway).
It lets someone use the models through API keys, without signing into the account owner's
university account.

- Model picker built from the gateway's `/v1/models`. Each model is matched to an API key that is allowed to call it.
- Streaming answers, Markdown and code blocks, Stop, Regenerate and Copy.
- Conversation history saved locally (SQLite), grouped by date and searchable, with rename and delete.
- File attachments (button, drag and drop, or paste):
  - **Images** are sent to vision models (marked 🖼 in the model list).
  - **PDF, DOCX and text/code files** have their text extracted and added to the message.
- Runs only on `127.0.0.1`, so other machines on the network can't reach it.

## How it works

```
Browser  ──►  local server (app/main.py, holds the API keys)  ──►  https://api.<domain>/v1/...
```

The API keys stay in `config.json` on the laptop and are never sent to the browser.
The committed `config.json` contains only **placeholder keys**. Put the real keys in on the
laptop only, and **never commit them** (see below).

## Setup (on the trainee's laptop)

Requirements: **Python 3.10+** and git. On Windows, install Python from python.org and tick
**"Add python.exe to PATH"**.

1. Clone the repo:
   ```
   git clone https://github.com/<you>/custom-lehmus-interface.git
   ```
2. Edit `config.json` in the cloned folder and replace the `cm_api_REPLACE_WITH_…` placeholders with the real keys.
   Then stop git from tracking your local edits, so the keys can't be committed or pushed by accident:
   ```
   git update-index --skip-worktree config.json
   ```
   The file contains:
   - `api_base_url`: from the **API URL** button at the bottom of the ConfidentialMind portal sidebar.
   - `api_keys`: one or more `cm_api_…` keys with an **Invoke** grant on the models.
     Without `"models"`, every chat model the key can access is listed. With `"models": ["id", …]`, only those models are used.
   - Optional: `default_model`, `system_prompt`, `port` (default 8765).
3. Create the desktop icon:
   - **Windows:** right-click `scripts\create-desktop-shortcut.ps1` → *Run with PowerShell*
   - **Linux / macOS:** `./scripts/create-desktop-shortcut.sh`
4. Double-click **Lehmus Chat** on the desktop. The first start installs dependencies (about a minute),
   then the chat opens in the browser. Closing the console window stops the app.

You can also start it manually with `start.bat` (Windows) or `./start.sh` (Linux/macOS).

## Creating the API keys

In the portal, go to **API Keys** and create a key with the **Invoke** action on each Model (or Model Alias)
the trainee should use. Each model page also has a *Create API key* link that pre-fills this.
Give the keys an expiry date and revoke them when the traineeship ends.

## Updating

`git pull` in the folder. Dependencies are reinstalled automatically if `requirements.txt` changed.
Chat history (`data/`) is not touched. Because of `--skip-worktree`, your local `config.json` with the
real keys is kept as well. If `config.json` ever changes in the repo, `git pull` will refuse; run
`git update-index --no-skip-worktree config.json`, back up your keys, and merge by hand.

## Data

- `data/chat.db`: conversations and messages
- `data/uploads/`: uploaded files

Back up the `data/` folder to keep the history. Delete it to start fresh.

## Troubleshooting

| Problem | Fix |
|---|---|
| "No models available" | Check `api_base_url` and the keys in `config.json`, then press ⟳ next to the model list. |
| `HTTP 401` for a key | The key is wrong, expired or revoked. |
| `Gateway error 403` | The key has no Invoke grant for that model. |
| `Gateway error 404` | The model isn't ready or its ID changed. Press ⟳. |
| Port already in use | Change `port` in `config.json`. |
