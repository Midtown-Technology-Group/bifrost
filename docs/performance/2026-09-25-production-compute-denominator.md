# Issue 890 production compute denominator, 2026-09-25

Azure Resource Graph `resourcechanges` for `asp-mtg-bifrost-production` records these `sku.capacity` transitions on September 25 UTC: 1→2 at 04:33:15, 2→1 at 04:48:21, and 1→2 at 05:08:39. There were no plan-capacity changes in the returned history between September 20 and the first transition. The [read-only change extract](results/2026-09-25-production-b3-capacity-changes.json) retains the timestamps and old/new values without caller identities. The current plan readback is two B3 instances.

The [production execution sample](results/2026-09-25-production-execution-shape.json) requested creations from 2026-09-24 04:00 UTC and included records through 2026-09-25 04:06:45 UTC. It contains 3,069 successful executions. The capacity history establishes **one B3 instance during that sample**. Using the 24.11 hours from the requested start through the latest included creation gives approximately **127 observed successful executions per B3-hour**. The query completion time was not recorded, so 24.11 hours is a lower bound on the observation window and 127 is an approximate upper bound on its rate. This is observed production use, not a saturation capacity or a forecast for the current two-instance configuration.

Readback commands:

```bash
az appservice plan list --query "[?name=='asp-mtg-bifrost-production'].{name:name,capacity:sku.capacity}" -o json
az graph query -q "resourcechanges | where tostring(properties.targetResourceId) contains 'asp-mtg-bifrost-production' | extend changeTime=todatetime(properties.changeAttributes.timestamp), cap=properties.changes['sku.capacity'] | where isnotempty(cap) | project changeTime, previous=tostring(cap.previousValue), next=tostring(cap.newValue) | order by changeTime asc" --query data -o json
```
