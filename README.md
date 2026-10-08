# EVE Fleet Management Tool

A Windows desktop tool for fleet organisers in EVE Online. Build a library of fittings, roles and doctrines, assign pilots to roles, and check each pilot's actual hangar against what their role needs.

- **Characters:** log in one or more characters with EVE's official login (EVE SSO) and pull their assets, clones and implants, and their corporation's hangars when they're a Director.
- **Fittings:** paste fits in EFT format, then rename, edit or delete them. Capitals get extra requirements: fuel, fleet hangar stock, carried ships, fighters pre-loaded in tubes, the escape frigate. A Capsule fitting with implants is an implant set.
- **Library:** roles (fittings needed at a location) and doctrines (a set of roles), with characters assigned to roles.
- **Ships:** every ship a character or corporation holds, by place and hangar. Give each ship the fitting it's meant to fly and an owner; see how it compares in an audit view and an EFT view.
- **Doctrines:** every assigned pilot's ships, checked module by module against their role. A shopping list is built from what's missing, using what's already in the pilot's hangars first.
- **Doctrine packages:** export a doctrine library to a file and import it on another machine, so a whole corp can share one.
- **ESI features** (each can be turned off in Options): skill checks, hub prices, alliance contracts for missing ships, opening the market or a route in your game client, syncing fittings with the game, and noticing lost ships.

**Help → User Guide** (F1) explains the workflows and how the tabs connect.

## What's new in 1.7.0-rc7

**Log in again:** one more permission, `esi-location.read_ship_type.v1` (see [What it accesses](#what-it-accesses-and-where-your-data-goes)). After installing, use **Characters ▸ Add Character** for each character. Until you do, the ship that character is sitting in is left out, as before; everything else works. Install over RC6, and your library, ships and settings are kept.

- **The ship you're sitting in:** EVE's asset list leaves out the ship a character is in (a Titan that never docks, a cyno alt logged off in space). Each pull now reads it and adds it where it is: a station, a structure, or in space ("(in space)" in the Ships tab).
- **Orphaned items:** an item ESI lists inside a ship or container that isn't itself in the list (old rigs, say) is left out of the assets and logged, instead of showing up on its own.
- **Unknown structures:** a ship missing from the assets is no longer taken for a structure and offered for naming.
- **Options:** the character list can be selected in every theme; right-click ▸ **Remove Character…** (or Delete) removes one.
- **Ships tab:** the **Owner** list offers every linked character's corporation, not only the ones a Director pulls.

## What's new in 1.6.0-rc6

**Version numbers:** from this release the RC number is the minor version, so RC6 is **1.6.0**. RC1 to RC5 were all 1.0.0. **Help ▸ About** shows the version and when it was built.

**Log in again:** this release asks for more permissions (see [What it accesses](#what-it-accesses-and-where-your-data-goes)). After installing, use **Characters ▸ Add Character** for each character. Until you do, the new features skip that character and say "log in again"; everything else works as before. There's no database rebuild: install over RC5, and your library, ships and settings are kept.

**Tranquility's status:**
- **A lamp** on the right of the tab row shows whether Tranquility is up. A red dot on it means CCP has posted an incident or maintenance; hover to read it.
- **While the server's down** (including daily downtime), pulls and logins wait. Auto Pull tries again quietly every 5 minutes.
- **A pull that keeps failing** goes into a short safe mode instead of hammering ESI. A character that still fails is skipped, and the rest are pulled.

**ESI features: Options ▸ ESI Features.** Each can be switched off, and one that's off makes no calls. Losses and public contracts start off; the rest start on.
- **Skill check:** the audit says when a pilot can't fly their fit ("Can't fly: Carriers V (has IV)") and **Copy Skill Plan** copies the missing levels for the game's skill planner. In the Ships tab, right-click a ship ▸ **Can <pilot> Fly This?**.
- **Prices:** the shopping list is priced at Jita 4-4 (or another hub), from real sell orders, with a total in Fleet Totals.
- **Contracts:**
  - **Your stock:** your own item-exchange contracts count as stock.
  - **Offers:** a missing ship is offered alliance and corporation contracts in its system, as an exact fit or a near fit (90%, with what's missing).
  - **Use This Contract:** holds the purchase for 2 hours while you go and get it.
  - **Capitals:** a missing capital is offered contracts within one jump. **Find a Hull…** searches up to 5 jumps, and public contracts too if you turn them on (slow the first time).
- **Open in the game client:** from the shopping list:
  - **Set Destination** and **Add Waypoint:** route to where your stock is.
  - **Show in Market:** the item in the game's market window.
  - **Mail Shopping List:** an in-game mail you send yourself.
  - **Open in Game:** a contract.

  The app checks the character is logged in to the game first.
- **Fitting sync with the game:**
  - **Fittings ▸ Import from Game…:** bring in the fits you've saved in game.
  - **Save Fits to Game…:** right-click a pilot in the audit to save their doctrine fits to their in-game fittings. When you change a fitting later, the app offers to update their copy.
  - **Fittings ▸ Deleted from Game…:** keeps each fit the app deletes in game for 14 days, with **Restore**.
- **Losses** (off unless you turn it on):
  - **After a pull,** the app reads recent killmails. When an assigned ship of that hull has disappeared, it asks whether that was the one lost. It never decides by itself.
  - **A lost ship** shows as **Lost: replace** until a replacement turns up.
  - **Options under it:** **Copy SRP Items** (the hull, then what was destroyed and dropped), an insurance estimate, and corporation losses for Directors.

**Smaller changes:**
- **Ships tab:**
  - **Implants** tab: each clone, where it is, its implants by slot, and which of your implant sets it carries.
  - **Audit pane:** right-click ▸ **Copy Missing Items** and **Show in Market**.
- **Library:** the Fit box no longer repeats the hull, and the Fit and Station boxes are wider.
- **Fittings tab:** the list starts collapsed.
- **Options:** the panels sit in two columns.
- **Right-click menus:** they list only what applies to the line you clicked, with no greyed-out entries.

## What's new in 1.0.0-rc5

No new permissions and no database rebuild: install over RC4. Your library, ships and settings are kept.

- **Hard and soft requirements.** Tick **Soft requirement** when adding one in the Library (or right-click it ▸ **Make Soft**). A soft requirement is checked the same way but only warns: the pilot stays ready, and **Add Every Missing Item** leaves its ships out. A hard requirement for a whole system (or any system) makes the role's requirements for the same fitting inside it soft, so one ship can serve both. When a doctrine package update changes requirements you made hard or soft, you're asked whether to keep yours.
- **Coloured status icons.** Ready, warning and failing lines have coloured icons in every theme, and failing lines are tinted: red for a hard failure (something to buy), yellow for a soft one (something to move).
- **Homes.** Each ship has a **Home** system (or **Anywhere**, for supers and titans that live in space), set in the Ships tab and filled in when you assign a fitting. A ship serves its Home system's requirements; elsewhere in that system it's *move it to the station*, away from it it's *deployed: bring it back*. Both are soft failures, never a purchase. Ships assigned before this update have no Home and audit as before until you give them one (right-click ▸ **Make <system> Its Home**).
- **<Personal>** at the top of the Ships tab's Fitting list: for ships that aren't doctrine ships. The audit never looks at them.
- **Before suggesting a replacement,** the audit looks for a ship with no fitting in the same system, in case you've already bought one.
- **Doctrines ▸ By System:** audit one system's requirements for every pilot, then **Onboard Ships Here** (new ships get their fitting and Home in one go) or **Adopt Ships Here** (ships moved for a deployment take this system as Home, with a warning if that leaves another system short). Both show a preview first.
- **Fittings tab:** a search box above the list (ship class, hull or fitting name).
- **Ships tab:** opens with the ship list at three-quarters of the width, and its columns sized to their text.

## What's new in 1.0.0-rc4

A small update to RC3: no new permissions and no database rebuild, so just install over RC3.

- **Duplicate fits are caught.** Importing or saving a fit that's identical to one you already have (same hull, modules, drones, fighters and cargo) asks first and names the matching fitting, so you can import it anyway or stop.
- **Pop-up windows open centred on the app,** not in the top-left corner of the screen.
- **Message boxes and name prompts follow your colour theme,** and open without the Windows sound. (Save and open file windows are Windows' own and keep its look.)

Coming from RC2? Read the RC3 notes below too.

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
Get-FileHash .\EveFleetManagementTool-1.7.0-setup.exe
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
| `esi-skills.read_skills.v1` | **Skill check:** read the character's trained skills, to say whether they can fly a doctrine fit. |
| `esi-skills.read_skillqueue.v1` | **Skill check:** read the skill queue, to count skills that finished since the last pull. |
| `esi-location.read_online.v1` | **Open in the game client:** check the character is logged in before sending anything to the game. |
| `esi-location.read_location.v1` | **Find a Hull:** start the capital contract search from the system the character is in. Also where the ship they're in is (below). |
| `esi-location.read_ship_type.v1` | The ship the character is sitting in. EVE's asset list leaves it out, so without this a Titan that never docks, or a cyno alt logged off in space, would never be seen. |
| `esi-ui.open_window.v1` | **Open in the game client:** open a market window, a contract, a character's info, or a new mail (filled in, never sent) in the game. |
| `esi-ui.write_waypoint.v1` | **Open in the game client:** set the autopilot destination or add waypoints. |
| `esi-contracts.read_character_contracts.v1` | **Contracts:** read contracts the character can see, including ones assigned to their alliance, to offer replacement ships. |
| `esi-contracts.read_corporation_contracts.v1` | **Contracts:** the same for the corporation's contracts. |
| `esi-fittings.read_fittings.v1` | **Fitting sync:** read the character's saved fittings, to import them or see which are saved. |
| `esi-fittings.write_fittings.v1` | **Fitting sync:** save doctrine fittings to the character, and delete ones it replaces (kept for 14 days to restore). Only when you confirm. |
| `esi-killmails.read_killmails.v1` | **Losses** (off unless you turn it on): read the character's recent killmails, to spot assigned ships that were destroyed. |
| `esi-killmails.read_corporation_killmails.v1` | **Losses:** the same for the corporation's ships. Director only. |

Each feature can be turned off in **Options ▸ ESI Features**; a feature that's off makes no calls.

**What it can change in game:** only what you ask for, after you confirm. It can save and delete in-game fittings, set destinations and waypoints, and open windows in the client. It can't move items, accept or create contracts, send mail, or see your wallet. Corporation hangars and corporation killmails are only read for a character who is a Director.

**Everything stays on your PC.** The app talks only to CCP: the login server, ESI, CCP's static data download, and CCP's status page (`status.eveonline.com`, run for CCP by Atlassian Statuspage), which it reads to know when Tranquility is down. There's no server of ours, no analytics and no telemetry. Everything it stores is in the `data` folder next to the program (for the installer: `%LOCALAPPDATA%\Programs\EVE Fleet Management Tool\data`):

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
