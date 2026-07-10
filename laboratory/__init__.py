"""Finite-domain mechanism-design experiments for ChoreMarket.

Nothing in this package is imported by the deployed web application. The lab
uses one sign convention throughout: a positive transfer is money received by
an agent; a negative transfer is money paid by an agent.
"""

from .domain import ChoreDomain, Outcome, Profile, Type
from .mechanism import Lottery, TabularMechanism

__all__ = ["ChoreDomain", "Lottery", "Outcome", "Profile", "TabularMechanism", "Type"]
