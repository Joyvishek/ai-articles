# Daily AI Article Digest

This app sends you a daily email with newly published AI articles, short summaries, and links.
It uses RSS/Atom feeds first, then fetches article pages when the feed summary is too short.

## Setup

1. Copy `digest_config.example.json` to `digest_config.json`.
2. Edit `digest_config.json` and set:
   - `email.to` as one email address or a list of addresses
   - `email.from`
   - `smtp.host`
   - `smtp.port`
   - `smtp.username`
   - `smtp.password`
3. For Gmail, use an App Password instead of your normal account password.

Example recipient list:

```json
"email": {
  "to": [
    "joyghosh@yopmail.com"
  ],
  "from": "your_email@gmail.com",
  "subject": "Daily AI Article Digest"
}
```

If `email.to` is an empty list, the scheduled run stops before fetching or sending:

```json
"to": []
```

## Free Email Provider

The lowest-cost option is Gmail SMTP with a free Gmail account and an App Password. It has sending limits, but it is enough for a small personal daily digest. No email provider can be guaranteed free forever, so keep SMTP settings editable in `digest_config.json`.

You can also keep the password out of the config file by setting environment variables:

```powershell
$env:DIGEST_SMTP_HOST = "smtp.gmail.com"
$env:DIGEST_SMTP_USERNAME = "your_email@gmail.com"
$env:DIGEST_SMTP_PASSWORD = "your_app_password"
```

## Test It

```powershell
python .\main.py --config .\digest_config.json --dry-run
```

Send a real email:

```powershell
python .\main.py --config .\digest_config.json
```

If no new AI articles are found, the app exits successfully and does not send an email.

## Run Every Day On Windows

Open PowerShell in `D:\Projects\ai-articles`.

After `digest_config.json` is ready, register the scheduled task for 12:00 PM:

```powershell
.\setup_windows_task.ps1 -Time "12:00"
```

If PowerShell says scripts are disabled, use the included `.cmd` launcher:

```powershell
.\setup_windows_task.cmd -Time "12:00"
```

If Windows says `Access denied`, open PowerShell as Administrator and run the same command again.

To change the daily time later, run setup again with the new time:

```powershell
.\setup_windows_task.cmd -Time "09:30"
```

The app stores already-sent links in `sent_articles.json`, so the same article is not emailed repeatedly.

## Manage The Windows Task

To check if the task exists:

```powershell
Get-ScheduledTask -TaskName "Daily AI Article Digest"
```

To see last run/result details:

```powershell
Get-ScheduledTaskInfo -TaskName "Daily AI Article Digest"
```

To run it manually once:

```powershell
Start-ScheduledTask -TaskName "Daily AI Article Digest"
```

To test the Python command directly:

```powershell
python .\main.py --config .\digest_config.json
```

To delete the scheduled task:

```powershell
Unregister-ScheduledTask -TaskName "Daily AI Article Digest" -Confirm:$false
```

## Run Every Day On Linux Or macOS

Make the helper script executable:

```bash
chmod +x ./run_daily.sh
```

Test it once:

```bash
./run_daily.sh
```

Open cron:

```bash
crontab -e
```

Run every day at 12:00 PM:

```cron
0 12 * * * cd /path/to/ai-articles && /usr/bin/env sh ./run_daily.sh >> ai_articles.log 2>&1
```

Use the full project path on your machine. On PythonAnywhere, use its scheduled task UI instead of cron.

## Docker

Build the image:

```bash
docker build -t ai-articles .
```

Run once with a local `digest_config.json`:

```bash
touch sent_articles.json
docker run --rm -v "$PWD/digest_config.json:/app/digest_config.json:ro" -v "$PWD/sent_articles.json:/app/sent_articles.json" ai-articles
```

With Docker Compose:

```bash
cp .env.example .env
docker compose run --rm ai-articles
```

Docker does not schedule jobs by itself. Schedule the `docker run` or `docker compose run` command with Windows Task Scheduler, cron, GitHub Actions, or your server's scheduler.

## PythonAnywhere

PythonAnywhere can run this as a scheduled task.

1. Upload or clone this project into your PythonAnywhere account.
2. Copy `digest_config.example.json` to `digest_config.json`.
3. Set `email.to`, `email.from`, and SMTP settings.
4. Prefer environment variables for the SMTP password:

```bash
export DIGEST_SMTP_HOST=smtp.gmail.com
export DIGEST_SMTP_USERNAME=your_email@gmail.com
export DIGEST_SMTP_PASSWORD=your_gmail_app_password
```

5. In PythonAnywhere, open **Tasks** and add a daily scheduled task for 12:00 PM.

Example command:

```bash
cd /home/YOUR_USERNAME/ai-articles && python3 main.py --config digest_config.json
```

Free PythonAnywhere accounts may restrict outbound internet access to an allowlist. If a feed or SMTP host is blocked, use a paid account or another scheduler that allows outbound SMTP and RSS requests.
