---
title: "PKB Tag Registry"
source_type: tag-registry
---

# PKB Tag Registry

## Namespace: topic.bbq

- `topic.bbq` – root topic
    - `topic.bbq.equipment`

## Namespace: topic.cooking

- `topic.cooking` – root topic
    - `topic.cooking.grilling`
    - `topic.cooking.heat-management`
    - `topic.cooking.recipes` *(topic-specific extension)*

## Namespace: type

- `type.note` – human-written note
- `type.reference` – static source
- `type.solution` – reusable solution (a note tagged as a solution)
- `type.summary` – breadth overview

## Namespace: status

- `status.draft` – proposed, awaiting human approval
- `status.approved`
- `status.conflict-review`

## Namespace: domain

- `domain.legal`
    - `domain.legal.compliance`

## Cross-topic mappings (aggregated from `related_topics`)

- `topic.cooking.grilling` ↔ `topic.bbq.equipment`
- `topic.cooking.heat-management` ↔ `topic.bbq.equipment`
