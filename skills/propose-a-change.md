---
name: propose-a-change
description: How to change bromigOS, the homelab repo, the extensions catalog, the dotfiles or your own code the way a developer does — in your own clone, as your GitHub account bromigodev, through a branch, a pull request, CI and a merge — and what to tell the operator afterwards.
when_to_use: "Fixing or changing code or docs in bromigOS, VECTOR (yourself), the homelab repo, the extensions catalog or the dotfiles; opening, watching or merging a pull request; any 'make a PR', 'fix that bug in your code', 'change the lab config'."
triggers: '\b(pull request|PR|pr_open|pr_merge|work_clone|your own code|fix (it|that) in (bromigos|the homelab|your code))\b'
---

# Propose a change

You change these repos in your own clones, never in the operator's checkouts under
`~/github.com` (they're read-only to you) and never by hand on a server.

| repo | what it is | what a merge means |
|---|---|---|
| `vector` | your own code | it reaches the operator's desktop with the next `bromigos update` (edge first) |
| `bromigOS` | the desktop | the same, for the bromigOS packages |
| `homelab` | the lab, as code | Argo CD applies it to the lab from master |
| `extensions` | the extensions catalog | machines see it when they fetch the catalog again |
| `dotfiles` | the operator's dotfiles (public) | his `~/.dotfiles` gets it when he pulls |

Your clones are in `~/.local/share/bromigos/vector/work/<repo>`. You work there as
bromigodev, your own GitHub account.

## Small changes: do it

A typo, a doc fix, a small bug with an obvious fix, a value in the lab's config.

1. `work_clone` the repo. It clones it, or fetches and rebases your branches.
2. `work_branch` with a short name (`fix-safety-typo`).
3. Read the repo's AGENTS.md or CLAUDE.md and README first. Edit the files with
   `run_shell`, cwd = your clone. Keep the change small and in the repo's own style.
4. Run the repo's tests in the clone with `run_shell`: the ones the repo's docs or CI
   name for what you changed. Fix what fails. Say which you ran. Your terminal reads the
   text of a test script before it runs it, and refuses a script that names something it
   refuses, such as a path to a key. Then say so, and let CI run that test.
5. `work_commit` with the exact paths you changed and a message in the repo's style
   (look at `git log --oneline -10` first). Never a folder, never a glob.
6. `work_push`. It rebases on the latest default branch first.
7. `pr_open` with a short title and a plain description: what changed, why, and how you
   tested it. No lore, no filler.
8. `pr_merge`. It waits for CI and merges when it's green.
9. Tell the operator in a sentence or two: what changed, the PR's link, and what arrives
   where (the result's `arrives`). For your own code and bromigOS, that's "with your next
   `bromigos update`".

## Larger changes: plan first

More than a few files, a new feature, anything in the lab beyond one app's values,
anything you're not sure about. Explain the plan to the operator first, in a few
sentences, and wait for his go-ahead. Then follow the steps above. Several small PRs are
better than one large one.

## What you never merge

`pr_merge` refuses a PR that touches the code behind your limits: the terminal's sandbox,
its secret and real-money refusals, the emergency switch, the guard, your tool limits,
Vault policies and your cluster permissions, the host's real-money code, the workflows
and CODEOWNERS. `bromigos-holo work limits` lists them. Such a PR stays open for the
operator, and the PR says so. You may open one when he asks for it. Then tell him it's
his to review and merge. Don't look for another way to land it: your terminal refuses
`gh pr merge`, the API's merge, and pushes to main.

## When something goes wrong

- **CI is red.** Read the failing run (`github` action runs, or `gh run view --log-failed`
  in your clone). Fix it on the same branch, `work_commit`, `work_push`, then `pr_merge`
  again.
- **The rebase conflicts.** `work_push` stops and pushes nothing. Resolve the conflict
  in the clone (`git status` shows where), then try again. If it isn't clearly yours to
  resolve, tell the operator.
- **No credential.** If a tool says the bromigodev token isn't set up, tell the operator
  and stop.
- **The dotfiles refuse a commit.** The dotfiles are public. Move the private detail to
  the private overlay, as the refusal says.

## Lab changes

Lab changes go through the homelab repo, never by hand on a server: no `kubectl edit`,
no editing files over SSH. Change the repo, open the PR, merge it, then `argocd_wait`
for the app to confirm it's Synced and Healthy.
