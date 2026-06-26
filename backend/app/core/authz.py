"""Pure authorization predicate — the single RBAC decision (B1; INV-2/INV-11).

``permits(role, permission, *, params)`` is the one authorization decision the
API ``require_role`` / route guards and the Decision-Queue ``can_decide`` (B2)
read. It evaluates a (role, permission) pair against the committed RBAC matrix
in ``params.rbac`` — ``permissions`` maps a NAMED PERMISSION → the list of roles
that hold it (permission → roles, see :class:`~app.core.params.Rbac`). The lookup
is therefore the cleanest possible ``role in permissions.get(perm, [])``.

Default-deny is structural: an unknown permission is absent from the map, so the
``get`` falls back to an empty list and no role holds it; a role not listed for a
known permission is likewise denied. An UNKNOWN role (one not in
``params.rbac.roles``) is also denied — it cannot appear in any permission's role
list, so the membership check already returns ``False``. Unlike
:func:`~app.core.program.resolve_program`, this predicate does NOT raise on an
unknown role: it is a yes/no authz gate, and raising would force every call site
into a try/except. The safe, boring choice — deny, never raise — is made here so
callers can treat the result as a plain boolean.

This is part of the deterministic core and stays pure: it imports only the typed
``Params`` from ``app.core.params`` (core→core is fine) and does no I/O — no
repository, adapter, httpx, or settings import (the core-purity test guards this).
"""

from __future__ import annotations

from app.core.params import Params


def permits(role: str, permission: str, *, params: Params) -> bool:
    """Return whether ``role`` holds ``permission`` under the RBAC matrix (B1).

    Default-deny: an unknown permission (absent from ``params.rbac.permissions``)
    grants nobody, and a role not listed for a known permission — including an
    unknown role absent from ``params.rbac.roles`` — is denied. The predicate
    never raises; it returns ``False`` for any pair it does not explicitly allow.

    Args:
        role: The actor's role token (e.g. ``"admin"`` / ``"leader"`` /
            ``"operator"``). An unrecognized role yields ``False``.
        permission: The named permission being checked (e.g.
            ``"decision_queue.view"``). An unknown permission yields ``False``.
        params: Loaded params (§8); supplies the ``rbac`` matrix (INV-11).

    Returns:
        ``True`` iff ``role`` is among the roles that hold ``permission``.
    """
    return role in params.rbac.permissions.get(permission, [])
