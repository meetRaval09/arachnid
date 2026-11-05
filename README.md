# 🕷️ Arachnid

**A spider that crawls your project, finds problems, and fixes the ones it can prove are harmless.**

It never changes how your product looks or behaves, not even by one pixel. Anything it is not sure about is left
alone and reported to you, and anything that would change your code waits for a person to approve it.

![The Arachnid dashboard](docs/screenshots/dashboard.png)

---

## What it does

| | |
|---|---|
| **Finds** | broken code, unused imports and variables, typos, wrong brand spellings (`Github` → `GitHub`), grammar that breaks meaning ("their is"), duplicate files, nested folders, missing images, scripts and styles, leaked secrets in URLs, switched-off security checks, insecure `http://` scripts, requests without a timeout, heavy libraries, page weight, memory growth |
| **Fixes** | only what passes the **safety gate**: your project is copied, the change is made on the copy, and a real browser (or your own tests) compares before and after. Any difference means the change is held back. |
| **Asks** | before anything that is a judgement call. Lighter or faster code is offered as a suggestion that waits for your approval. |
| **Reports** | everything in one standard report, sorted as 🔴 alarms → 🟠 decisions → ✅ handled → 💡 ideas. Each item goes to the right person: the owner of that folder and whoever changed it. |
| **Undoes** | every change, byte for byte. |

It works on any language. Python, JavaScript/TypeScript, HTML/CSS, JSON and Markdown get the deepest checks, and the
dependency files of npm, pip, Maven, Gradle, Go, Cargo, Composer, Bundler, NuGet, pub and SwiftPM are understood too.

## Quick start

You need **Python 3.10 or newer**.

```bash
git clone https://github.com/YOUR-GITHUB-NAME/arachnid.git
cd arachnid

# optional, but recommended: the safety gate and the browser checks
pip install -r requirements.txt
python -m playwright install chromium

# open the dashboard for your project
python dashboard.py "C:\path\to\your\project"
```

Or use it straight from the command line:

```bash
python arachnid.py scan  "C:\path\to\your\project"   # look only - changes nothing
python arachnid.py fix   "C:\path\to\your\project"   # fix what is provably safe (backed up first)
python arachnid.py undo                               # put the last fix back (repeat to go further back)
python arachnid.py selftest                           # prove the whole chain works on this computer
```

## The dashboard

`python dashboard.py <project>` opens a control room in your browser. It runs only on your computer, at
`http://127.0.0.1:8765`.

- **Send the crawler out:** Scan, Fix what is safe, Undo, and the extra checks (outside connections, page weight,
  memory growth, lighter code, dead code).
- **What the crawler caught:** every finding is a drop on a spider web. The closer it is to the spider, the more urgent.
- **Live crawl:** the spider walks your file tree to each file at the moment a check opens it. When the job is done it
  climbs back to its web and spins the result into words.
- **Auto-scan:** add or change a file, and a scan starts by itself. Scans only read.
- **Suggestions:** approve or reject them. Approved ones still pass the safety gate.
- **On GitHub:** the project's Arachnid runs. You can see which step a running check is on, and replay a finished run's walk.

![Live crawl](docs/screenshots/live-crawl.png)

## Setup for a team (dashboard → Setup)

A five-step page that connects a repository and writes everything a team needs:

1. **GitHub:** sign in and choose the repository.
2. **Owners:** give folders to teams or people. This becomes `.github/CODEOWNERS`.
3. **Email:** one inbox for everyone or one per team; pick the mail provider; send a test email.
4. **Rules:** who gets alarms, who gets decisions.
5. **Finish:** one pull request with the workflow, CODEOWNERS and settings. The mail password goes into GitHub's
   encrypted secrets.

![Setup](docs/screenshots/setup.png)

## On GitHub, automatically

Copy [`github/arachnid.yml`](github/arachnid.yml) to `.github/workflows/arachnid.yml` in your project, or let the setup
page do it.

- **Pull request into main:** Arachnid checks the change first and writes one comment. A critical problem in a file
  that pull request changes turns the check red. Problems that were already in main are listed, but they do not block
  the merge.
- **Push into main:** a backup check. Alarms open (and later close) one "Arachnid alarm" issue. Safe fixes are offered
  as a pull request, `arachnid/auto-fixes`.
- **Alarms reach the right people:** GitHub @mentions, and team emails through your own mail server. Each alarm is
  sent once, not on every push.

## Promises

- Nothing reaches `main` without a person merging it. Nothing is ever pushed to main.
- No change is applied unless the safety gate proves the product is unchanged. If nothing can be run to prove it, the
  change is held back.
- Suggestions that change code wait for human approval.
- Every change is backed up and can be undone.
- Reports and emails contain file names, line numbers and short descriptions only. They never contain your code, and
  secrets are masked.
- GitHub logins stay in memory. Mail passwords live in GitHub's encrypted secrets, never in a file.

## Tests

```bash
python arachnid.py selftest   # the whole chain: repair, safety gate, alarms, approvals, undo
python qa_accuracy.py         # 15 planted problems must be found, 7 clean look-alikes must stay quiet
python qa_stress.py           # 15 kinds of projects: unicode names, 1500 files, symlink loops, CRLF, ...
```

The same tests run on every push (`.github/workflows/tests.yml`, a copy is in `github/tests.yml`).

## Learn more

[docs/GUIDE.md](docs/GUIDE.md) explains every check, every setting and every command in plain words.

## Licence

MIT, see [LICENSE](LICENSE). The bundled spell checker is
[pyspellchecker](https://github.com/barrust/pyspellchecker) by Tyler Barrus (MIT, see `vendor/LICENSE-pyspellchecker.txt`).
