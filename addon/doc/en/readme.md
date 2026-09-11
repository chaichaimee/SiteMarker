![NVDA Logo](https://www.nvaccess.org/files/nvda/documentation/userGuide/images/nvda.ico)

# SiteMarker

Bookmark any spot on any website, then jump straight back to it with a single keypress.

**author:** chai chaimee  
**url:** https://github.com/chaichaimee/SiteMarker

> **Note:** The slogan above and the GitHub URL are not present in the source code and should be reviewed/confirmed before publishing.

## Introduction

SiteMarker lets you place invisible "markers" on specific pieces of text on any webpage, then jump directly to them later, or have NVDA click them for you automatically. Instead of arrowing or Tab-bing through a long page every time you visit it, you press one hotkey and land exactly where you need to be — a chat box, a "Load more" button, a specific headline, a "Skip ad" button, or anything else that repeats or is hard to find.

Markers are organized per website. Each website you configure gets its own set of markers, its own URL matching rule, and its own "focus mode" behaviour. Everything is saved automatically, so once a site is set up, jumping and clicking just work every time you return to that page.

### Hot Keys

> **NVDA+Windows+F12**  
> Single Tap : Add/Edit Marker at the current line (opens the marker editor if no marker matches here yet, or the Marker Manager with a new marker ready to be added if this site has no configuration at all)  
> Double Tap : Open Site Manager (or Add Site dialog if the current site has not been configured yet)  
> Triple Tap (or more) : Open Marker Manager for the current site

> Taps are counted within a 0.4 second window of each other. Pressing the hotkey once and waiting briefly triggers the single-tap action; pressing it again quickly within that window advances to the double-tap action, and so on.

> **J** / **Shift+J**  
> Jump to the next / previous marker assigned to the J key

> **F** / **Shift+F**  
> Jump to the next / previous marker assigned to the F key

> **D** / **Shift+D**  
> Jump to the next / previous marker assigned to the D key

> **Z** / **Shift+Z**  
> Jump to the next / previous marker assigned to the Z key

> **Alt+J**  
> Single Tap : Auto-click the element matching the marker assigned to Alt+J

> **Alt+C**  
> Single Tap : Auto-click the element matching the marker assigned to Alt+C

> **Alt+X**  
> Single Tap : Auto-click the element matching the marker assigned to Alt+X

> **Alt+Z**  
> Single Tap : Auto-click the element matching the marker assigned to Alt+Z

> **Note:** The Jump keys (J, F, D, Z) and Auto-Click keys (Alt+J, Alt+C, Alt+X, Alt+Z) are only active while you are in browse mode on a supported browser (Chrome, Edge, Firefox, Brave, Opera, Vivaldi, or Thorium). On any other window, or when a site has no marker assigned to that key, the key is passed straight through so it types or behaves normally.

## Features

### Site Manager

Every website you want markers on must first be added as a "site." A site is defined by:

* **Display Name** — a friendly name shown in the Site Manager list.
* **URL Pattern** — the address or domain text that is checked against the page you are currently on.
* **Match Type** — how the pattern is compared to the page's actual URL:
  * Domain Only — the page's domain must equal the pattern exactly.
  * Include Subdomains — the domain must equal the pattern, or end with it (so a pattern of "example.com" also matches "mail.example.com").
  * Contain Substring — the pattern just needs to appear anywhere in the URL.
  * Exact Matching — the whole URL must match the pattern exactly.
  * Regular Expression — the pattern is treated as a regex and searched against the URL.
* **Focus Mode** — controls how NVDA's normal focus/forms-mode behaviour is altered on this site:
  * Normal — NVDA behaves as usual.
  * Don't enter form mode — when a control on this site receives real Windows focus, NVDA is prevented from automatically switching into forms mode for that focus event (browse mode stays active).
  * Disable focus — browse/focus mode switching driven by real focus changes is suppressed entirely on this site.

From the Site Manager you can add, edit, or delete a site, and jump straight into that site's Marker Manager. Pressing Delete on a selected site (or F2 to edit) works directly from the list, and a right-click/context menu offers the same actions.

### Marker Manager and Marker Properties

Each site holds its own list of markers. A marker is a rule that tells SiteMarker "when the text on this page matches this pattern, this is a landmark." Every marker has:

* **Pattern** — the text (or regular expression) to search for. When you open the marker editor from the page itself (via the NVDA+Windows+F12 single tap), this is pre-filled with your current selection, the current paragraph under the caret, or the focused control's name/value, whichever is available first.
* **Pattern Match** — how the pattern is compared against each paragraph on the page:
  * Contains Text — matches if the pattern appears anywhere inside the paragraph.
  * Exact Paragraph — matches only if the whole paragraph equals the pattern exactly.
  * Regex Match — the pattern is treated as a regular expression and searched within the paragraph.
* **Mode** — whether this marker is a **Jump** marker (moves your cursor/caret there) or an **Auto Click** marker (activates the element automatically).
* **Key** — which hotkey triggers this marker: J, F, D, or Z for Jump markers; Alt+J, Alt+C, Alt+X, or Alt+Z for Auto Click markers.
* **Search Scope** — Entire Document searches the whole page (loading more content further down if needed); Viewport Only restricts the search to roughly the currently visible area of the page (about 50 paragraphs above and below your caret).
* **Display Name** — a friendly label shown in the Marker Manager list.
* **Offset** — how many lines (matching what a single Up/Down arrow-key press would move) to shift away from the matched paragraph before landing, from -50 to +50. This is useful when the text you can reliably match isn't exactly the line you want to end up on (e.g. matching a post's author name but wanting to land two lines below, on the post's reply button).

The Marker Manager list lets you Add, Edit, or Delete a single marker, or Clear All markers for the site at once. Delete and F2-to-edit also work directly on the selected list item, and a right-click context menu duplicates these actions. If you open the multi-tap hotkey on a paragraph that already matches one of the site's existing markers, SiteMarker opens that marker directly for editing instead of offering to add a new one.

### Jumping to markers (J / F / D / Z)

Pressing a jump key scans paragraph by paragraph, forward or backward from your current position, looking for any marker assigned to that key. As soon as a match is found:

1. If the marker has an offset, SiteMarker moves that many lines away from the matched paragraph (or, if an offset pattern is set, keeps scanning nearby lines for one matching that pattern instead of a fixed line count) before landing.
2. If the offset would land you on an empty paragraph, SiteMarker keeps stepping forward until it reaches non-empty text.
3. Your caret is moved there and NVDA speaks the destination text.

If the visible part of the page runs out before a match is found (for example, an endless "feed" style page), SiteMarker automatically sends Page Down to load more content and keeps searching, up to 100 load-more attempts. If nothing is found after all of that, or after 5 attempts in a row that added no new content, you'll hear "No next marker" or "No previous marker."

SiteMarker avoids re-announcing the exact same spot twice in a row (some pages repeat identical text in several places in the underlying accessibility tree) and skips over matches that land inside an editable field or combo box, retrying past them instead.

### Auto-Click (Alt+J / Alt+C / Alt+X / Alt+Z)

Pressing an auto-click key scans the page from the very beginning for a marker assigned to that key, applies its offset the same way jump markers do, and then clicks the matching element for you using a three-step fallback so that even stubborn buttons (such as a video site's "Skip Ad" button) get activated reliably:

1. Try the element's own accessibility "do action" command.
2. If that isn't available, try invoking it via its UI Automation Invoke pattern.
3. If both of those fail, move the mouse cursor over the element and simulate a physical left-click.

After a successful click, NVDA speaks the clicked text and announces "Clicked." On YouTube specifically, if the page's URL and the text under the caret haven't changed shortly after clicking (a sign the click didn't register, which can happen with a not-yet-fully-interactive skip button), SiteMarker automatically retries the click up to 2 more times. On other sites this automatic re-click is skipped, since a toggle-style control can succeed on the very first click while still looking unchanged, and re-clicking would just toggle it back off.

If no matching, clickable target is found by the end of the page, SiteMarker automatically retries the whole search up to 5 times with a short, increasing delay between attempts (to catch elements, like an ad's skip button, that only appear a second or two after the page loads), before finally announcing "No auto click target found."

### Automatic focus-theft recovery

On some pages, the automatic "load more" scrolling SiteMarker performs during a scan can accidentally land real keyboard focus on an unrelated editable field or search box instead of just scrolling the page. When this happens during an active scan, SiteMarker automatically restores focus back to the document so your search isn't interrupted. If the exact same page keeps stealing focus back onto the same field no matter how many times this is corrected, SiteMarker stops trying to scroll further for that scan and simply finishes the search with whatever content has already loaded.

### Background page-change detection

SiteMarker automatically re-checks the active markers whenever you switch focus, whenever the page's content updates, and on a periodic timer (about every 2 seconds) while you're on a browser page. This keeps your markers up to date on single-page apps and dynamically updating sites without you having to do anything manually.

### Per-site data storage

All of your sites and their markers are saved automatically to your NVDA user configuration folder (under a SiteMarker folder) as one file per site, so your setup is preserved between NVDA sessions and does not require any internet connection to work.

## Support Me

If this tool has made your life easier, consider fueling the next update with a small donation.

[![Support me](https://img.shields.io/badge/Donate-Support%20Me-blue?style=for-the-badge&logo=stripe)](https://buy.stripe.com/dRm9AU1xQ3Ds22N6VK1VK01)

Your support means the world. Let's build something great together

© 2026 Chai Chaimee NVDA Add-on Released under GNU GPL