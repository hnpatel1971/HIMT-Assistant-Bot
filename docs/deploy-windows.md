# Deploying the HIMT Assistant Bot on Windows Server 2022 with IIS

The bot runs on the same Windows server as HIMT-LMS. A Python service on port
8001 serves both the API and the chat widget, and MongoDB runs on the same
server. IIS forwards the bot's requests to port 8001. Without a domain, the bot
gets its own port on the server's IP address (`http://<server IP>:8081/`). With
a domain, it gets its own host name on ports 80/443 (for example
`chat.himtmarine.com`). Either way, every other request still goes to the LMS
site. Run the commands in
**PowerShell as Administrator**.

This setup has not yet been tested end to end on Windows. Report anything that
fails at a step.

## Before you start

- Optional: a domain name for the bot. You can install and try the bot without
  one. You need one to embed it on an `https://` website (see below).
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
Replace `FILL_ME` in `ANTHROPIC_API_KEY`, add the values from Emergent, save,
and run the same command again. Leave `BOT_HOSTNAME` empty if you have no
domain; the bot is then served at port `BOT_PORT` (8081).

The second run:

1. clones this repository to `C:\himt-assistant-bot`
2. builds the chat widget (`frontend\build`)
3. installs the backend into `backend\.venv`
4. starts the `HIMT-Assistant-Bot` Windows service, which starts after MongoDB
5. adds an IIS site, `himt-assistant-bot`, on port `BOT_PORT`, or on port 80
   for `BOT_HOSTNAME` when one is set
6. opens that port in Windows Firewall. If the server is in a cloud (Azure,
   AWS, etc.), also allow the port in the provider's network settings.

The LMS site, its service and its settings are not changed.

Check it from the server:

```powershell
Invoke-WebRequest http://127.0.0.1:8001/api/health
```

## Website content

On its first start the bot crawls the HIMT websites by itself, which takes a
few minutes. After that it re-crawls every day at `DAILY_REINGEST_HOUR` (UTC)
and refreshes himt.co.in every 6 hours.

Then open `http://<server IP>:8081/` in a browser to try the bot.

## Adding a domain and HTTPS (needed for embedding)

Browsers block an `http://` widget on an `https://` page, so embedding on
himtmarine.com needs the bot on `https://`, and certificates are issued for
names, not bare IP addresses. The simplest name is a subdomain of the site you
already have. Whoever manages himtmarine.com's DNS adds an A record such as
`chat` pointing at the server's IP, which costs nothing. Then set
`BOT_HOSTNAME=chat.himtmarine.com` in the settings file and run `install.ps1`
again.

Then use win-acme (win-acme.com) to issue a free Let's Encrypt certificate for
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
