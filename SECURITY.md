# Security

This page says how to report a security problem in VECTOR, the desktop's AI assistant,
and what happens after you do. Please report it privately, so it can be fixed before
anyone else hears about it.

VECTOR runs commands, reads your screen and talks to models, so his limits matter. They
are code, listed in [docs/safety.md](docs/safety.md). A way around one of them is a
security problem.

## Report a problem

Don't open a public issue for a security problem. Use one of these instead:

- **GitHub's private report.** Open the repository's **Security** tab and choose **Report
  a vulnerability**. Only the maintainers see it.
- **Email.** Write to contact@bromigos.org with "Security" at the start of the subject.

Tell us:

- what is affected: a tool, a limit, or a file in this repository;
- the version, from `pacman -Q bromigos-vector`;
- how to reproduce the problem, step by step. If a prompt does it, include the prompt;
- what an attacker could do with it.

Leave out anything private, such as your hostnames, addresses or tokens. We don't need
them.

## What happens next

We reply to say we have your report. We tell you whether we can reproduce the problem,
and we keep you posted until it's fixed. When the fix ships, we credit you in the release
notes, unless you'd rather we didn't.

Please give us a fair chance to ship a fix before you tell anyone else.

## What this covers

- The code in this repository: VECTOR, his tools and limits, the hologram system and the
  gallery.
- The rest of the desktop lives in
  [bromigos-org/bromigOS](https://github.com/bromigos-org/bromigOS). Report a problem
  there the same way.

A model that says something wrong is not a security problem by itself. A model that gets
VECTOR to do something his limits should stop is one.

## Which versions get fixes

A fix ships in the next bromigOS release on each channel. Older releases don't get
separate fixes. To get a fix, update with `bromigos update`.
