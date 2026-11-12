import os
import sys

# .../collections/ansible_collections/incidentsim/verify/tests/unit -> .../collections
COLLECTIONS = os.path.abspath(os.path.join(os.path.dirname(__file__), *([".."] * 5)))
if COLLECTIONS not in sys.path:
    sys.path.insert(0, COLLECTIONS)
