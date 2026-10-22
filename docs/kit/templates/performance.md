---
type: performance
title: <Product> Performance and Test Report
subtitle: What was measured, on what, and what it means
audience: Maintainers
sources: <benchmark scripts, test folders>
---

# Summary
::: stats
<n> | <headline metric>
<n> | <second>
:::

# Method and environment
| Item | Value |
|:--|:--|
| Machine | <CPU, RAM, disk, OS> |
| Data set | <size, kind> |
| Version | <version and commit> |

# Results
```chart <What is compared>
{"type":"bar","labels":["Before","After"],"series":[{"name":"Seconds","values":[10,4]}]}
```

| Test | Result | Target | Verdict |
|:--|:--|:--|:--|
| <test> | <value> | <value> | [pass] |

# Bottlenecks
- <Where the time goes>

# How to reproduce
```text Commands
python -m pytest
```

# Test coverage
| Area | Tests | Notes |
|:--|:--|:--|

# To check
- <item>
