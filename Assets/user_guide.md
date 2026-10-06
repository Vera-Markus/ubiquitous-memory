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

The first time it starts, the tool downloads CCP's game data (about 100 MB) and builds its database. When an update needs new data, it offers to rebuild it; **Tools ▸ Check for DB Update** checks for a newer CCP release.

## The tabs

### The Doctrines tab

Pick a doctrine and press **Run Doctrine Audit**. The tree on the left shows the doctrine, then each role, each character assigned to it, each of their requirements, and the ships checked for each one. The **Shopping List** on the right collects what's missing ([Shop for what's missing](#shop-for-whats-missing)).

What it checks comes from three places: the doctrine and roles in the [Library](#the-library-tab), the fittings in [Fittings](#the-fittings-tab), and which ship is assigned which fitting in [Ships](#the-ships-tab).

### The Ships tab

Every ship one holder has. Pick the holder at the top: one of your characters, or a corporation ([corporations](#corporations)). The list is grouped by place, then hangar (your personal hangar, a corporation division by name, deliveries, asset safety), then ship. A ship carried inside another, in its ship maintenance bay or fleet hangar, sits under its carrier.

- Select ships of one hull, choose a fitting, press **Assign Fitting**. **Clear Fitting** takes it away.
- **Owner** says whose requirements the ship serves ([owners](#owners)).
- **Assigned only** hides ships with no fitting.
- On the right, the selected ship's **Audit** and **EFT** tabs ([check one ship](#check-one-ship)).

### The Library tab

Where doctrines are put together.

- **Doctrine Library** and **Role Library** (left): create, rename and delete doctrines and roles.
- **Role Requirements** (middle): pick a role, then add what it needs. Each requirement is a **Hull** and **Fit**, at a **System** (or **<Any System>**) and optionally a **Station**. The list is grouped by system.
- **Doctrine Overview** (right): pick a doctrine and **Add** roles to it. Right-click a role to **Assign character**, or a character to remove them.
- **Doctrine Packages**: **Export Doctrine Package…** and **Import Doctrine Package…** ([share a doctrine](#share-a-doctrine)).

The **Hull** and **System** boxes are searchable: type a few letters to filter.

### The Fittings tab

Your saved fittings, grouped by ship class and hull.

- **New Fitting**: paste EFT text from the game. Tick **Shared Doctrine Fitting** for a fitting that's part of a doctrine you'll share ([shared and local](#shared-and-local)).
- **Edit**, then **Save**: change a fitting's EFT text. Unknown items are listed.
- If a fit you import or save is identical to one already saved (same hull, modules, drones, fighters and cargo, whatever its name), the tool says which and asks before saving another copy.
- **Edit Doctrine Requirements**: what a capital or other ship carries beyond its EFT text ([capitals](#set-up-a-capital)).
- Right-click a fitting to **Rename…**, **Delete…** or **Copy-Multibuy** (a whole ship's worth, for the game's Multibuy).

A fitting for the **Capsule** with implants in it is an [implant set](#implant-sets).

### The Options tab

**Connected Characters**, **Pull All**, **Auto Pull**, and **Appearance** (the colour theme).

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
3. Its owner is set to whoever holds it ([owners](#owners)).

You can also assign from an audit: a requirement marked **⚪ not checked** has hulls of the right type at its place with no fitting. Right-click it ▸ **Assign the Fitting to**, then pick a ship, or **All**.

Assignments follow a ship by its item ID, through contracts and trades. They're remembered for 30 days after a ship was last seen in a pull, so lending a ship to someone outside the tool doesn't lose its settings.

### Read an audit

Each line has an icon:

- ✅ ready.
- ⚠ ready, but needs attention: a refit, a better module than the fit asks for, something to take off, a ship away from its place.
- ❌ not ready: something is missing.
- ⚪ not checked: there's a hull at the place with no fitting assigned ([assign it](#assign-your-ships)).

A character shows ❌ if anything fails, else ⚪ if anything isn't checked.

Under each requirement, its ships:

- **📌 Home**: at the requirement's place. When several are, all are listed and the best one counts.
- **↗ Away**: assigned and owned, but somewhere else. Ready if its fit is, but flagged.
- **❓ Missing**: assigned, but not seen in any pull. Shows where it was last seen, and offers a replacement.

Under each ship: what's missing, and the refit. **Fit** (move from cargo into a slot), **Stow** (move to cargo, where the fit carries it), **Remove** (take it off: the fit doesn't want it). Ammo and scripts loaded in modules count as cargo.

Above the roles: **📦 Ships still packed** (ships carried inside others that the carrier's requirements don't call for, or one fitted differently from what it should be), and **⛽ Fuel** totals across the doctrine.

A pilot can fly their own variant of a requirement: in **Library ▸ Role Requirements**, select it and **Replace Requirement**. The audit then looks for ships assigned that fitting. **Undo Replacement** goes back.

### Check one ship

In [Ships](#the-ships-tab), select one ship with a fitting. On the right:

- **Audit**: the same check as in an audit, for this ship alone.
- **EFT**: the ship in EFT layout. The switch at the top changes the view:
  - **Current**, what's on the ship: green in place, orange in the wrong place, struck through to take off.
  - **Expected**, what the fitting says: green aboard, orange aboard but elsewhere, red missing.

Both views come from the same check as the Audit tab, so they always agree.

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

### Set up a capital

In [Fittings](#the-fittings-tab), select the fitting and press **Edit Doctrine Requirements**. The boxes shown depend on the hull's bays:

- **Fuel Bay**: a slider and number per fuel. Each slider stops where the bay is full alongside the others. Jump fuel and module fuels (Strontium for Siege and Triage) are listed; **Add fuel** adds another. A fuel at 0 isn't required.
- **Fleet Hangar + cargo**: extra stock, one item per line, "Name x100".
- **Ship Maintenance Bay**: press **+** to add a ship: a hull with any fitting, or one of your saved fittings of it. **×** removes the selected entry. A carried ship that doesn't match its fitting is reported under **📦 Ships still packed**.
- **Fighter tubes**: how many full squadrons of each fighter type are loaded in tubes. With any set, the tubes must hold exactly those, and the fighter bay exactly the rest. Left at 0, tubes and bay are counted together.
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

Each assigned ship has an **owner**: a character or a corporation. It's whoever held the ship when it was first assigned; change it with **Owner** in [Ships](#the-ships-tab).

- A ship serves only its owner's requirements. A corporation's ship sitting in your hangar doesn't count for you.
- Your ship counts for you wherever it is; in a corporation hangar it's **Away**, unless that's where the requirement is.
- When someone other than the owner holds a ship, the Owner column shows **↩ owner**: give it back.

## Reference

### Shared and local

**Shared Doctrine Fitting**s, and the roles and doctrines built from them, can go into [packages](#share-a-doctrine). Local ones are yours only and never leave. A shared role uses shared fittings; a local role, local fittings.

### Menus

- **Characters ▸ Add Character / Remove Character…**: removing a character deletes their login and asset data and their role assignments.
- **Tools ▸ Check for DB Update**: newer CCP game data.
- **Tools ▸ Name Unknown Structures…**: name a player structure that none of your characters can see.
- **Tools ▸ Clear Asset Data… / Clear Non-Doctrine Data… / Clear Library… / Full Reset…**: delete pulled data, local records, the whole library, or everything. Each asks first.
- **Debug ▸ View Logs / Export Logs**: what the tool did, for bug reports.
- **Help ▸ User Guide** (F1): this guide.

### What stays private

Logins, assets, clones, corporation data and ship assignments stay in the tool's data folder on your computer. The tool talks only to EVE's servers.
