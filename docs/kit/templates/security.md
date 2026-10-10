---
type: security
title: <Product> Security and Privacy Report
subtitle: What it protects, what leaves the PC, and what could go wrong
audience: Owners and reviewers
sources: <modules>
---

# Summary
::: stats
<n> | network destinations
<n> | open findings
<n> | controls in place
:::

# What is protected
| Asset | Why it matters | Where it lives |
|:--|:--|:--|

# Trust boundaries
```mermaid Boundaries: what sits inside and outside the PC.
flowchart LR
  subgraph PC
    app[App] --> data[(Data)]
  end
  app -. opt-in .-> net((Internet))
```

# Controls
| Threat | Control | Where in the code | Status |
|:--|:--|:--|:--|

# Network use
| Destination | Purpose | What is sent | Opt-in? |
|:--|:--|:--|:--|

# Privacy inventory
| Data | Stored where | Leaves the PC? | How to remove |
|:--|:--|:--|:--|

# Findings
| ID | Finding | Severity | Status |
|:--|:--|:--|:--|

# Residual risk
<What is accepted, and why.>

# To check
- <item>
