# EVE Fleet Management Tool

A Windows desktop tool for fleet organisers in EVE Online. Build a library of fittings, roles and doctrines, assign pilots to roles, and check each pilot's actual hangar against what their role needs.

- **Characters:** log in one or more characters with EVE's official login (EVE SSO) and pull their assets, clones and implants, and their corporation's hangars when they're a Director.
- **Fittings:** paste fits in EFT format, then rename, edit or delete them. Capitals get extra requirements: fuel, fleet hangar stock, carried ships, fighters pre-loaded in tubes, the escape frigate. A Capsule fitting with implants is an implant set.
- **Library:** roles (fittings needed at a location) and doctrines (a set of roles), with characters assigned to roles.
- **Ships:** every ship a character or corporation holds, by place and hangar. Give each ship the fitting it's meant to fly and an owner; see how it compares in an audit view and an EFT view.
- **Doctrines:** every assigned pilot's ships, checked module by module against their role. A shopping list is built from what's missing, using what's already in the pilot's hangars first.
- **Doctrine packages:** export a doctrine library to a file and import it on another machine, so a whole corp can share one.

**Help → User Guide** (F1) explains the workflows and how the tabs connect.

## What's new in 1.0.0-rc3

**Before your first audit, assign your ships.** The Doctrines tab now checks only ships that have a fitting assigned: open the **Ships** tab, select a ship (or several of one hull), choose its fitting and press **Assign Fitting**. A requirement with an unassigned hull at its place shows ⚪ *not checked*; right-click it → **Assign the Fitting to** to assign from the audit.

**Add your characters again.** RC3 asks for new permissions (clones and implants, and corporation hangars for Directors): use **Characters → Add Character** once for each character. Your library and settings are kept.

**The app rebuilds its game database once** (about 100 MB from CCP) at the first start after upgrading, because fuel limits need item volumes it didn't store before. Accept when asked.

New since RC2:

- **Ships tab:** every ship by place and hangar (corporation divisions by name), ships carried inside capitals nested under them. Assign fittings and owners; an assignment follows the ship through trades and is remembered for 30 days after it was last seen. Owners decide whose requirements a ship serves; a corporation's ship doesn't count for a pilot.
- **Audit view and EFT view** of a ship, with a Current / Expected switch: what's in place, in the wrong place, missing or to take off.
- **Audits:** ships at home, away or missing; Fit / Stow / Remove refit wording; loaded ammo counts as cargo; mutated (Abyssal) modules judged by their base module.
- **Implant sets** checked against jump clones and the active clone.
- **Corporation hangars,** read by a Director and shown like a character's.
- **Capitals:** fuel amounts with sliders capped by the bay; maintenance bay entries can name a saved fitting; fighter squadrons pre-loaded in tubes.
- **Shopping list:** what's already in the pilot's hangar in the same system is listed under *Pull from stock*, the rest under *Buy*; **Copy Multibuy** copies only what to buy.
- A **Light** theme, a searchable Library with requirements grouped by system, alphabetical character lists, the **User Guide**, and a program built with its own start-up code and version information (fewer antivirus false alarms).

> **Pre-release.** This is an early build for a small group of testers. Expect rough edges, and please report them (see [Reporting problems](#reporting-problems)).

## Install

Download from the [Releases](../../releases) page. Each release has:

| File | What |
|---|---|
| `EveFleetManagementTool-<version>-setup.exe` | The installer. Installs for your Windows user only (no admin rights) and adds a Start menu shortcut. |
| `EveFleetManagementTool-<version>-portable.zip` | The same app without an installer. Unzip anywhere you can write to and run `EveFleetManagementTool.exe`. |
| `SHA256SUMS.txt` | Checksums of the two files above. |

**Windows SmartScreen will warn you** ("Windows protected your PC"), because the program isn't code-signed. Code-signing certificates are expensive for a free fan project. Click **More info → Run anyway**. If you'd rather check first, compare the checksum (below), or read the source in this repo. Every release is built from it by the [Release workflow](.github/workflows/release.yml), and you can see the build log on the Actions tab.

**On first run** the app downloads CCP's static game data (about 100 MB) and builds its item database from it. This takes a minute or two. **Tools → Check for DB Update** fetches a newer one after an EVE patch.

**Upgrading:** run the new installer over the old one. Your characters, library and settings are kept. With the portable zip, copy your old `data` folder into the new one.

### Checking the download

In PowerShell, in the folder you downloaded to:

```powershell
Get-FileHash .\EveFleetManagementTool-1.0.0-setup.exe
```

The hash it prints must match the line for that file in `SHA256SUMS.txt` (the case of the letters doesn't matter).

## What it accesses, and where your data goes

When you add a character, you log in on CCP's own login page, in your browser. The app never sees your password. It asks for these permissions (ESI scopes):

| Scope | Why |
|---|---|
| `esi-assets.read_assets.v1` | Read the character's assets, to see which ships and modules they have and where. |
| `esi-universe.read_structures.v1` | Look up the names of player structures your assets are in. |
| `esi-clones.read_implants.v1` | Read the implants in the character's active clone, to check them against doctrines. |
| `esi-clones.read_clones.v1` | Read the character's jump clones (where they are and their implants), for the same check. |
| `esi-characters.read_corporation_roles.v1` | See whether the character is a Director of their corporation, so the app knows who can read its hangars. |
| `esi-assets.read_corporation_assets.v1` | Read the corporation's hangars, to find ships held by the corporation. Only works for a Director; for anyone else it's never used. |
| `esi-corporations.read_divisions.v1` | Read the names of the corporation's hangar divisions. Director only, like the hangars. |

That's all. It can't change anything in game and can't see your wallet or mail. Corporation hangars are only read for a character who is a Director.

**Everything stays on your PC.** The app talks only to CCP: the login server, ESI, and CCP's static data download. There's no server of ours, no analytics and no telemetry. Everything it stores is in the `data` folder next to the program (for the installer: `%LOCALAPPDATA%\Programs\EVE Fleet Management Tool\data`):

| Folder | Holds |
|---|---|
| `data\auth` | Login tokens, one file per character. **They are stored unencrypted**, so don't share this folder. Anyone with a copy of these files can read that character's assets until you revoke the app's access at [community.eveonline.com](https://community.eveonline.com/support/third-party-applications/). **Characters → Remove Character…** deletes the tokens from this PC. |
| `data\raw`, `data\generated` | Pulled assets, your fittings, roles and doctrines. |
| `data\logs` | Logs of the last three sessions. They don't contain login tokens. |
| `data\eve.db` | CCP's static game data. |

The login briefly runs a small web server on your PC at `http://localhost:8080/` to receive the login result from your browser. If another program already uses port 8080, adding a character will fail; close that program and try again.

## Reporting problems

Include:

1. The version (it's in the window title).
2. What you did, what you expected and what happened.
3. The log: **Debug → Export Logs** saves it to a file you can send. It contains no login tokens, but it can mention your character and structure names, so read it first if that matters to you.

## Building it yourself

You need Windows and Python 3.14. [Inno Setup 6](https://jrsoftware.org/isinfo.php) is only needed for the installer.

```powershell
python -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r requirements-dev.txt
.\.venv\Scripts\python.exe run_gui.py                 # run from source
.\.venv\Scripts\python.exe tools\build_release.py     # build the zip, installer and checksums
```

## Licence and credits

The source is under the [MIT licence](LICENSE).

EVE Online and all related names, images and data are trademarks or property of CCP hf. This is an unofficial fan project, not affiliated with or endorsed by CCP hf. Game data comes from CCP's Static Data Export and the EVE Swagger Interface (ESI).
