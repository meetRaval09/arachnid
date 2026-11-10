# Arachnid

A spider that crawls your project, finds problems, and fixes the ones it can fix **without changing how your product looks or behaves - not even by one pixel**. Anything it is not sure about is left alone and reported to you.

## Everyday use (open PowerShell inside the `arachnid` folder)

    python arachnid.py scan       # look only - changes nothing, prints everything it found
    python arachnid.py fix        # fix what is safe
    python arachnid.py undo       # put the last fix back (run again to go further back)
    python arachnid.py selftest   # proves on this computer that the whole chain works

Add a folder at the end to check somewhere else: `python arachnid.py scan "C:\path\to\project"`.
By default it checks the folder that contains `arachnid`.

## What it does (in this order)

1. **Syntax** - finds broken code (missing brackets, quotes, commas...) in Python, JSON, JS/TS, HTML, CSS, Markdown and repairs it only when exactly one repair makes the real parser happy.
2. **Unused code** - finds imports that are never used, variables that are filled and never read, and "ambiguity bugs" (two functions with the same name, a variable called `list` hiding Python's own, a dictionary with the same key twice, a list as a default value ...). Only provably useless things are removed; ambiguity bugs are only reported, because fixing them changes behaviour.
3. **Duplicate files** - merges identical images/assets, keeping the copy closest to the pages that use it, and rewrites the references.
4. **Nested folders** (`main/main/main`) - folds them into the outer folder, moving the inner one to backups.
5. **Spelling** - fixes clear typos in comments, text and names.
6. **Meaning breakers** (`textcheck.py`) - words that are spelled right but wrong: "their is", "your welcome", "should of", a word typed twice, a number that disagrees with its word ("three (4)"), a date that does not exist, placeholder text like "Lorem ipsum" left on a page, a sentence that stops in the middle. Reads `.md`, `.txt`, `.rst` and the visible text of web pages, English only. Every finding comes with a blunt explanation. Only clear-cut fixes in documentation files are applied (`--fix`); text on web pages is part of the product, so it is only reported.

## Alarms: what needs you and what the crawler handles itself

After every `scan` or `fix`, Arachnid sorts what it found into four piles (`python arachnid.py triage` shows the last result again):

- **[ALARM]** - critical: the product is probably broken right now and the crawler cannot repair it (a badly broken file, a page pointing to a missing picture/script/style). Shown loudly, saved to `reports/alarms.json`, sent to Slack/Discord/Teams if you add `--webhook URL` (or set `ARACHNID_WEBHOOK`; only file names, line numbers and one-line descriptions are sent - never code or secrets), and the command ends with exit code 2 so an automatic pipeline turns red.
- **[ATTENTION]** - needs your decision but is not an emergency (a fix the safety gate held back, a likely bug, a choice only you can make). Nothing was touched.
- **[HANDLED]** - the crawler fixed it or can fix it itself. Reported only.
- **[IDEAS]** - optional suggestions.

## Suggestions (never changes anything)

    python arachnid.py insights

Looks for possible memory leaks (things opened and never closed), code and files that are heavy on memory, functions nobody mentions, lines that can never run, files nobody uses, libraries you declared but do not use, and heavy libraries that have lighter options. Works for any language: patterns are known for JavaScript/TypeScript (React, Three.js/WebGL), Python, Java, C/C++, C#, Go, Rust, PHP and Ruby; the language-neutral checks work for every text-based language. Each suggestion says how sure it is (high / medium / low). Reading code cannot PROVE a leak - only running and measuring can.

## The safety gate

Steps 1-4 (and optionally 4 with `--gate-spelling`) must pass the gate before anything is written: the project is copied, the change is made on the copy, and a hidden browser compares before/after (files loaded, page HTML, element positions, console errors, pixels at desktop and phone size). If anything differs, the change is **held back for you**.
Some things are always yours to decide: changes that would create new public web addresses, files referenced by dynamically built paths, code that is badly broken with no healthy copy.

## Safety promises

- Nothing is deleted - removed things go to `arachnid/backups/<time>/`.
- Every fix can be undone.
- Secrets are never read into reports.

## Needs

Python 3.10+. For the gate: `pip install playwright pillow` and `playwright install chromium`.
Single tools (`spellcheck.py`, `syntaxcheck.py`, `logiccheck.py`, `dedupe.py`, `clones.py`, `gate.py`, `crawler.py`) still work on their own with `--help`.


## Terminology (terms.py)
Scans every text area (docs, visible page text, code comments), senses what needs fixing, and improves terms without changing meaning:
official spellings (javascript -> JavaScript, Github -> GitHub), one name for one thing (e-mail/email, by your project's majority), one English
(British/American, by majority). Only spelling/capitalisation changes. Code, links, `backticks`, names and page text are never changed
(page text and unprovable code comments are report-only). Plainer-wording ideas are suggestions only.
Run: `python terms.py` (look), `python terms.py --fix`, `python terms.py --undo`. Teach it your terms in `glossary.txt`.


## Advisor, connections and approvals (Steps 10-11)
- `python arachnid.py advise` - lighter frameworks/libraries (React -> Preact, Electron -> lighter shell, Firebase modular, headless OpenCV ...) and code that is slow or heavy
  as data grows (list searched inside loops, nested matching loops, repeated copying, repeated recursion). Each idea states time/memory before -> after, the risk and a confidence level.
- `python arachnid.py netcheck [--online]` - maps every outside connection and finds secrets in addresses, switched-off certificate checks, http scripts, CDN files
  without integrity hash or fixed version, calls without timeout/error handling, localhost/IPs left in code, risky CORS, unchecked downloads in build files, trackers;
  `--online` also finds dead links, bad/expiring certificates, slow answers.
- **Nothing is changed until a human approves.** `python arachnid.py proposals` lists everything waiting; `approve <id>|all`, `reject <id>`, then `apply-approved`.
  Only exact mechanical edits can be applied (list -> set, list -> generator, add a timeout); each goes through the safety gate and is backed up (`undo` restores it).
  Bigger ideas (new framework, redesign) are explained for a human to do.


## Measuring (Steps 12-13)
- `python arachnid.py size [--build] [--online]` - weighs what each page really downloads (raw and compressed), by type, names the big libraries (with source maps: which package
  takes how much), and MEASURES savings: images re-encoded as WebP in memory, un-minified scripts/styles minified in memory, old font formats. Limits: `--budget-js-kb`, `--budget-total-kb`.
- `python arachnid.py leaks [--build]` - opens each page in a hidden browser (outside internet blocked), uses it for many rounds (scroll, resize, safe clicks; your own clicks via `leakcheck.py --steps steps.json`),
  cleans up memory after every round, and reports LEAK / suspicious / stable from the measured growth of memory, page elements and event listeners; points at suspicious code from the code reader.
  Not part of `scan` because it takes about 1-3 seconds per page.

## Exact edits (apply only after approval + safety gate)
list -> set (membership only), `sum([...])` -> generator, missing `timeout=30` on requests, `r = r + [x]` in a loop -> `append`/`extend`, `sorted(xs)[0]` / `[-1]` -> `min` / `max`.
`python arachnid.py netcheck --online` also builds paste-ready CDN tags (exact version + integrity hash + crossorigin) - these are shown to you, not applied automatically,
because they change the page's HTML and the safety gate (which demands an identical page) would hold them back.


## Dependency files of many languages (deps.py)
Arachnid reads the library lists of Java (pom.xml, Gradle), Go (go.mod), Rust (Cargo.toml), PHP (composer.json), Ruby (Gemfile), C# (.csproj, packages.config),
Dart (pubspec.yaml), Swift (Package.swift) and Python (requirements.txt, pyproject.toml, Pipfile) - besides package.json. For each it reports libraries declared but never used,
built-in / maintained replacements (e.g. Joda-Time -> java.time, Newtonsoft.Json -> System.Text.Json, lazy_static -> LazyLock, log4j 1.x -> a supported logger),
heavy settings (tokio "full"), unpinned versions (LATEST, `*`, `any`, Git without a commit) and packages fetched over plain http. Everything is a suggestion (`advise`, `netcheck`, `insights`); nothing is changed.

## Self-test of the whole crawler (qa_stress.py)

`python qa_stress.py` builds 15 throwaway practice projects in a temporary folder and runs the full crawler on each one:
a static website, a Python library with tests, a front-end with CDN tags, dependency files in seven languages, an empty
folder, binary files only, unicode and spaces in names, CRLF/BOM/latin-1 files, 1500 files, deep folders with a symlink
loop, markdown with code blocks, valid modern code, an already-clean project, odd command-line input, and two scans at once.

For every project it checks that `scan` changes nothing, that `fix` settles, that valid code is never touched, that line
endings and markdown code blocks survive, that the project's own tests still pass, that every page looks and behaves the
same in a real browser (desktop and phone) as before, and that `undo` restores the original byte for byte. It also checks speed.
Run one round with `--round 7`, list them with `--list`. Run only one copy at a time, because `undo` is shared.

## Accuracy test (qa_accuracy.py)

`python qa_accuracy.py` hides 15 known problems in a practice project (typos, grammar, brand spelling, broken Python and
JavaScript, a missing stylesheet, an http script, duplicate files, an unused import, a function defined twice, a secret
key in a URL, certificate checks switched off, a request without a timeout, a slow lookup list) next to 7 clean
look-alike files. It scores how many problems were FOUND (goal: 30 of 30) and how many FALSE complaints were made about
the clean files (goal: 0). Add `--esbuild <path to node_modules>` to test the stronger JavaScript reader too.

## Automatic checks on GitHub (github/arachnid.yml)

Copy `github/arachnid.yml` into your project as `.github/workflows/arachnid.yml`. Either keep the `arachnid` folder
inside your project, or change `ARACHNID_REPO` at the top of the file to the GitHub address Arachnid is published at.

* **Pull request into main.** Arachnid scans the change before it enters main, without changing anything. It writes one
  comment on the pull request (updated on every push) with clickable file:line links. A critical problem turns the check red.
* **Push into main.** It scans main again as a backup guard. An alarm turns the run red, and GitHub e-mails the person who
  pushed. Then it tries the safe fixes. Anything that passes the safety gate is offered as the pull request
  `arachnid/auto-fixes`. A person decides whether to merge it.
* It never pushes to main, never merges, never approves, and stores no passwords (it uses GitHub's temporary run key).
* One-time setting for the fix pull requests: Settings -> Actions -> General -> tick "Allow GitHub Actions to create and
  approve pull requests". Optional: add a secret `ARACHNID_WEBHOOK` to receive alarms in Slack/Discord/Teams.
* `ci_summary.py` builds the comment from `reports/ledger.jsonl`. Full reports are attached to every run as a download.

## Who hears about what: one report, routed to the right people

Every message (pull request comment, alarm issue, e-mail) is built from one file, `reports/arachnid-report.json`
(`report.py`). It always has the same order: alarms, then decisions, then what was handled, then ideas.

**Routing** (`routing.py`):
* Owners come from GitHub's standard `.github/CODEOWNERS` file (example: `github/CODEOWNERS.example`).
* The person who changed the code is the pull request's author for files that pull request changes. Otherwise it comes
  from `git blame`.
* Who gets pinged for each type of finding:
  * alarm: the owners, plus the person who made the change, plus any "always" receivers (e.g. security)
  * decision: the person who made the change
  * handled and ideas: nobody is pinged, they are only listed
* In a pull request, only alarms in files that pull request changes turn the check red. Problems that were already in
  main are listed without pinging anyone, and are tracked in one "Arachnid alarm" issue. That issue opens, updates and
  closes by itself.

**Way 1, GitHub:** the @names in the comment or issue make GitHub notify those people (in the app and by e-mail).
No setup is needed.

**Way 2, the firm's own e-mail** (`notify.py`):
* Each team's group address (from `arachnid.config.json`, example in `github/arachnid.config.example.json`) gets ONE
  e-mail with only its own findings.
* The subject is always built the same way, so mail rules can sort it into folders:
  `[Arachnid] [ALARM] [payments-team] api.py:5 - ...`
* Only new alarms and decisions are e-mailed. The same alarm is not sent again on every push.
* The mail server login is read from the GitHub secrets `ARACHNID_SMTP_HOST`, `_PORT`, `_USER`, `_PASSWORD` and
  `ARACHNID_MAIL_FROM`. It is never stored.
* Without a mail server, `python notify.py email` saves the e-mails to `reports/outbox/` instead.
* `python notify.py preview` shows who would get what.

## Dashboard (dashboard.py)

`python dashboard.py "C:\path\to\your\project"` opens a control room in your browser (only on this computer, at
http://127.0.0.1:8765). Press Ctrl+C in the window to stop it.

* **Send the crawler out:** the Scan, Fix what is safe and Undo buttons, plus the extra checks. These are the same
  commands you would type. Fix still goes through the safety gate, and suggestions still need your approval.
* **What the crawler caught:** every finding is shown as a drop on a spider web. Alarms sit closest to the spider.
  Click a drop to jump to the finding.
* **Live crawl:** the spider walks through your project's file tree to each file at the moment a check opens it.
  Updates come over a WebSocket (a connection that stays open so the page gets news instantly). If WebSocket is
  blocked, the page checks for news once a second instead.
* **Approvals:** tick the suggestions you want, then press Approve and Apply.
* **Watching (on by default):** the dashboard looks at your project every 2 seconds. When files are added or
  changed, it waits until they stop changing (so a half-saved file or a copy in progress is not scanned too early),
  then starts a scan by itself. The new files are marked "new" or "changed" in the live crawl. A scan only reads, so
  it needs no permission. Fix, apply and undo still always wait for you.
  * Arachnid's own fixes and undos do not count as new work.
  * Editor scratch files (`~`, `.swp`, `.tmp`, `.#...`) are ignored.
  * Many files at once (for example a `git pull`) give one scan, not many.
  * Turn it off with the switch on the page, or start the dashboard with `--no-watch`.
* **On GitHub:** the Arachnid runs of this project. A running check shows which step it is on (checked every few
  seconds). A finished run can replay the spider's walk through every file it read.
  * Reading private repositories and downloading a run's trail needs a GitHub login: run `gh auth login`, or set
    `GITHUB_TOKEN`. The login is kept in memory only and never sent to the page.
  * For GitHub Enterprise, set `ARACHNID_GITHUB_API=https://<your-host>/api/v3`.

How the live trail works: `live/sitecustomize.py` uses Python's built-in "audit hook" (a listening point Python
offers) to note each file a check opens. It writes only file names, never contents, to
`reports/live-events.jsonl`. It is switched on only when the dashboard or the GitHub workflow starts a check.
Tested: scan results are identical with and without it.

Safety: the page only answers to this computer. Every button needs a secret key that is made fresh each time the
dashboard starts. Only one job runs at a time.

## Setup page (dashboard → Setup)

Open the dashboard, then press **Setup** (or go to http://127.0.0.1:8765/setup). Five steps:

1. **GitHub.** Sign in, then choose the repository. There are three ways to sign in:
   * the GitHub command-line tool's login (`gh auth login`),
   * GitHub's own sign-in screen with a short code (needs `ARACHNID_GITHUB_CLIENT_ID`, the public id of a GitHub OAuth
     app with "device flow" switched on),
   * a token (the page links to GitHub's token page with the right permissions ticked: `repo`, `workflow`, `read:org`).
2. **Teams and owners.** The organisation's teams and the repository's people are read from GitHub. Give folders
   (or patterns like `*.md`) to them; this becomes `.github/CODEOWNERS`.
3. **Email.** Choose **one address for everyone** (one e-mail per team, all to that inbox; the subject says which team)
   or **one address per team**, with optional addresses for people. Pick the mail provider (Outlook, Gmail, Zoho, Other)
   and send a test e-mail.
4. **Who gets what.** Choose who receives alarms and who receives decisions, any "always" receivers, and whether
   Arachnid's automation may open fix pull requests.
5. **Review and finish.** Preview the files, then **Create pull request**. That one pull request (branch
   `arachnid/setup`) contains `.github/workflows/arachnid.yml`, `.github/CODEOWNERS` and `arachnid.config.json`.
   Nothing reaches main until a person merges it.

The mail settings go into GitHub's encrypted secrets. Saving them automatically needs the small `PyNaCl` library
(`pip install pynacl`). Without it, the page lists the secrets with a link to paste them on GitHub yourself.

The GitHub login is kept in memory only and never written to a file. `arachnid.config.json` never contains a password.
