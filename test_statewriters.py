"""THE GATE'S COPY OF THE STATE-WRITER LINTER: runs the R1/R2/R3
census (unregistered writers, non-atomic saves, resurrection shapes)
in-process and exits with its verdict — the commit gate parses the
RESULT line. The linter module itself resolves through PYTHONPATH (the
real repo's reviewed copy); under the gate it censuses that tree, which
is exactly the contract: an unregistered or raw writer in the fleet
blocks the commit until it joins the registry."""
import sys

import diag_statewriters as ds

sys.exit(ds.main())
