# Inventory Availability and Allocation Policy

## Availability check
Compare on-hand quantity, reserved quantity, and available-to-promise inventory at the fulfillment center. A positive on-hand count alone does not establish availability for a new order.

## Allocation discrepancy
When a reservation or stock movement conflicts with the order allocation, preserve the order, SKU, fulfillment center, and movement reference. Reconcile the reservation before reallocating stock across fulfillment centers.

## Replenishment
Record supplier and receiving milestones when replenishment delay is suspected. Do not classify a supplier delay without a late or missed inbound commitment.