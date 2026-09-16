<p align="center">
  <img src="https://www.nvaccess.org/files/nvda/documentation/userGuide/images/nvda.ico" alt="NVDA Logo" width="120">
</p>

<h1 align="center">SiteMarker</h1>

<p align="center">Bookmark any spot on a webpage and jump straight back to it, every time you visit — no more hunting through long pages line by line.</p>

<p align="center">
  <b>author:</b> chai chaimee<br>
  <b>url:</b> <a href="https://github.com/chaichaimee/SiteMarker">https://github.com/chaichaimee/SiteMarker</a>
</p>

---

## Introduction

Do you visit the same websites over and over, and keep having to scroll or search for the same button, headline, or field every single time? SiteMarker lets you "mark" any spot on a page — by the text near it — and then jump straight back to that spot with a single keypress, or even have NVDA click it for you automatically.

Think of it as personal bookmarks for the web, but smarter: instead of remembering a scrollbar position, SiteMarker remembers a piece of text (like "Skip Ad", "Reply", or a friend's name in a chat list) and finds it again for you, even if the page has changed shape since your last visit.

SiteMarker works per‑website: you set up your markers once for a site (e.g. YouTube, Facebook, a webmail inbox), and from then on your shortcuts work automatically whenever you're browsing that site. It runs in the background while you use Chrome, Edge, Firefox, Brave, Opera, Vivaldi, or Thorium, and stays out of the way everywhere else.

---

### Hot Keys

> **NVDA+Windows+F12**  
> **Single Tap :** Add or Edit Marker for the current paragraph  
> **Double Tap :** Open Site Manager (or Add Site, if this site isn't set up yet)  
> **Triple Tap :** Open Marker Manager for the current site  

This one key combination does three different things depending on how many times you tap it quickly (within about four‑tenths of a second of each other):

* **Single Tap** — If your cursor is currently sitting on text that already matches one of your markers, this opens that marker's Properties dialog directly so you can edit it. Otherwise, it opens the Marker Manager and immediately starts adding a brand‑new marker based on whatever text your cursor or focus is on.
* **Double Tap** — Opens the Site Manager for the site you're currently on, so you can review, add, edit, or delete sites and jump into their marker lists. If the current site hasn't been configured at all yet, this opens the Add Site dialog instead, pre‑filled with the current page's domain.
* **Triple Tap** (or more) — Jumps straight to the Marker Manager for the current site, skipping the Site Manager. If no site is configured yet for the current page, you'll be told to add a site first.

> **J**  
> Jump to the next marker assigned to this key  

> **Shift+J**  
> Jump to the previous marker assigned to this key  

> **Alt+J**  
> Automatically click the element matched by the marker assigned to this key  

**J**, **Shift+J**, and **Alt+J** are the only jump/auto‑click keys reserved by SiteMarker out of the box. This is deliberate: every other letter key (like F, D, or Z) is left completely free for NVDA's own browse‑mode navigation unless you specifically choose to use it. If you want more shortcuts, you assign a **Custom Shortcut** to a marker instead (see Features below) — that key then becomes active only on sites where you've actually used it, and only while you're on those sites.

---

## Features

### 1. Marking Text on a Page

A "marker" tells SiteMarker to watch for a specific piece of text on a specific website. When you press its assigned key, SiteMarker searches the page for that text and takes you there (or clicks it, if it's set to Auto Click).

Each marker has these settings, found in the **Marker Properties** dialog (opened from Marker Manager's Add/Edit buttons, or automatically via the NVDA+Windows+F12 single‑tap shortcut):

* **Pattern** — the text SiteMarker looks for. Pre‑filled automatically from your current selection or cursor position when you open this dialog.
* **Pattern Match** — how the Pattern is compared against each paragraph on the page:
  * *Contains Text* — matches if the paragraph contains your pattern anywhere within it.
  * *Exact Paragraph* — matches only if the whole paragraph is exactly your pattern.
  * *Regex Match* — treats your pattern as a regular expression for advanced matching.
* **Mode** — *Jump* (move your cursor there and read it aloud) or *Auto Click* (activate/click it automatically).
* **Key** — J for a Jump marker or Alt+J for an Auto Click marker by default, or your own **Custom Shortcut** (see below).
* **Auto‑jump / Auto‑click on page load** — when turned on, this marker fires by itself as soon as the page finishes loading, with no keypress needed at all.
* **Search Scope** — *Entire Document* searches the whole page (loading more content automatically on long or infinite‑scroll pages if needed), while *Viewport Only* restricts the search to roughly what's near your current position, which is faster on very long pages.
* **Display Name** — the label shown for this marker in the Marker Manager list. Defaults to the Pattern if left blank.
* **Offset** — how many lines to move, up or down, after the match is found, before landing — exactly like pressing the Up or Down arrow key that many times. A negative number moves up, a positive number moves down. Useful when you actually want the line just above or below your matched text, not the match itself.

### 2. Custom Shortcuts per Marker

Instead of using the shared J or Alt+J keys, any marker can be given its own dedicated keyboard shortcut via the **Custom Shortcut...** button in Marker Properties. Press the button, then simply press the key combination you want to use — SiteMarker captures it and shows it back to you.

For a Jump‑mode marker, assigning a custom key automatically also binds Shift+that key to jump backward to the previous match, mirroring how Shift+J pairs with J. Choosing **Use Standard Key** reverts the marker back to the shared J or Alt+J key. Custom shortcuts only become active while you're on a site that actually uses them, so they never interfere with your other add-ons or with NVDA elsewhere.

### 3. Site Manager

Opened via NVDA+Windows+F12 (double tap), the Site Manager lists every website you've configured markers for. From here you can:

* **Add New Site** — set up a new website, pre‑filling the URL from wherever you currently are.
* **Edit Site** — change a site's settings (see below).
* **Delete Site** — remove a site and all of its markers (with a confirmation prompt).
* **Manage Markers** — jump into that site's Marker Manager.

Pressing Delete or F2 while a site is selected in the list also deletes or edits it, and a right‑click context menu offers the same actions.

Each site has these settings, in the Add/Edit Site dialog:

* **Display Name** — the name shown for this site in the list.
* **URL Pattern** — the address or domain this configuration applies to.
* **Match Type** — how the URL Pattern is compared to the page you're browsing:
  * *Domain Only* — matches this exact domain only.
  * *Include Subdomains* — also matches subdomains of this domain.
  * *Contain Substring* — matches if the page's address contains this text anywhere.
  * *Exact Matching* — matches only this exact, full address.
  * *Regular Expression* — matches using a regular expression for advanced patterns.
* **Focus Mode** — controls how NVDA's own focus/forms mode behaves on this site:
  * *Normal* — NVDA behaves as it normally would.
  * *Don't enter form mode* — prevents NVDA from automatically switching to forms mode when focus moves, useful on sites where that switch is more disruptive than helpful.
  * *Disable focus* — keeps browse mode active regardless of what receives focus on the page.
* **Skip blank lines when navigating with Up/Down and Ctrl+Up/Down** — when checked, pressing these arrow keys on this site automatically skips over empty lines/paragraphs instead of stopping on them, so you land on the next line or paragraph that actually has content.

### 4. Marker Manager

Lists every marker set up for one particular site. From here you can Add, Edit, or Delete a single marker, or use **Clear All** to remove every marker for this site at once (each with a confirmation prompt). Pressing Delete or F2 on a selected marker, or right‑clicking it, gives you the same options. A **Back to Site Manager** button returns you to the site list.

### 5. Automatic Loading of More Content

On pages that load more content as you scroll (social‑media feeds, chat threads, and similar "infinite scroll" pages), a Jump marker set to search the *Entire Document* will automatically scroll the page further down for you if it reaches the end of what's currently loaded without finding a match, then keep searching — up to a generous number of attempts — before telling you no match was found.

### 6. Reliable Clicking, Including Stubborn Buttons

When a marker's Mode is set to Auto Click, SiteMarker tries several different ways to activate the matched element (NVDA's standard action, then a more modern accessibility interface, then finally a real simulated mouse click as a last resort), so it can successfully click buttons that don't always respond to the simplest method — such as a video site's "Skip Ad" button. On YouTube specifically, if a click doesn't seem to have taken effect yet, SiteMarker automatically retries it a couple more times.

### 7. Focus Protection During a Scan

While SiteMarker is scrolling a page to load more content for a scan, it's possible (on some sites) for that scrolling to accidentally land your real keyboard focus on a search box or other editable field instead of staying on the page. SiteMarker automatically detects this and restores your focus back to the document, so a jump or auto‑click in progress isn't derailed.

---

## Support Me

If this tool has made your life easier, consider fueling the next update with a small donation.

[![Support me](https://img.shields.io/badge/Donate-Support%20Me-blue?style=for-the-badge&logo=stripe)](https://buy.stripe.com/dRm9AU1xQ3Ds22N6VK1VK01)

Your support means the world. Let's build something great together.

***

<p align="center">
  © 2026 Chai Chaimee NVDA Add-on Released under GNU GPL
</p>