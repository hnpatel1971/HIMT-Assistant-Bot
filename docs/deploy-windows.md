# Deploying the HIMT Assistant Bot on Windows Server 2022 with IIS

The bot runs on the same Windows server as HIMT-LMS. A Python service on port
8001 serves both the API and the chat widget, and MongoDB runs on the same
server. IIS answers requests for the bot's own host name (for example
`chat.himtmarine.com`), handles HTTPS, and forwards those requests to port 8001.
Every other request still goes to the LMS site. Run the commands in
**PowerShell as Administrator**.

This setup has not yet been tested end to end on Windows. Report anything that
fails at a step.

## Before you start

- Pick the bot's address, for example `chat.himtmarine.com`, and point its DNS
  record (an A record) at the server's public IP.
- Get a Claude API key from https://console.anthropic.com/settings/keys.
- From Emergent's environment settings for the bot, note any values you want to
  keep: `HIMT_ADDITIONAL_SITES`, `RESEND_API_KEY`, `RESEND_FROM_EMAIL`,
  `WEAK_ANSWER_ALERT_TO`.

## Install

Copy `deploy\windows\install.ps1` to the server and run:

```powershell
powershell -ExecutionPolicy Bypass -File C:\Users\Administrator\Downloads\install.ps1
```

The first run installs Git, Python 3.12, Node.js, MongoDB, NSSM and the IIS
proxy modules. Any of them that the LMS already installed are left as they are.
It then opens `C:\himt-assistant-bot-config\himt-assistant-bot.env` in Notepad.
Replace every `FILL_ME` (`BOT_HOSTNAME` and `ANTHROPIC_API_KEY`), add the values
from Emergent, save, and run the same command again.

The second run:

1. clones this repository to `C:\himt-assistant-bot`
2. builds the chat widget (`frontend\build`)
3. installs the backend into `backend\.venv`
4. starts the `HIMT-Assistant-Bot` Windows service, which starts after MongoDB
5. adds an IIS site, `himt-assistant-bot`, bound to `BOT_HOSTNAME` on port 80

The LMS site, its service and its settings are not changed.

Check it from the server:

```powershell
Invoke-WebRequest http://127.0.0.1:8001/api/health
```

## Load the website content

The database starts empty. The bot re-crawls the HIMT websites every day at
`DAILY_REINGEST_HOUR` (UTC). To fill it straight away, trigger a crawl with the
`ADMIN_TOKEN` from the settings file:

```powershell
Invoke-WebRequest -Method Post -Headers @{ "X-Admin-Token" = "<ADMIN_TOKEN>" } http://127.0.0.1:8001/api/admin/reingest
```

## HTTPS

Use win-acme (win-acme.com) to issue a free Let's Encrypt certificate for
`BOT_HOSTNAME` and add the HTTPS binding to the `himt-assistant-bot` site.
Choose the site when win-acme asks, so it picks up the host name.

## Embed on the website

Before `</body>` on the HIMT website pages, add:

```html
<script src="https://chat.himtmarine.com/embed.js" async></script>
```

Use your own `BOT_HOSTNAME` in that address. Remove the old
`preview.emergentagent.com` script tag.

## Updating

Run `install.ps1` again. It pulls the latest `main`, rebuilds, and restarts
the service. Settings changes in the `.env` file also take effect on a re-run.

Logs are in `C:\himt-assistant-bot\logs\service.log`.
