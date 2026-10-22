---
type: schema
title: <Product> Data Model
subtitle: Tables, keys, relationships and how they change
audience: Developers
sources: <schema module, migrations>
---

# Overview
```mermaid Entity relationships: the tables that matter most.
erDiagram
  FILES ||--o| EXIF : has
  FILES ||--o{ TAGS : carries
```

::: stats
<n> | tables
<n> | migrations
<n> | version
:::

# Tables
## <Table>
| Column | Type | Meaning |
|:--|:--|:--|

# Migrations
| Version | What changed | Why |
|:--|:--|:--|

# Invariants
- <Rule that must always hold, and where it is enforced>

# Common queries
```sql <What it answers>
SELECT 1;
```

# To check
- <item>
