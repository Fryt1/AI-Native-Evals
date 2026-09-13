# Build a Blender scene

You have a running Blender instance reachable through the configured Blender MCP
tools. Use those tools for every change to the scene — do not drive Blender by
shelling out to `blender --background`, and do not write a custom socket client.

## What to build

Starting from the scene as you find it, create exactly these three objects:

| Object name  | Kind   | Location (x, y, z) |
| ------------ | ------ | ------------------ |
| `EvalTable`  | Mesh   | 0.0, 0.0, 0.75     |
| `EvalLamp`   | Light  | 2.0, 0.0, 3.0      |
| `EvalCamera` | Camera | 6.0, -6.0, 4.0     |

Names must match exactly — they are how the scene is read back afterwards.
Locations are each object's world-space origin, in metres.

You may shape the mesh however you like; a default cube scaled to a table-like
proportion is fine. What matters is that the three objects exist with the right
names, the right kinds, and their origins at the stated positions.

The scene starts with Blender's default objects. They are not part of the
result: remove anything you are not asked to create, including the defaults and
any scratch object you make while working. The finished scene should contain the
three objects above and nothing else.

## Before you report completion

Read the scene back through the MCP tools and confirm the three objects exist
with the expected names, kinds and positions, and that nothing else is left in
the scene. Report what you actually observed. If something does not match, say
so rather than reporting success.
