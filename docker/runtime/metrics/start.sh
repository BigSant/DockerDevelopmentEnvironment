#!/bin/sh
# The metrics service: the sampler's minute, probe and traffic loops in the background (each restarted when it
# stops), php -S in front serving what they write.
set -u
runtime=/opt/setup/runtime/metrics
( while :; do php "$runtime/sampler.php"; sleep 10; done ) &
( while :; do php "$runtime/sampler.php" --probes; sleep 10; done ) &
( while :; do php "$runtime/sampler.php" --traffic; sleep 10; done ) &
exec php -S 0.0.0.0:9100 "$runtime/index.php"
