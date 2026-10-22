---
type: audit
title: <Subject> Audit
subtitle: <Scope in one line>
audience: Owners and maintainers
sources: <commit range, folders reviewed>
status: Draft
---

# Verdict
<Two to four sentences. State the overall result and the one thing to do first.>

::: stats
<n> | findings
<n> | high or critical
<n> | fixed
<n> | open
:::

# Scope and method
| In scope | Out of scope | How it was reviewed |
|:--|:--|:--|

# Findings at a glance
```chart Findings by area and severity
{"type":"stacked","labels":["Area A","Area B"],"series":[{"name":"High","values":[1,0],"color":"#A3203A"},{"name":"Medium","values":[2,3],"color":"#E0A030"},{"name":"Low","values":[4,2],"color":"#6C8EBF"}]}
```

# Findings
| ID | Finding | Severity | Status |
|:--|:--|:--|:--|
| A1 | <What is wrong, in one line> | [medium] | [open] |

## A1: <Title>
**Evidence:** <file:line>. **Impact:** <what goes wrong, for whom>. **Recommendation:** <fix>.

# Checked and sound
- <Thing that was reviewed and found correct>

# Recommended order of work
1. <First fix, and why>

# To check
- <item>
