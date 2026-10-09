"""The ONE function that reads the Model out of a member's dashboard package.

A v3 dashboard is an artifact of ``kind="dashboard"`` holding a Model (the data types
and field shapes), a View, a theme and ``bound_to``. ``dashboard_write`` validates an
agent's value against the Model **inside that current package** -- never against a
builtin template manifest, which is what it did while templates were the only source.

THIS MODULE IS THE WHOLE SEAM. :func:`read_package_model` is the only place that knows
where a Model comes from; the write path calls it and nothing else. When the package
artifact itself lands (CREW-21656, owned separately), only this function's BODY
changes -- its signature, its four states and the refusal each maps to are settled in
``INTERFACE.md`` and consumed by :mod:`kiro_crew.dashboard_agentic`.

Why a state and not an exception for a content problem
------------------------------------------------------
Every refusal on the write path is RECORDED to the crewmate's mistake book before the
response is sent, which is the only reason its next cycle is cheaper than this one. An
exception raised from in here would escape that recording and become a 500: the agent
would learn nothing, and a person would be shown a server error for the ordinary fact
that no package exists yet. So "there is no package", "the Model does not parse" and
"this is not yours" are RETURNED, each as its own state, and only a failure that is
not about content -- the store refusing a path, a read that errors -- raises.

That is also exactly how :mod:`kiro_crew.dashboard_templates.instance` answers for a
stored template: a record that does not parse comes back as ``error`` with a sentence,
never as a raise. One posture, two readers.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Final

from kiro_crew.dashboard_agentic import Instance

logger = logging.getLogger(__name__)

__all__ = [
    "PACKAGE_STATES",
    "STATE_EMPTY",
    "STATE_ERROR",
    "STATE_FORBIDDEN",
    "STATE_LIVE",
    "PackageRead",
    "PackageReadError",
    "read_package_model",
]

#: The package holds a Model this write can be checked against.
STATE_LIVE: Final[str] = "live"
#: No dashboard package is bound to this member yet.
STATE_EMPTY: Final[str] = "empty"
#: A package is there and its Model does not parse.
STATE_ERROR: Final[str] = "error"
#: A package is there and this caller may not read it.
#:
#: Its OWN state rather than folded into :data:`STATE_EMPTY`, because the two need
#: opposite sentences: ``empty`` tells an agent to create a package, and saying that to
#: an agent reaching another member's package sends it to do the wrong thing on every
#: cycle forever.
STATE_FORBIDDEN: Final[str] = "forbidden"

#: Every state :func:`read_package_model` can answer with.
#:
#: Pinned against the refusal table in :data:`dashboard_agentic.PACKAGE_REFUSALS` by
#: test, because the mapping there is keyed by these STRINGS -- the write path must not
#: import this module (the Model type travels the other way), so the vocabulary is
#: shared as values and the pin is what keeps the two from drifting. A state added here
#: with no refusal beside it would be reported to an agent as a bare "no package".
#:
#: ``stale`` is deliberately absent. On a template it means "the registry moved past
#: this copy", and a package has no registry behind it to move.
PACKAGE_STATES: Final[tuple[str, ...]] = (STATE_LIVE, STATE_EMPTY, STATE_ERROR, STATE_FORBIDDEN)


class PackageReadError(Exception):
    """The package could not be read at all. IO, never content.

    Distinct from every state above so the caller can separate "I read it, and here is
    what it says" from "I could not read it", which are a 400 and a 500.
    """


@dataclass(frozen=True)
class PackageRead:
    """What the write path is told about a member's current dashboard package."""

    #: The Model as the write path needs it, or ``None`` for every non-live state.
    #:
    #: :class:`~kiro_crew.dashboard_agentic.Instance` is REUSED rather than a new type
    #: minted beside it, and that reuse is what keeps this seam one function:
    #: ``check_write`` already accepts exactly this narrow view -- the field specs plus
    #: the version a write is stamped with -- so it is not touched by the move off
    #: templates. ``instance_version`` carries the PACKAGE's version here.
    model: Instance | None
    #: One of :data:`PACKAGE_STATES`.
    state: str
    #: One sentence a person can be shown, and the one the refusal quotes.
    state_reason: str


def read_package_model(slug: str, member: str) -> PackageRead:
    """The Model inside *slug*'s current dashboard package.

    STUB BODY. The signature, the four states and the sentence are what callers depend
    on and they are final; where the Model is read from is not, and this reads it from
    the template instance store so the seam is exercised end to end and today's
    behaviour does not regress while the package artifact is built.

    The stub's own mapping, which is ``read_instance``'s existing rule verbatim:

    * ``live`` / ``stale`` resolve to the stored copy's manifest -- a stale copy has a
      complete manifest and renders, and a write is checked against the same manifest
      the page is drawn from;
    * ``empty`` resolves to the DEFAULT template's manifest, because that is the page
      the crewmate is actually shown and validating against it is what makes a written
      value appear where the reader is looking;
    * ``error`` is the one state where no manifest parses, so there is nothing to
      validate against;
    * no instance AND no default in the registry answers :data:`STATE_EMPTY`.

    :data:`STATE_FORBIDDEN` is UNREACHABLE from the stub, and that is a fact about the
    stub rather than about the contract: the instance store is keyed by the member's own
    slug and has no per-artifact owner check to fail. The branch, its refusal and its
    test exist so the real body only has to return the value.

    An absent registry is NOT one of the states and is not caught: the registry ships in
    the same package as this module, so a build without it is a packaging fault, and
    reporting it as ``empty`` would send every crewmate to create a package through a
    store the build does not have.
    """
    # Resolved through the modules rather than imported at the top, which keeps the
    # boot-path rule the dashboard handlers follow and lets a test replace one.
    from kiro_crew.dashboard_templates import instance as instance_store
    from kiro_crew.dashboard_templates.manifest import ManifestError, parse_manifest

    try:
        record = instance_store.read(slug)
    except Exception as exc:
        # A read that FAILED, which is the one thing that is not a state: the record
        # could not be opened, so nothing is known about the Model either way. Caught
        # broadly rather than on the store's own error type, because a stub store put
        # in its place has no such type and a real one's ``read`` touches the file
        # system -- and the ANSWER is the same either way.
        raise PackageReadError(f"the dashboard package for {slug!r} cannot be read: {exc}") from exc
    # Read DEFENSIVELY off the store, each name with the literal it stands for as its
    # default, which is what ``read_instance`` did before this and for the same reason:
    # this module resolves the store at call time so a narrower stand-in can be put in
    # its place, and an attribute lookup that raised would turn a replaced store into
    # a 500.
    live = getattr(instance_store, "STATE_LIVE", "live")
    stale = getattr(instance_store, "STATE_STALE", "stale")
    empty = getattr(instance_store, "STATE_EMPTY", "empty")
    state = getattr(record, "state", "")
    reason = str(getattr(record, "state_reason", "") or "")
    if state == empty:
        try:
            fallback = instance_store.default_instance(slug)
        except Exception:
            logger.warning(
                "no default dashboard package for %s to write against", slug, exc_info=True
            )
            fallback = None
        if fallback is None:
            # ``default_instance`` ANSWERS None when the registry ships no default
            # rather than raising, so this covers both halves.
            return PackageRead(None, STATE_EMPTY, f"{member or slug} has no dashboard package yet")
        record = fallback
        reason = str(getattr(record, "state_reason", "") or "")
    elif state not in (live, stale):
        # ``error`` lands here, and so does a state this reader does not recognise.
        # BOTH are STATE_ERROR rather than STATE_EMPTY: a package the store can see
        # and this reader cannot interpret is present, and telling the agent to create
        # one would be the wrong instruction on every cycle.
        return PackageRead(
            None, STATE_ERROR, reason or f"this dashboard package is in state {state!r}"
        )
    try:
        manifest = parse_manifest(dict(record.manifest))
    except (ManifestError, TypeError, ValueError) as exc:
        # The copy loaded for the store and does not parse for this reader, which is a
        # content problem and so a state. Its own problems are the sentence, because an
        # agent told "the Model is broken" with no detail has nothing to act on.
        return PackageRead(
            None, STATE_ERROR, f"this dashboard package's Model does not load: {exc}"
        )
    return PackageRead(
        Instance(manifest=manifest, instance_version=int(getattr(record, "instance_version", 0))),
        STATE_LIVE,
        reason,
    )
