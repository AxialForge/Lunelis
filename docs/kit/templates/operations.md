---
type: operations
title: <Product> Operations Guide
subtitle: Install, update, back up, restore, recover
audience: The person who looks after the installation
sources: <modules>
---

# Where things live
```text Data folder
<folder tree with one-line purposes>
```

# Install and update
```mermaid Update flow: from check to restart.
flowchart LR
  A[Check] --> B[Download] --> C[Verify] --> D[Swap] --> E[Restart]
```

# Back up and restore
## Back up
1. <Step>
## Restore
1. <Step>
::: warn
<What can go wrong, and how to avoid it.>
:::

# Recovery decision tree
```mermaid Something is wrong: where to start.
flowchart TD
  A{Does it start?} -->|No| B[Check the log]
  A -->|Yes| C{Is data missing?}
```

# Health checks
| Check | How | Healthy looks like |
|:--|:--|:--|

# Logs and support
| File | What it holds | Where |
|:--|:--|:--|

# To check
- <item>
