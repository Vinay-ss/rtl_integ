"""Allow ``python -m pyverilog_auto`` to run the CLI."""
import sys
from .cli import main

sys.exit(main())
