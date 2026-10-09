# User Guide

This tool checks whether your characters' ships are ready for your doctrines, and tells you what to fix and what to buy. Everything stays on your computer.

The tool is built from a few pieces that point at each other:

- **Fittings**: what a ship should carry ([Fittings tab](#the-fittings-tab)).
- **Roles**: a set of requirements, each one a fitting at a place ([Library tab](#the-library-tab)).
- **Doctrines**: a set of roles, with characters assigned to them.
- **Ships**: your real ships, each assigned a fitting and an owner ([Ships tab](#the-ships-tab)).
- **Audits**: the doctrine checked against the ships ([Doctrines tab](#the-doctrines-tab)).

Skim [Getting started](#getting-started) first, then use the [workflows](#workflows) as recipes.

## Getting started

1. **Add your characters.** Use **Characters ▸ Add Character**. Your browser opens EVE's login page; log in and accept. Repeat for each character. They appear in **Options ▸ Connected Characters**, in alphabetical order.
2. **Pull your assets.** In **Options**, press **Pull All**. It reads every character's assets, clones and implants, and the hangars of any corporation one of your characters is a Director of ([corporations](#corporations)). After a pull, Pull All rests for 15 minutes. Tick **Enable Auto Pull** to pull once an hour while the tool is open.
3. **Add fittings.** In **Fittings**, press **New Fitting** and paste a fit from the game (Fitting window ▸ Copy to Clipboard). See [The Fittings tab](#the-fittings-tab).
4. **Build a doctrine.** In **Library**, create roles and a doctrine, give each role its requirements, and assign characters. See [Build a doctrine](#build-a-doctrine).
5. **Assign your ships.** In **Ships**, give each of your ships the fitting it's meant to fly. The audit only checks assigned ships. See [Assign your ships](#assign-your-ships).
6. **Audit.** In **Doctrines**, pick the doctrine and press **Run Doctrine Audit**. See [Read an audit](#read-an-audit).

The first time it starts, the tool downloads Fenris Creations' game data (about 100 MB) and builds its database. When an update needs new data, it offers to rebuild it; **Tools ▸ Check for DB Update** checks for a newer Fenris Creations release.

## The tabs

### The Doctrines tab

Pick a doctrine and press **Run Doctrine Audit**. The tree on the left shows the doctrine, then each role, each character assigned to it, each of their requirements, and the ships checked for each one. The **Shopping List** on the right collects what's missing ([Shop for what's missing](#shop-for-whats-missing)).

What it checks comes from three places: the doctrine and roles in the [Library](#the-library-tab), the fittings in [Fittings](#the-fittings-tab), and which ship is assigned which fitting in [Ships](#the-ships-tab).

### The Ships tab

Every ship one holder has. Pick the holder at the top: one of your characters, or a corporation ([corporations](#corporations)). The list is grouped by place, then hangar (your personal hangar, a corporation division by name, deliveries, asset safety), then ship. A ship carried inside another, in its ship maintenance bay or fleet hangar, sits under its carrier. **Ship** shows the hull; the name you gave it in game is in **Name**, beside it.

- Select ships of one hull, choose a fitting, press **Assign Fitting**. **Clear Fitting** takes it away.
- **Several ships of one hull** (Ctrl- or Shift-click), then right-click ▸ **Onboard These Ships…**: one window for all of them, with **Fitting** (any saved fitting of the hull, or <Personal>), **Owner** and **Home**, set together with **Apply**. No doctrine is needed. Leave Home empty to keep each ship's Home, or give it the system it's in.
- **Columns:** drag a column's edge to resize it; the widths are remembered for every character and corporation. The last column, **Owner**, takes whatever room is left.
- **Owner** says whose requirements the ship serves ([owners](#owners)).
- **Assigned only** hides ships with no fitting.
- On the right, the selected ship's **Audit** and **EFT** tabs ([check one ship](#check-one-ship)).
- **The ship a character is sitting in** is listed too, though EVE's asset list leaves it out: a Titan that never docks shows in its system, marked **(in space)**. A character added before this needs adding again (**Characters ▸ Add Character**) for it.

The **Implants** tab, beside **Ships**, shows the chosen character's clones: the active clone and each jump clone with where it is, its implants by slot, and which of your library's implant sets it carries ([implant sets](#implant-sets)). It also gives the home station and the last clone jump.

### The Library tab

Where doctrines are put together.

- **Doctrine Library** and **Role Library** (left): create, rename and delete doctrines and roles.
- **Role Requirements** (middle): pick a role, then add what it needs. Each requirement is a **Hull** and **Fit**, at a **System** (or **<Any System>**) and optionally a **Station**. The list is grouped by system.
- **Doctrine Overview** (right): pick a doctrine and **Add** roles to it. Right-click a role to **Assign character**, or a character to remove them.
- **Doctrine Packages**: **Export Doctrine Package…** and **Import Doctrine Package…** ([share a doctrine](#share-a-doctrine)).

The **Hull** and **System** boxes are searchable: type a few letters to filter.

### The Fittings tab

Your saved fittings, grouped by ship class and hull, in a list down the left. Beside it, two tabs for the selected fitting:

- **Loadout** (shown first):
  - **Left:** the fit, one line per slot (empty slots faded), then the drone or fighter bay. A hull with neither shows a faded **Drone bay 0 m³** box.
  - **Right:** the cargo (and fleet hangar), then the fitting's doctrine requirements, edited right there ([capitals](#set-up-a-capital)).
  - **An implant set** shows just its implants.
- **EFT text**: the fitting as EFT text. **Edit** and **New Fitting** switch to it.

- **Search**: type in the box above the list to show only fittings whose ship class, hull or name contains the text. **Clear** (or Esc) shows them all again.
- **New Fitting**: paste EFT text from the game. Tick **Shared Doctrine Fitting** for a fitting that's part of a doctrine you'll share ([shared and local](#shared-and-local)).
- **Edit**, then **Save**: change a fitting's EFT text. Anything the EVE database doesn't know is listed and left out.
- If a fit you import or save is identical to one already saved (same hull, modules, drones, fighters and cargo, whatever its name), the tool says which and asks before saving another copy.
- **Requirements you haven't saved:** picking another fitting, or pressing **Edit** or **New Fitting**, asks first. **Yes** saves them, **No** discards them, **Cancel** stays.
- Right-click a fitting to **Rename…**, **Delete…** or **Copy-Multibuy** (a whole ship's worth, for the game's Multibuy).

A fitting for the **Capsule** with implants in it is an [implant set](#implant-sets).

### The Options tab

**Connected Characters**, **Pull All**, **Auto Pull**, **Appearance** (the colour theme) and **ESI Features**. Right-click a character (or select it and press Delete) ▸ **Remove Character…**. Under **Pull All**, a line says what a pull is doing: which character, a safe-mode countdown, characters skipped.

**ESI Features** turns features that use extra data from Fenris Creations on and off. A feature that's off makes no calls and shows nothing. So far:

- **Skill check:** pulls each character's skills with their assets and checks them against their doctrine fittings ([skill check](#skill-check)).
- **Open in the game client:** the shopping list can set a route, show the market or start a mail in a character's EVE client ([open in game](#open-in-the-game-client)).
- **Contracts:** your own contracts count as stock, and a missing ship is offered your alliance's contracts ([contracts](#contracts)).
  - **Public contracts in Find a Hull** (off unless you turn it on): the capital search reads public contracts too. It warns first, since a wide search is slow the first time.
- **Prices**, with the **Trade hub** beside it: the shopping list priced at the hub's sell orders ([prices](#prices)).
- **Fitting sync with the game:** import fits saved in the game, save doctrine fits to a pilot's in-game fittings, and keep them up to date ([fitting sync](#fitting-sync-with-the-game)).
- **Losses** (off unless you turn it on): recognises assigned ships that were destroyed ([losses](#losses)). Under it, each off unless chosen: **SRP items**, **Insurance estimate**, **Corporation losses (Directors)**.

### Tranquility's status

The lamp on the right of the tab row shows Tranquility's status. The app checks it before anything calls Fenris Creations.

- **Green:** online.
- **Amber:** online with problems. In **VIP mode** (only Fenris Creations staff can log in), calls to Fenris Creations are off. When Fenris Creations reports **ESI degraded**, they still go ahead, but some may fail.
- **Red:** offline, or the **daily downtime** (10:55–11:15 UTC; a good check from 11:10 ends it early).
- **Grey:** unknown, e.g. no internet.

A **red dot** on the lamp means Fenris Creations' status page has something new: an incident, or maintenance coming up. Hover over the lamp to read it. Click it to open the page, which also clears the dot. The dot clears itself after an hour too, or when the incident is resolved.

**While Tranquility is down:**
- **Pull All**, **Add Character** and **Check for DB Update** are off. Everything that works from what you've already pulled keeps working: the library, audits, exports.
- **Auto Pull** waits quietly and tries again every 5 minutes. The hourly timer starts again from the pull that succeeds.

**When a pull fails part way:**
1. **The first failure** stops the pull. You're asked whether to **Retry All** characters.
2. **If it fails again,** the app goes into a 5-minute **safe mode**, reading Fenris Creations' status page every 30 seconds.
   - If the page reports a problem, pulls are **on hold** until Tranquility is back.
   - If the page is clear, the pull carries on from the character that failed.
3. **A character that fails again** is skipped, and the rest are pulled.
4. **A character whose login has expired** is skipped straight away: add it again with **Characters ▸ Add Character**.

The pull's summary lists anyone skipped. Their assets stay as they were at the last pull that reached them.

## Workflows

### Build a doctrine

1. In [Fittings](#the-fittings-tab), add each fitting the doctrine uses (**Shared Doctrine Fitting** ticked).
2. In [Library](#the-library-tab), **Create** a role in the Role Library, for example "DPS 1".
3. In **Role Requirements**, choose the role, then a **Hull**, its **Fit**, and where it should be: a **System**, and a **Station** if it must be in one place. **<Any System>** means anywhere. Press **Add Requirement**. Add as many as the role needs, for example a Devoter in Amarr and an Archon in Jita.
4. **Create** a doctrine, pick it in **Doctrine Overview**, and **Add** the role.
5. Right-click the role ▸ **Assign character**.

The doctrine can now be [audited](#read-an-audit), once the ships are [assigned](#assign-your-ships).

### Assign your ships

The audit checks only ships that have a fitting assigned, so it never guesses which of two Devoters is your doctrine one.

1. Open [Ships](#the-ships-tab) and pick the character.
2. Select a ship (or several of one hull), choose the fitting, press **Assign Fitting**. The ship's audit appears on the right.
3. Its owner is set to whoever holds it ([owners](#owners)), and its **Home** to the system it's in.

**Home** is the system a ship belongs to (never a station), or **Anywhere** for ships that live in space, like supers and titans. Change it in the **Home** box under Owner. A ship serves the requirements in its Home system; anywhere else it's a soft failure ("bring it back"), never something to buy. Ships assigned before Homes existed show **—** and audit as they always did until you give them one.

Choose **<Personal>** at the top of the Fitting list for ships that aren't doctrine ships (a mining fleet, your own ratter). The audit never looks at them, and **Assigned only** hides them.

You can also assign from an audit: a requirement marked **⚪ not checked** has hulls of the right type in its system with no fitting, perhaps a replacement you've already bought. Right-click it ▸ **Assign the Fitting to**, then pick a ship, or **All**: it gets the fitting and that system as its Home. A ship with no Home offers **Make <system> Its Home** on its right-click menu.

Assignments follow a ship by its item ID, through contracts and trades. They're remembered for 30 days after a ship was last seen in a pull, so lending a ship to someone outside the tool doesn't lose its settings.

### Read an audit

Each line has an icon:

- ✅ ready.
- ⚠ ready, but needs attention: a refit, a better module than the fit asks for, something to take off, a ship away from its place.
- yellow ❌ a soft failure: the ships exist but are in the wrong place, or the requirement is soft. The character stays ready.
- ❌ not ready: something is missing.
- ⚪ not checked: there's a hull at the place with no fitting assigned ([assign it](#assign-your-ships)).

A character shows ❌ if a hard requirement fails, else ⚪ if one isn't checked. The icons are coloured, and a failing line is tinted: red when it fails, yellow when only a soft requirement does.

**Hard and soft requirements.** A requirement is hard unless you tick **Soft requirement** when adding it (or right-click it in **Library ▸ Role Requirements** ▸ **Make Soft**). A soft requirement is audited the same way, but it only ever warns: the character stays ready, and **Add Every Missing Item** leaves its ships out (right-click to add them). A hard requirement over a wider area (a whole system, or any system) makes the role's requirements for the same fitting inside it soft, so one ship can serve both; the list says "(soft · covered by Jita)". When a package update changes requirements you made hard or soft, you're asked whether to reset them to the fitting manager's.

Under each requirement, its ships:

- **📌 Home**: at the requirement's place. When several are, all are listed and the best one counts.
- **↔ In the system**: its Home is this system, but it's at another station: move it. A soft failure.
- **↗ Deployed**: its Home is this system, but it's elsewhere, or with another character: bring it back. A soft failure.
- **↗ Away (no Home set)**: a ship assigned before Homes, somewhere else. Ready if its fit is, but flagged.
- **❓ Missing**: assigned, but not seen in any pull. Shows where it was last seen, and offers a replacement.

Under each ship: what's missing, and the refit. **Fit** (move from cargo into a slot), **Stow** (move to cargo, where the fit carries it), **Remove** (take it off: the fit doesn't want it). Ammo and scripts loaded in modules count as cargo.

Above the roles: **📦 Ships still packed** (ships carried inside others that the carrier's requirements don't call for, or one fitted differently from what it should be), and **⛽ Fuel** totals across the doctrine.

A pilot can fly their own variant of a requirement: in **Library ▸ Role Requirements**, select it and **Replace Requirement**. The audit then looks for ships assigned that fitting. **Undo Replacement** goes back.

### Audit one system, onboard and adopt ships

Above the audit tree, **By System** swaps the doctrine box for a system box: **Audit System** shows every character's requirements in that system, across all doctrines, by station. The right-click menus and the shopping list work as in **By Doctrine**. (Requirements for any system aren't listed here.)

Two buttons beside it each open a preview; nothing changes until you press **Apply**:

- **Onboard Ships Here…** (new ships, first-time setup): ships in the system with no fitting. Each is offered the fittings its owner's requirements there use for its hull (already chosen when there's one), **Skip**, or **<Personal>**, with how it would audit. **Next** lists ships whose hull has no saved fitting at all, ticked, to mark them <Personal>. Applied, each gets its fitting, its owner and the system as its Home.
  - **Owner: a corporation** (tick it, then pick the corporation): every ship onboarded in the window belongs to that corporation instead of the character holding it. Useful when you've taken ships out of a corporation hangar you can't see in full, fitted them, and want them marked as the corporation's. Each ship is then offered every saved fitting of its hull, doctrine or not, and hulls nothing in the system requires join the list. Corporation ships don't count towards a character's requirements.
- **Adopt Ships Here…** (ships moved for a deployment): ships in the system with a fitting whose Home is somewhere else, or not set. Ticked ones get the system as their Home; their fitting doesn't change. Ships whose fitting nothing in the system uses can't be ticked. The warning under the list names any system the move would leave short (it then fails there, until a ship is bought or moved back).

### Check one ship

In [Ships](#the-ships-tab), select one ship. On the right:

- **Audit**: the same check as in an audit, for this ship alone.
- **EFT**: the ship in EFT layout. The switch at the top changes the view:
  - **Current**, what's on the ship: green in place, orange in the wrong place, struck through to take off.
  - **Expected**, what the fitting says: green aboard, orange aboard but elsewhere, red missing.
  - **A ship with no fitting assigned:** **Current** shows it as it's fitted now, all in green; **Expected** has nothing to show until it has a fitting.

Both views come from the same check as the Audit tab, so they always agree.

Right-click the **Audit** view:

- **Copy Missing Items**: what the ship lacks, one line per item, ready for the game's Multibuy window.
- on a missing item ▸ **Show <item> in Market…**: the item in the market window of the ship's pilot ([open in the game client](#open-in-the-game-client); only while that's on in Options).

### Skill check

With **Options ▸ ESI Features ▸ Skill check** on, each pull also reads the characters' skills. A character added before the skill check needs adding again (**Characters ▸ Add Character**) so its login includes skills. Until then the audit shows "⚪ Skills not checked" under the character.

**In the Doctrines audit,** a requirement whose pilot is missing skills gets a line under it:

- **❌ Can't fly: Carriers V (has IV):** a skill for the hull or a fitted module is missing. On a hard requirement the pilot isn't ready.
- **⚠ Skills to train: Drones V (has IV):** only drones, fighters, charges or cargo need it. A warning; the pilot is still ready.

On a soft requirement everything missing is a warning.

**Right-click** the line or its requirement ▸ **Copy Skill Plan**. It copies the missing levels, one per line, each skill's prerequisites first. Paste it into the game's skill planner with **import from clipboard**.

**In the Ships tab,** right-click any ship ▸ **Can <pilot> Fly This?**. It checks the ship as it is, fitted modules and everything aboard, against its owner's skills (or the skills of the character holding it). It answers **Yes**, or lists what's missing and offers to copy the skill plan. A corporation's ship needs a character as its owner first.

### Shop for what's missing

In the [Doctrines](#the-doctrines-tab) audit tree, right-click:

- a ship ▸ **Add Missing Items to Shopping List**: what that ship lacks.
- a requirement with no ship ▸ **Add Missing Items to Shopping List**: a whole replacement ship.
- anywhere ▸ **Add Every Missing Item in This Audit**: every ship at home (away and missing ships are left to you).

Before buying, the list uses what you already have. Anything the character holds **in the same system and not inside a ship** (hangar, deliveries, containers in the hangar, packaged hulls) is listed under **Pull from stock**, with where it is. Each unit is used once, in the order ships were added. The rest is under **Buy**.

- **Copy Shopping List** and **Export Shopping List**: both sections, with headings.
- **Copy Multibuy**: only the Buy lines, ready to paste into the game's Multibuy window.
- Select lines and press Delete (or right-click ▸ **Remove**) to take items off.

Lines added once show **[x]** in the tree, so nothing is bought twice.

### Prices

With **Options ▸ ESI Features ▸ Prices** on, each line gets a price from the chosen **Trade hub** (Jita 4-4 unless changed), from its sell orders only:

- A **Buy** line costs what that many would cost, cheapest orders first: 20,000 of something is priced at the 20,000 cheapest, not 20,000 × the lowest order. "only 15,000: ≈ 7.5 M" means the hub hasn't enough; "none at hub" means it sells none.
- A **Pull from stock** line shows what its items would cost to buy.
- **Fleet Totals** adds the estimate to buy (items with no sell orders left out, and it says how many) and what your stock covers.
- A **capital hull** is never priced: it comes from contracts ([capitals](#capitals)).

Prices are read again after 5 minutes. "Pricing at Jita 4-4…" shows while they load.

### Contracts

With **Options ▸ ESI Features ▸ Contracts** on, **Pull All** also reads your characters' contracts, and your corporations' (which include your alliance's). The first pull reads a lot of contracts' contents, so it can take a minute; later pulls read only new ones.

- **Your own contracts are stock.** An item exchange you've put up and nobody has taken yet still holds your items: they're listed under **Pull from stock** as "in contract", after your hangar there. To use them, take the contract back first.
- **A missing ship is offered alliance contracts.** Under a hard requirement whose ship is missing altogether (not one that's away or in the wrong place), the tree lists contracts assigned to your corporation or alliance in that system, the requirement's own station first:
  - **Exact fit:** the hull and every fitted module (extras are fine).
  - **Near fit:** the hull and at least 90% of the fitted modules, with what's missing and what's extra.

  A contract that asks for items in return (PLEX, say) isn't offered.

Right-click a contract:

- **Use This Contract…**: for 2 hours the ship isn't bought. It comes off the shopping list if it was on it, and **Add Missing Items** asks first. The line counts down; right-click it ▸ **Cancel Contract Choice** to stop.
  - **When one of your characters accepts it**, the line says "Contract accepted by …: waiting for the asset pull" until the ship turns up, however long that takes.
  - **When the ship turns up,** the choice is done.
  - **When the contract's gone** (someone else took it, or it expired), the line says so and the ship is bought as usual.
- **Open in Game…**: opens it in a character's client. The game shows one contract window at a time, so the window steps through the offers with **Open Next**.

### Fitting sync with the game

With **Options ▸ ESI Features ▸ Fitting sync with the game** on:

- **Fittings ▸ Import from Game…**: pick a character to see the fits they've saved in game, with a hull filter and a search. Fits already in the library are marked. Click fits to tick them, then **Import Ticked**. Ticking one that's already there asks first.
- **Doctrines audit ▸ right-click a pilot ▸ Save Fits to Game…**: the fittings that pilot flies in the audit, and whether each is in their in-game fittings. The ones not saved, or saved in an older version, are ticked. **Save Ticked…** saves them after a confirm.
- **Keeping them up to date:** each fit saved this way carries a short tag at the end of its description, like `[EFMT 1000 v3]`, naming its library fitting and version. When you change a fitting in the Library, pilots with an older copy are offered **Replace the in-game fitting with the update?**
- **Fittings ▸ Deleted from Game…**: the game has no way to edit a saved fit, so replacing one deletes the old copy and saves the new one. Every fit the app deletes is kept here for 14 days, and **Restore** saves it again.

The game's list of fits can take up to 5 minutes to show a change. The app remembers what it saved and deleted in the meantime.

### Losses

With **Options ▸ ESI Features ▸ Losses** on, **Pull All** also reads each character's recent killmails.

A loss is matched to a ship of the same hull that has an assigned fitting, was seen in an earlier pull and is gone now. The ship can belong to any of your linked characters, since one may have flown another's, or, with **Corporation losses** on, to the corporation. The fit isn't compared: modules get swapped and ammo gets used. <Personal> ships and ships whose fitting no requirement uses are never matched.

The app never decides on its own. After the pull it asks about each ship that could be it:
- **Yes:** it's **💥 Lost <date>: replace** in the audit, and greyed out under **Lost ships** in the Ships tab.
- **No:** it isn't, and the next one (if any) is asked about.
- **Cancel:** decide later. Each reads **Possibly lost** until you right-click the right one ▸ **This One Was Lost**.

Right-click a lost or possibly lost ship ▸ **It Wasn't This One** to take it back.
- **When it clears:** the loss stops showing once its requirement passes or only warns (a replacement is there, or on its way), or after 30 days, when the ship's assignment is forgotten.
- **SRP items** (on): right-click a lost ship ▸ **Copy SRP Items**: the hull, then what was destroyed and what dropped.
- **Insurance estimate** (on): the loss line adds what Platinum insurance pays out for the hull.
- **A lost Titan or Supercarrier** gets the app's condolences, and the offer to remove its requirement while the pilot saves up for the next one. No contracts are offered for them.

### Capitals

Carriers, Force Auxiliaries, Dreadnoughts (Lancers too), Rorquals and Jump Freighters have their **hull** from contracts, never the market. Titans, Supercarriers and Black Ops follow the usual rules.

- **In the audit tree,** a missing capital is offered alliance contracts **within one jump** (its range at Jump Drive Calibration V), nearest first. The hull is enough, since the modules come from the hub.
- **Find a Hull…** (right-click that line, or the hull's Buy line on the shopping list) opens a search:
  - **Centre:** where the chosen character is (asked of the game), or any system you pick.
  - **What it reads:** alliance contracts within **5 jumps**. With **Options ▸ ESI Features ▸ Public contracts in Find a Hull** on, public ones too, nearest first: the list fills in as their contents are read. The first wide search can take 5 to 10 minutes; contracts read once aren't read again.
  - **The slider** narrows the list to 1 to 5 jumps at once.
  - **Each contract** says what it holds: **Exact fit**, **Near fit** (with what's missing), another of your fittings for the hull by name, **Hull + rigs**, or **Hull**. Contracts in structures the app can't place are listed last.
  - **Open in Game…** opens the selected one, with **Open Next** for the rest.

Jumps are a straight-line estimate: the distance divided by the hull's range, rounded up. Gates and routes aren't counted.

### Open in the game client

With **Options ▸ ESI Features ▸ Open in the game client** on, right-click a shopping list line:

- a **Pull from stock** line ▸ **Set Destination…** or **Add Waypoint…**: its station or structure, in the autopilot route.
- a **Buy** line ▸ **Show in Market…**: the item in the market window. Now and then the game doesn't show it; the window stays open with **Send Again**.
- in the audit tree, a missing item ▸ **Show <item> in Market…**: the same, for the pilot above it.
- any line ▸ **Mail Shopping List…**: a new in-game mail holding the list, addressed to the pilots it's for. Nothing is sent until you press Send in game.

A small window asks which character's client to use. It starts on the line's pilot and shows who is **in game**. EVE only acts on a character who's logged in and in space or docked (not at character selection), so the app checks first and sends nothing to anyone else. A character who has just logged in can read as offline for up to a minute.

A character added before this feature needs adding again (**Characters ▸ Add Character**) so its login includes it.

### Set up a capital

In [Fittings](#the-fittings-tab), select the fitting: its **Loadout** shows the requirement boxes its hull has, under the cargo. Change them, then press **Save Requirements**; **Clear all** removes them all (it asks first). The boxes:

- **Fuel Bay**: a slider and number per fuel. Each slider stops where the bay is full alongside the others. Jump fuel and module fuels (Strontium for Siege and Triage) are listed; **Add fuel** adds another. A fuel at 0 isn't required.
- **Fleet Hangar + cargo**: extra stock, one item per line, "Name x100".
- **Ship Maintenance Bay**: the list scrolls when it's long. Press **+** to add a ship: a hull with any fitting, or one of your saved fittings of it. **×** removes the selected entry. A carried ship that doesn't match its fitting is reported under **📦 Ships still packed**.
- **Fighter tubes**: in the fighter bay box, beside each fighter type: how many full squadrons of it are loaded in tubes. With any set, the tubes must hold exactly those, and the fighter bay exactly the rest. Left at 0, tubes and bay are counted together.
- **Escape Bay**: the escape frigate's fitting, or **Any ship**.
- **Notes**: shown on the ship's row in audits.

### Implant sets

A [fitting](#the-fittings-tab) for the **Capsule** with implants in it is an implant set; its name is the set's name. Add it to a role like any requirement. The audit looks through the character's clones for it: a jump clone at the requirement's place, then the active clone, then clones elsewhere.

Slots 1–6 must match exactly. Slots 7–10 (hardwirings) need strength 05 or better; if the set lists an 06 or better, that's the minimum, with a warning that the better one is preferred. Slot 10 accepts any mindlink.

### Share a doctrine

In [Library](#the-library-tab) ▸ **Export Doctrine Package…**, tick the doctrines, roles and fittings to share. A role brings its fittings, and a fitting brings the fittings it names (its escape frigate, its carried ships). The package is a single file to send.

To use one: **Import Doctrine Package…**. Re-importing a newer version replaces what the earlier one installed. A package can include which characters fly which role (you choose when exporting); on import, assignments for characters you haven't added are skipped and listed.

Packages never carry logins, assets, clones, ship assignments or corporation data.

### Corporations

A character who is a **Director** of their corporation can read its hangars. **Pull All** then pulls them too, with division names, and the corporation appears as a holder in [Ships](#the-ships-tab), acting like a character. If several of your characters are Directors of the same corporation, one pull covers it. A character added before this feature needs adding again ([Add Character](#getting-started)) to grant the corporation permissions.

### Owners

Each assigned ship has an **owner**: a character or a corporation. It's whoever held the ship when it was first assigned; change it with **Owner** in [Ships](#the-ships-tab). The list offers every linked character's corporation, even one with no linked Director (its hangars aren't pulled, but a ship can still be its).

- A ship serves only its owner's requirements. A corporation's ship sitting in your hangar doesn't count for you.
- Your ship counts for you wherever it is; in a corporation hangar it's **Away**, unless that's where the requirement is.
- When someone other than the owner holds a ship, the Owner column shows **↩ owner**: give it back.

## Reference

### Shared and local

**Shared Doctrine Fitting**s, and the roles and doctrines built from them, can go into [packages](#share-a-doctrine). Local ones are yours only and never leave. A shared role uses shared fittings; a local role, local fittings.

### Menus

- **Characters ▸ Add Character / Remove Character…**: removing a character deletes their login and asset data and their role assignments.
- **Tools ▸ Check for DB Update**: newer Fenris Creations game data, with a link to EVE's patch notes. A new database is applied when the app starts, so after the download it offers **Restart now?**
- **Tools ▸ Name Unknown Structures…**: name a player structure that none of your characters can see.
- **Tools ▸ Clear Asset Data… / Clear Non-Doctrine Data… / Clear Library… / Full Reset…**: delete pulled data, local records, the whole library, or everything. Each asks first. After a Full Reset the app has to start again: **Yes** restarts it, **No** closes it.
- **Debug ▸ View Logs / Export Logs**: what the tool did, for bug reports.
- **Help ▸ User Guide** (F1): this guide.
- **Help ▸ Check for Updates…**: a newer version of the tool. The installed version downloads it, checks it and installs it, then opens again with your data kept; the portable zip opens the release page. **Options ▸ Updates** switches the quiet once-a-day check at startup.

### What stays private

Logins, assets, clones, corporation data and ship assignments stay in the tool's data folder on your computer. The tool talks only to EVE's servers, and to GitHub when it checks for a new version of itself.
