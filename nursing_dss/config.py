"""Central configuration. Every tunable number in the system lives here.

Values marked (ASSUMPTION) are engineering defaults chosen by the dev team and
should be reviewed by a clinical stakeholder before any real-world use.
"""
import os

# ---------------------------------------------------------------- database
# On Vercel the only writable location is /tmp (ephemeral, per serverless instance).
_default_db = "/tmp/nursing_system.db" if os.environ.get("VERCEL") else "nursing_system.db"
DEFAULT_DB_PATH = os.environ.get("NURSING_DB_PATH", _default_db)

# ---------------------------------------------------------------- floor plan
# Where nurses start their rounds, in the same units as Rooms.x_coord/y_coord.
STATION_COORD = (0.0, 5.0)

# ---------------------------------------------------------------- qualifications
QUALIFICATIONS = ("CHARGE", "RN", "RPN", "STUDENT")

# (ASSUMPTION) Highest patient acuity (1-5) each role may be assigned.
MAX_ACUITY_BY_QUALIFICATION = {
    "CHARGE": 5,
    "RN": 5,
    "RPN": 4,
    "STUDENT": 2,
}

# (ASSUMPTION) A nurse flagged `mobility_restricted` may only be given rooms
# that are close to the station AND close to each other (metres / grid units).
MOBILITY_MAX_FROM_STATION = 30.0
MOBILITY_MAX_SPREAD = 20.0

# ---------------------------------------------------------------- optimizer
# cost = W_BALANCE * balance_term + W_DISTANCE * total_walk + W_CONTINUITY * rooms_changed
# "utilization": balance = std-dev of (load / max_workload) in percentage points
# "raw":         balance = std-dev of raw acuity load * 10
BALANCE_MODE = "utilization"
W_BALANCE = 1.0
W_DISTANCE = 0.05
W_CONTINUITY = 2.0
MAX_LOCAL_SEARCH_PASSES = 50

# ---------------------------------------------------------------- call bell routing
# Seconds an alert may stay un-acknowledged before it escalates to the
# closest available nurse.
ACK_TIMEOUT_SECONDS = int(os.environ.get("ACK_TIMEOUT_SECONDS", "60"))
# Maximum number of different nurses one call may be passed through.
MAX_ESCALATION_LEVELS = 3

# Alert status vocabulary (also enforced by a CHECK constraint in the schema).
ALERT_STATUSES = (
    "DISPATCHED",            # delivered to the responsible nurse's device
    "ACKNOWLEDGED",          # nurse acknowledged
    "ESCALATED",             # un-acknowledged; passed on to a backup nurse
    "ESCALATED_UNASSIGNED",  # nobody was assigned to this bed; fallback used
    "DELIVERY_FAILED",       # could not reach this nurse (no device / failure)
    "ESCALATION_FAILED",     # nobody else could be reached either
)
