# Mounted by the chart at /usr/local/etc/jupyter, a config directory that the
# docker-stacks images search but do not use, so their own /etc/jupyter files
# stay visible. Enables the rucio_token_renewal server extension that is
# mounted next to this file; PYTHONPATH is left alone (images may set it).
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
c.ServerApp.jpserver_extensions.update({"rucio_token_renewal": True})  # noqa: F821
