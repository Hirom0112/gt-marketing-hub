"""The agnostic enrollment-system (SIS) boundary (ARCHITECTURE/INV-9; MULTI_AGENT_COCKPIT §4).

The reconcile core consumes a normalized :class:`~app.adapters.sis.base.RosterRecord`
only — it never knows which SIS produced it. Impls (a v1 ``SimulatedSISAdapter``
and a future ``LiveSISAdapter``) are M5; M0 ships the interface + record shape +
the ``SIS_MODE`` registry seam.
"""
