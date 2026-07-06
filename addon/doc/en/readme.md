![NVDA Logo](https://www.nvaccess.org/files/nvda/documentation/userGuide/images/nvda.ico)

# SiteMarker


<p align="center">Mark Your Way, Jump with Ease</p>


<p align="center"><b>author:</b> chai chaimee</p>
<p align="center"><b>url:</b> https://github.com/chaichaimee/SiteMarker</p>


## Description

Every day, you spend hours inside the same websites – reading articles, checking dashboards, filling forms, or navigating through long lists. To move from one important section to another, you press the arrow keys again and again, hunting for that one paragraph or button you need. It is repetitive, tiring, and wastes your precious time.

**SiteMarker** changes that. It lets you place permanent markers on any paragraph or element inside a web page. Once marked, you can jump directly to that spot with a single keystroke – forward or backward, in any order. No more endless scrolling or arrow‑key marathon. Just mark, jump, and get things done.


## Hotkeys

The main key for all actions is <kbd>Windows+F12</kbd>. This is a **multi‑tap** key – its behaviour changes depending on how many times you press it quickly (within 0.4 seconds).

- **Single tap (<kbd>Windows+F12</kbd> once)** – If you are on a line that *already has* a marker, the edit dialog for that marker opens. If the line *has no marker* yet, the **Add Marker** dialog appears, pre‑filled with the current paragraph text.
- **Double tap (<kbd>Windows+F12</kbd> twice quickly)** – Opens the **Site Manager** for the current website. If the site already exists, you can edit its settings or manage all its markers. If the site is new, you are prompted to add it.

## Getting Started – Step by Step

### 1. Add Your First Site

When you open a website where you want to use markers, double‑tap <kbd>Windows+F12</kbd>. The **Add Site** dialog appears.

- **Display Name:** Give your site a friendly name (e.g., “Gmail Inbox” or “Daily News”).

- **URL Pattern:** Enter the web address (or part of it) that identifies this site.

- **Match Type:** Choose how SiteMarker should match the current URL against your pattern.

  **Domain Only** – Matches only the main domain (e.g., pattern `google.com` works for `www.google.com`, `mail.google.com`, but not for `google.co.uk`).

  **Include Subdomains** – Like Domain Only, but the URL must end with your pattern. For example, pattern `example.com` matches `sub.example.com` and `news.example.com`, but not `example.org`.

  **Contain Substring** – Matches if your pattern appears anywhere in the full URL (including path and query). Example: pattern `/search` matches `https://www.google.com/search?q=test`.

  **Exact Matching** – The entire URL must equal your pattern exactly (case‑insensitive). Use this when you want markers only for one specific page, like `https://example.com/dashboard`.

  **Regular Expression** – For power users. Write a regex pattern (e.g., `https://.*\.example\.com/.*`) to match any page under any subdomain of `example.com`.

Click **Save Site**. Your site is now registered, and you can start placing markers.

### 2. Place Your First Marker

Navigate to the exact paragraph, line, or button you want to remember. Then **single‑tap** <kbd>Windows+F12</kbd>. The **Add Marker** dialog opens.

- **Pattern** – SiteMarker automatically fills this with the text currently under focus. You can edit it if needed. This text is what SiteMarker searches for.

- **Pattern Match** – Choose how the pattern should be compared:

  **Contains Text** – Finds the pattern anywhere inside the paragraph (partial match).

  **Exact Paragraph** – The paragraph must equal the pattern exactly (whole text match).

  **Regex Match** – Use a regular expression for advanced matching.

- **Mode** – Decide what this marker does:

  **Jump** – When you press the assigned jump key, the cursor moves to this marker. You can assign one of four jump keys: <kbd>J</kbd>, <kbd>F</kbd>, <kbd>D</kbd>, or <kbd>Z</kbd>.
  Press the key alone to jump **forward** to the next marker with that key. Press <kbd>Shift</kbd>+key to jump **backward**.

  **Auto Click** – Instead of just moving focus, SiteMarker will automatically **click** on the element when you press the assigned click key. The available click keys are <kbd>Alt+J</kbd>, <kbd>Alt+C</kbd>, <kbd>Alt+X</kbd>, and <kbd>Alt+Z</kbd>.

- **Display Name** – Give your marker a meaningful name (e.g., “Submit Button” or “Summary Section”).

- **Offset** – This moves the final cursor position relative to the found marker. Enter a whole number (positive or negative) to shift the landing position.

  **Positive number** (e.g., `2`) moves the cursor *forward* that many paragraphs after the marker.

  **Negative number** (e.g., `-1`) moves the cursor *backward* that many paragraphs before the marker.

  **0** (default) lands exactly on the marker itself.

Click **OK** – your marker is saved and ready to use.

## Jump & Click Like a Pro

Once you have markers set up, you can navigate your site effortlessly:

- Press <kbd>J</kbd>, <kbd>F</kbd>, <kbd>D</kbd>, or <kbd>Z</kbd> (alone) to jump forward to the **next** marker assigned to that key.
- Press <kbd>Shift+J</kbd>, <kbd>Shift+F</kbd>, <kbd>Shift+D</kbd>, or <kbd>Shift+Z</kbd> to jump **backward** to the previous marker.
- For auto‑click markers, press <kbd>Alt+J</kbd>, <kbd>Alt+C</kbd>, <kbd>Alt+X</kbd>, or <kbd>Alt+Z</kbd> – the cursor will find the next matching marker and click on the target element automatically.

Every jump speaks the target paragraph aloud and moves the screen review cursor to that position, so you always know where you are.

## Managing Everything

You can always double‑tap <kbd>Windows+F12</kbd> to open the **Site Manager**. From there you can:

- Edit a site’s display name, URL pattern, or match type.
- Add, edit, or delete markers for any site.
- Clear all markers at once.
- Navigate back and forth between the site list and the marker list.

The **Marker Manager** (accessible from the Site Manager) gives you a complete overview of all markers for the current site, with quick options to edit or delete each one.

## Share Your Markers

All your site and marker configurations are stored in a simple **JSON file** for each site. These files are located in:
