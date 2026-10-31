# Band Magic web UI style and reusable prompt

## Style summary

The interface is a compact technical configuration dashboard for embedded
and industrial devices. It is information-dense but visually calm, with
minimal decoration, clear grouping, and strong emphasis on usability.

Core characteristics:

- desktop-oriented administration interface with responsive wrapping;
- card-based content organization;
- main and secondary tab navigation without page reloads;
- compact forms and editable tables;
- warm gold accent for active and important states;
- muted red for errors;
- light and dark themes;
- horizontal scrolling for wide configuration tables;
- tooltips for values that may be visually truncated;
- one global save bar for configuration edits; live actions apply at once;
- help in separate popup pages, one per main tab.

## Typography

- **Lora**: application title, controller name and card headings;
- **Montserrat**: navigation, forms, tables, status values, and help text;
- system font fallbacks for offline embedded-device operation (the page
  links Google Fonts; without internet the fallbacks are used).

Suggested hierarchy:

- application title: Lora, 18 px, weight 600;
- controller name next to the title: Lora, 15 px, weight 500, muted;
- IP / demo-mode text in the header: Montserrat, 12 px, muted;
- card heading (`h2`): Lora, 14 px, weight 600;
- card subheading (`h3`): Montserrat, 12 px, weight 600, uppercase,
  letter-spacing .03em, secondary text color;
- normal text: Montserrat, 14 px, line height 1.4;
- forms, tables, buttons and tabs: Montserrat, 12 px;
- labels, hints, pills and small buttons: Montserrat, 11 px.

## Color palette

Tokens as used in `data/index.html` (`:root`, dark theme on
`html[data-theme="dark"]`).

### Light theme

```css
--bg: #fafafa;
--card: #ffffff;
--border: #d9d9d9;
--border-strong: #cccccc;
--text: #120d38;
--text-2: #515151;          /* secondary text, labels, table headers */
--text-muted: #7a7a7a;      /* hints, IP, inactive labels */
--accent: #dba41c;
--accent-bg: #fbf0d8;       /* active tab, active row, pills */
--accent-text: #8a6510;     /* text on accent-bg */
--accent-hover: #c0910f;
--on-accent: #120d38;       /* text on a solid gold button */
--danger: #b23b2f;
--danger-bg: #fbe9e6;
--profile-active: #120d38;  /* activated profile button / title */
--fill: #ffffff;            /* inputs, plain buttons */
--fill-muted: #eeeeee;      /* status tiles, hover, empty bits */
--band-row-alt: #f5f3ed;    /* alternate row pair in the band table */
--radius: 8px;
```

### Dark theme

```css
--bg: #0e0e0e;
--card: #1a1a1a;
--border: #2f2f2f;
--border-strong: #3f3f3f;
--text: #f5f5f4;
--text-2: #cccccc;
--text-muted: #7a7a7a;
--accent: #dba41c;
--accent-bg: #3b2e10;
--accent-text: #f0c34e;
--accent-hover: #e8b52e;
--on-accent: #141414;
--danger: #e2766a;
--danger-bg: #3a231f;
--profile-active: #c97a2b;  /* orange instead of navy on dark */
--fill: #1f1f1f;
--fill-muted: #2a2a2a;
--band-row-alt: #24221d;
```

Functional exception: the status popup uses green for RX
(`#1e7a2e` / dark `#4caf6b`, outline) and red for TX (`#c62828` fill with
near-black text). These are receive/transmit signals, not brand colors.

### Theme switching

- Toggle button in the header shows the theme it switches to ("Dark" /
  "Light"); small outlined 11 px button.
- Choice stored in `localStorage` (`bandMagicTheme`), applied by a script in
  `<head>` before first paint, so there is no light flash; every page
  (status popup, help pages) reads the same key. Storage failures are
  caught and the page falls back to light.

## Layout and components

- Main content width: max 1100 px, padding 16 px 20 px 60 px.
- Header: title, controller name, IP/status, theme toggle on the left;
  main navigation on the right; card background with a bottom border.
- Cards: 12 px radius, 16-18 px internal padding, 16 px apart.
- Card toolbar: heading on the left, buttons on the right (`.toolbar`,
  flex, wraps); the last button is a small **Help** button.
- Card heading conventions:
  - per-profile cards: `Title - <profile name>` (e.g. "PTT sequencer -
    Home shack"), the name updates with the edited profile;
  - global cards: `Title (global, not per-profile)`;
  - live cards: `Live status ...`, optionally with a connection pill.
- Form controls: 30 px high, 6 px radius, `--border-strong` border,
  `--fill` background, 12 px text, min width 60 px.
- Fields: label above the control (`.field`, 11 px label, 4 px gap);
  fields sit in `.row` (flex, wrap, 10 px gap).
- Checkboxes: 15 px, placed before their text inside
  `<label class="row" style="gap:6px">`. A checkbox in a row of labelled
  fields gets an empty `&nbsp;` label so it lines up with the inputs.
- Option groups: several related checkboxes in one row, 20 px gap.
- Hints: `.hint`, 11 px muted text, below the controls they explain; one
  paragraph per topic; the name of the control being explained in bold.
  Safety-critical hints start with a bold sentence.
- Sub-sections inside a card: separated by a 1 px top border and 12 px
  padding (e.g. the BCD profile switching row, Serial monitor).
- Tables: 12 px, compact cells, left-aligned headers in `--text-2`,
  1 px row borders, active row on `--accent-bg`.
- Wide tables: minimum column widths plus horizontal scrolling.
- Truncated fields: native `title` tooltip containing the complete value;
  short explanations on hover use the CSS tooltip `.field-tip[data-tip]`
  (inverted colors, max 200 px).
- Conditional blocks: `.conditional` hidden until `.visible` (source
  specific settings, options that only apply to one type).

Main tabs (current):

- Status
- Profiles (configuration profiles, input source, watchdog, PTT sequencer)
- Outputs
- IP relays
- Broadcast to LAN
- Virtual CAT COM
- TrxNet
- Network
- System

Secondary tabs (input source, inside Profiles):

- CAT/CI-V
- BCD input
- BCD Modbus
- Bluetooth CI-V
- Network (N1MM/DXLog)
- TCI (SunSDR)
- FlexRadio LAN

The active tab uses `--accent-bg`, `--accent-text` and a gold border.
Inactive tabs use the card background. A tab that cannot be chosen is
disabled at 45 % opacity with a `title` saying why.

### Buttons

- `.btn`: 30 px, plain fill, strong border; hover `--fill-muted`.
- `.btn.primary`: solid gold with `--on-accent` text (Save & restart).
- `.btn.small`: 24 px, 11 px text (Help, per-row actions).
- `.btn.activated`: solid `--profile-active` with white text (the
  activated profile); stays fully opaque when disabled.
- `.btn.ghost-danger`: red text, pale red border (delete/remove).

### Profiles

- Profile cards in a 3-column grid, 10 px radius; click to edit.
- The card being edited has a 2 px gold border; the activated profile's
  title is a navy (dark: orange) pill and its full-width button reads as
  activated.
- Everything below (input source, watchdog, PTT sequencer, Outputs,
  IP relays) edits the selected card's profile.

### Saving

- Configuration edits only mark the page dirty. A fixed save bar appears
  bottom-right (gold border, card background, shadow): "Unsaved changes -
  the device restarts when they are saved." with **Discard** and
  **Save & restart** (primary). While saving it shows "Saving..." and
  disables both buttons; a failure turns the bar red and keeps the edits.
- Actions that must work while operating apply live without the save bar:
  profile Activate, antenna toggle, relay test buttons.

### Status, badges and live data

- Status tiles: `.status-grid` (auto-fit, min 150 px), tile on
  `--fill-muted`, 8 px radius, 11 px muted label and 16 px bold value that
  wraps long text.
- Pills: 11 px bold on `--accent-bg`; `.pill.warn` red for errors or
  missing connection.
- Bit grids for relay banks / digital inputs: 18 px squares (26 px in
  tools), index above, gold when set, target bit ringed in gold, unused
  bits dimmed; check badges `.chk-badge` ok (gold) / fail (red).

### Status popup (`status.html`)

- Separate small window (opened sized to the main window), centered
  content, no card chrome, text not selectable.
- Frequency: Montserrat bold `clamp(36px, 12vw, 64px)`, tabular numerals,
  muted clickable unit; mode/VFO info in gold above the unit.
- Antenna toggle: gold outlined button (grey outline when not
  switchable); antenna name below.
- PTT badge: "RX" green outline, "TX" red fill, following the device's
  single "transmitting" signal (the same one that shows the LCD padlock).
- Extra lines only when relevant: PTT sequencer stage (muted, fault in
  red), watchdog, no profile, IP relay / BCD Modbus errors (red, bold).

### Help pages (`help_*.html`)

- One help page per main tab, opened by the card's **Help** button as a
  popup sized as a fraction of the main window and clamped to min/max
  (`openScaledPopup`), centered over it.
- Same tokens and fonts; body 13 px, line height 1.55, padding 18-20 px.
- `h1` Lora 19 px with a muted subtitle; `h2` Lora 14 px with a bottom
  border; `h3` 12 px uppercase.
- Settings explained in two-column tables (Setting / Meaning).
- `.note` box (gold border on `--accent-bg`) for tips and limits;
  `.warning` box (red border on `--danger-bg`) for safety and wiring.
- Inline `code` chips on `--fill-muted` for commands, endpoints, pins.

### Band table (Outputs)

- Each band entry is a pair of rows (A1 / A2) sharing one frequency range;
  pairs alternate background (`--band-row-alt`), the first row has no
  bottom border, the second a 2 px border, the active pair is gold.
- Short muted row labels (A1, A2); min widths for antenna names (160 px),
  relay selectors (150 px) and the VFO selector (92 px); table min width
  1900 px.
- Native scrollbar hidden; an always-visible range slider below the table
  (12 px track, 48 px gold thumb) scrolls it horizontally.

### Modals

- Overlay `rgba(20,22,18,.45)`, box on `--card`, 12 px radius, max 560 px
  wide, large shadow; used for detailed tools (bit calculator, relay tools).

## Reusable implementation prompt

```text
Create a responsive embedded-device configuration web interface in a compact
technical dashboard style.

Visual direction:
- Professional industrial/radio-control interface.
- Information-dense but calm and readable.
- White cards on a very light gray background in light mode.
- Dark charcoal cards and near-black background in dark mode.
- Deep navy text with a warm gold accent.
- Avoid gradients, glassmorphism, oversized headings, and decorative effects.
- Use subtle borders, 6-12 px corner radii, and restrained shadows (only for
  the floating save bar, tooltips and modals).

Typography:
- Use Lora for the application title, controller name and card headings.
- Use Montserrat for navigation, forms, tables, status values, and help text.
- Provide system-font fallbacks for offline embedded-device operation.
- Base text: 14 px.
- Form, table, button and tab text: 12 px.
- Labels, hints, pills and small buttons: 11 px.
- Card subheadings: 12 px uppercase with slight letter spacing.

Color tokens (light):
--bg: #fafafa; --card: #ffffff;
--border: #d9d9d9; --border-strong: #cccccc;
--text: #120d38; --text-2: #515151; --text-muted: #7a7a7a;
--accent: #dba41c; --accent-bg: #fbf0d8; --accent-text: #8a6510;
--accent-hover: #c0910f; --on-accent: #120d38;
--danger: #b23b2f; --danger-bg: #fbe9e6;
--profile-active: #120d38;
--fill: #ffffff; --fill-muted: #eeeeee; --band-row-alt: #f5f3ed;

Dark mode (html[data-theme="dark"]):
--bg: #0e0e0e; --card: #1a1a1a;
--border: #2f2f2f; --border-strong: #3f3f3f;
--text: #f5f5f4; --text-2: #cccccc; --text-muted: #7a7a7a;
--accent: #dba41c; --accent-bg: #3b2e10; --accent-text: #f0c34e;
--accent-hover: #e8b52e; --on-accent: #141414;
--danger: #e2766a; --danger-bg: #3a231f;
--profile-active: #c97a2b;
--fill: #1f1f1f; --fill-muted: #2a2a2a; --band-row-alt: #24221d;

Use green (RX) and red (TX) only as functional transmit/receive signals.

Layout:
- Top header with the device name, controller name, current status or IP
  address, theme toggle, and the main navigation.
- Limit the main content width to approximately 1100 px.
- Divide each page into cards with 16-18 px internal padding and a toolbar:
  heading left, actions right, a small Help button last.
- Per-profile card headings end with " - <profile name>"; global ones say
  "(global, not per-profile)".
- Use flexbox form rows with wrapping on narrow screens.
- Keep controls compact: approximately 30 px high.
- Put labels above fields; checkboxes before their text in a label row.
- Place explanatory help text (11 px, muted) below related controls, one
  paragraph per topic, naming the control in bold.

Navigation:
- Main navigation as tab-like buttons without page reload.
- A second level of tabs inside pages for source or device variants;
  unavailable tabs disabled with a tooltip giving the reason.
- Highlight the active tab with a pale gold background, gold border and
  dark gold text.

Tables:
- Compact editable tables with clearly separated headers.
- Highlight the active row with a pale gold background.
- Give text fields and selectors practical minimum widths.
- Put wide tables inside horizontally scrollable containers, hide the
  native scrollbar and add an always-visible range-slider scrollbar below.
- Group related rows in pairs with alternating background.
- Add a title tooltip containing the complete value to fields and selectors
  whose content may be visually truncated.
- Use short row labels such as A1 and A2 where space is limited.

Components:
- status tiles in an auto-fit grid;
- profile cards (edited card outlined in gold, activated one in navy);
- editable configuration tables;
- compact text and numeric inputs;
- select controls and checkboxes;
- primary (gold), plain, small, activated and danger buttons;
- inline validation messages;
- pills (gold, red warning) and connection-state badges;
- bit grids for relay/input states;
- modal dialogs for detailed tools;
- a floating save bar;
- light/dark theme toggle;
- a separate large-type status popup and separate help popup pages.

Interaction rules:
- Changes update the local UI immediately.
- Configuration edits are collected and saved together from a floating save
  bar (Discard / Save & restart); a failed save keeps the edits and shows a
  persistent red error in the bar.
- Operational actions (activate profile, toggle antenna, test a relay)
  apply live without the save bar.
- Do not rely only on color; include text or symbols for important states.
- Preserve keyboard accessibility, labels, focus states, and ARIA descriptions.
- Use native title tooltips for truncated values.
- Remember the theme in localStorage and apply it before first paint.
- Keep the application usable without external JavaScript frameworks.

Technical constraints:
- Produce semantic HTML, plain CSS, and vanilla JavaScript.
- Keep dependencies minimal for use on an embedded web server.
- Avoid large icon libraries and heavy animations.
- Make colors and spacing reusable through CSS custom properties.
- Keep the application functional when served from a local device.
```

For an ESP32 or another constrained embedded target, keep the vanilla
JavaScript and minimal-dependency requirements. For a larger application,
the same visual rules can be translated into React, Vue, or another component
framework without changing the design language.
