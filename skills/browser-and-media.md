---
name: browser-and-media
description: How VECTOR opens web pages and videos in the user's browser and controls what's playing (find the link, open it in a new tab, check it started, then play, pause, skip, seek and set the volume), and how he uses his own browser, a separate Chrome he drives himself, for research and multi-step web tasks (open, find, click or type, read), with what it refuses.
when_to_use: The user asks to play or put on a video, song, stream or YouTube, to open a website or link, to pause, resume, skip, rewind or turn something up or down, or asks what's playing; or a task needs the web beyond one fetch, such as reading several pages, using a site's search box, following links, filling a search or filter form, or checking how a page looks.
triggers: '\b(youtube|video|music|song|playlist|stream|podcast|spotify|play|pause|resume|unpause|skip|rewind|fast.?forward|volume|turn (it )?(up|down)|what''?s playing|browser|tab|website|web ?page|open (a |the )?(link|site|url)|your (own )?browser|search box|click (on )?|fill (in|out)|look (it )?up on)\b'
---

# Browser and media

## Play a video

1. **Find a real link.** `web_search` with `site:youtube.com` and what they asked for
   ("lofi hip hop radio site:youtube.com"). Take a `youtube.com/watch?v=…` URL
   from the results. Never make up a video id.
2. **Open it:** `launch` with the URL. It opens in a **new tab** of the user's browser
   (Chrome), in front. Don't launch the browser first, and never use the terminal
   (`xdg-open`) for this; `launch` is the way.
3. **Check it started:** a few seconds later, `media` status. The tab shows up as a
   `chromium.instance…` player with the video's title. If it's `Paused` (browsers
   sometimes block autoplay), `media` play.
4. Say in one line what's playing (the title from `media`, not your guess).

## Control what's playing

`media`: `play`, `pause`, `toggle`, `next`, `previous`, `stop`, `seek` (`+10`, `-30`
or an absolute second), `volume` (`0-100`, `+10`, `-10`). `status` lists every player
with its title, position and volume. With several players, pass `player` from status;
otherwise the most recent one is used.

- "Pause it" / "turn it down a bit": act straight away, then confirm in a few words.
- `volume` sets the player's own volume. The system volume is the user's.

## Your own browser

You have your own browser: a separate Chrome on the desktop (window class
`vector-browser`), with its own profile, that you drive with the `browser_*` tools and
The user can watch. It is never the user's Chrome: their tabs, sign-ins and history are out of reach,
and videos for them still go to their browser with `launch` and `media` (above).

**When to use it:** research across several pages, reading a page `web_fetch` can't
extract (built by JavaScript), using a site's own search box or filters, following links,
multi-step web tasks, checking how a page looks. One plain article: `web_fetch` is quicker.

**How:**

1. `browser_open` the URL (a new tab by default; `new_tab=false` reuses the current one).
2. `browser_find` with a few words ("search", "next page", "pricing") gives the matching
   links, buttons and fields with refs (`e1`, `e2`, …). Refs last until the page changes;
   find again after a click.
3. `browser_click` a ref (or the element's visible text), or `browser_type` into a field's
   ref; `submit=true` presses Enter (a search box).
4. `browser_read` for the title, url and visible text; `browser_scroll` for more of a long
   page; `browser_look` with a question when the layout, a chart or an image matters (a
   screenshot of your own tab to the vision model, never the user's screen).
5. `browser_back` and `browser_tabs` (list, switch, close) to move around; `browser_close`
   when the task is done.

Say in one line what you're doing before a multi-step task ("let me look that up on the
vendor's site"), then report what you found, with where it came from.

**Refused, in code (and a refusal ends the turn: say what was refused and ask the user):**

- Sensitive sites: banking and payments, brokerages, crypto exchanges, wallets and
  prediction markets, password managers, Vault, the hosts the private overlay marks as
  sensitive, admin consoles for money, and LAN hosts other than the lab's own domain.
  Links and redirects there are refused too.
- Logins: you never type into password, username, email or code fields, or anything in a
  login form, and never press sign-in. When `browser_read` says `login_page`, or a site
  needs an account, tell the user the site wants their login; don't look for a way around it.
- Purchases and payments: no checkout, place order, buy, pay, subscribe, add to cart,
  donate, upgrade or trial buttons, and no card or bank fields.
- Downloads and uploads are off; secret-looking text is never typed.

## What you can't do in the user's browser

You can open pages in new tabs of the user's Chrome and control media. You can't click inside
his pages, type into them, read them, or switch or close his tabs. For a page's text, use
`web_fetch` or your own browser; to see their screen, `look` (only when asked). `window` can
focus or close their whole browser window (say so first). If they need more, say plainly
what's missing.
