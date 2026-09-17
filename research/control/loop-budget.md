# Loop budget

Every mission freezes an attempt count, wall-clock timeout per attempt, total
wall-clock budget, objective direction, and optional passing threshold before
the first result is observed. Failed and invalid attempts consume budget.

The local RTX 4060 is used for candidate proposal/critique. Simulation stays on
CPU. Provider/API spend belongs to one-time snapshot preparation, not candidate
iteration. Exhaustion transitions the mission to `paused`.
