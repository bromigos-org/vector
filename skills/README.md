# VECTOR's skills

A skill is a page of know-how for one kind of task, written for VECTOR to read. He loads
a skill when a question needs it, so his instructions stay short the rest of the time.

## How skills are found

VECTOR reads skills from these folders. When two share a name, the first one wins:

1. `~/.config/bromigos/skills/`, your own
2. `~/.local/share/bromigos/ext/active/skills/`, from installed extensions (`vector-skill`)
3. folders that plugins add ([docs/plugins.md](../docs/plugins.md))
4. `/usr/lib/bromigos/vector/skills/`, the ones in this folder

For each question, a router compares the question with every skill's `description` and
`when_to_use`. It attaches at most two skills that clearly fit. A skill whose `triggers`
match the question is attached first. VECTOR can also load a skill himself
(`load_skill`). A new or changed skill is live on the next question.

## The skills in this folder

| Skill | Use it for |
|---|---|
| [browser-and-media](browser-and-media.md) | Opening pages and videos, controlling what plays, and multi-step web tasks in his own browser |
| [hyprland-config](hyprland-config.md) | Changing the Hyprland config safely: keybinds, window rules, devices, login programs |
| [live-shader-layer](live-shader-layer.md) | Adding a background shader layer or deck to the live layer |
| [make-on-the-homelab](make-on-the-homelab.md) | Making images, cutouts, 3D meshes, holograms and voice lines for free |
| [make-sound-cue](make-sound-cue.md) | Making a sound or voice cue, within the budgets and the no-cloned-voices rule |
| [propose-a-change](propose-a-change.md) | Changing bromigOS, the homelab repo, the catalog, the dotfiles or his own code through his clones and a pull request |
| [update-visualization](update-visualization.md) | Changing a panel, deck, hologram or his console through the build loop |

## Write a skill

A skill is a Markdown file, either `<name>.md` or `<name>/SKILL.md`. It starts with
YAML frontmatter:

```markdown
---
name: water-the-garden
description: How to check the garden sensor and start a watering run.
when_to_use: The user asks about the garden, the soil or watering.
triggers: '\b(garden|watering|soil)\b'
---

# Water the garden

1. Read the sensor with `garden_status`.
...
```

| Field | Required | What it's for |
|---|---|---|
| `name` | Yes | Lower-case letters, digits and hyphens |
| `description` | Yes | What the skill teaches, in a sentence. The router reads it |
| `when_to_use` | Yes | The situations it's for. The router reads each one |
| `triggers` | No | A regular expression. A question it matches gets the skill first |

Keep a skill to what the task needs, and name files and commands exactly. A skill is text,
never code. It never holds hostnames, addresses or secrets. Write `{{endpoints.lab}}`-style
placeholders instead, and VECTOR fills them from your private overlay.
