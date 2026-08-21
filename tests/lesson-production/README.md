# Lesson-production regression suite

CI runs the portable ESP software probes from this directory. `t42.sh` remains as immutable
historical provenance but is not executed because its pre-auth assertions are superseded by the
current fail-closed assignment-console tests. `t54-esp.sh` retains its campaign `SKIP_REGATE`
classification.

Physical H1 playback, socket fault injection, and robot capture remain outside software CI. The
suite does not deploy or enable render, lesson-generation, or cinematic workers.
