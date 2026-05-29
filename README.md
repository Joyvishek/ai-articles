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
python .\ai_article_digest.py --config .\digest_config.json --dry-run
```

Send a real email:

```powershell
python .\ai_article_digest.py --config .\digest_config.json
```

## Run Every Day On Windows

Open PowerShell in `D:\Projects\ai-articles`.

After `digest_config.json` is ready, register the scheduled task for 8:00 AM:

```powershell
.\setup_windows_task.ps1 -Time "08:00"
```

If PowerShell says scripts are disabled, use the included `.cmd` launcher:

```powershell
.\setup_windows_task.cmd -Time "08:00"
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
python .\ai_article_digest.py --config .\digest_config.json
```

To delete the scheduled task:

```powershell
Unregister-ScheduledTask -TaskName "Daily AI Article Digest" -Confirm:$false
```
