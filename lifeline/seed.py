"""Sample data shared by demo.py and the tests.

Hospital names are made up; coordinates are approximate city centres.
Pittsburgh has exactly ONE O+ blood unit (TRG-BLD-1), which is the M0
conflict scenario: two families racing for the last O+ unit.
"""
from __future__ import annotations

from .clock import Clock
from .hospital import Hospital, Location
from .notifier import Notifier
from .registry import Registry
from .resources import BloodUnit, ICUBed, OxygenCylinder

REQUESTERS = {"asha": "asha-pass", "ben": "ben-pass", "chen": "chen-pass", "dana": "dana-pass"}
STAFF = {
    "trg_staff": ("trg-pass", "TRG"),
    "omc_staff": ("omc-pass", "OMC"),
    "lks_staff": ("lks-pass", "LKS"),
}


def _three_rivers() -> Hospital:
    h = Hospital("TRG", "Three Rivers General", Location("Pittsburgh", 40.4406, -79.9959))
    h.add_unit(ICUBed("TRG-ICU-1", "TRG", has_ventilator=True))
    h.add_unit(ICUBed("TRG-ICU-2", "TRG"))
    h.add_unit(OxygenCylinder("TRG-OX-1", "TRG", capacity_litres=680))
    h.add_unit(OxygenCylinder("TRG-OX-2", "TRG", capacity_litres=3400))
    h.add_unit(BloodUnit("TRG-BLD-1", "TRG", blood_group="O+"))   # the last O+ in Pittsburgh
    h.add_unit(BloodUnit("TRG-BLD-2", "TRG", blood_group="A-"))
    h.add_unit(BloodUnit("TRG-BLD-3", "TRG", blood_group="O-"))
    return h


def _oakland() -> Hospital:
    h = Hospital("OMC", "Oakland Medical Center", Location("Pittsburgh", 40.4418, -79.9561))
    h.add_unit(ICUBed("OMC-ICU-1", "OMC", has_ventilator=True))
    h.add_unit(OxygenCylinder("OMC-OX-1", "OMC", capacity_litres=680))
    h.add_unit(BloodUnit("OMC-BLD-1", "OMC", blood_group="B+"))
    h.add_unit(BloodUnit("OMC-BLD-2", "OMC", blood_group="AB+"))
    return h


def _lakeshore() -> Hospital:
    h = Hospital("LKS", "Lakeshore Hospital", Location("Cleveland", 41.4993, -81.6944))
    h.add_unit(ICUBed("LKS-ICU-1", "LKS"))
    h.add_unit(ICUBed("LKS-ICU-2", "LKS", has_ventilator=True))
    h.add_unit(OxygenCylinder("LKS-OX-1", "LKS", capacity_litres=1700))
    h.add_unit(BloodUnit("LKS-BLD-1", "LKS", blood_group="O+"))
    return h


def build_demo_registry(clock: Clock | None = None, notifier: Notifier | None = None) -> Registry:
    """A registry with 3 hospitals, 15 units, 4 requesters and 3 staff accounts."""
    registry = Registry(clock=clock, notifier=notifier)
    for hospital in (_three_rivers(), _oakland(), _lakeshore()):
        registry.add_hospital(hospital)
    for username, password in REQUESTERS.items():
        registry.register_requester(username, password)
    for username, (password, hospital_id) in STAFF.items():
        registry.register_staff(username, password, hospital_id=hospital_id)
    return registry
