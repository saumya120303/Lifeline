"""Unit tests for the LifeLine domain model. Run: python -m unittest -v"""
from __future__ import annotations

import json
import tempfile
import unittest
from datetime import datetime, timezone
from pathlib import Path

from lifeline import (
    Account,
    AuthenticationError,
    BloodGroup,
    BloodUnit,
    Clock,
    HoldExpiredError,
    HoldLimitError,
    HoldStatus,
    InvalidStateError,
    InvariantViolationError,
    JsonStore,
    LifeLineError,
    ManualClock,
    Notifier,
    PermissionDeniedError,
    PersistenceError,
    RecordingNotifier,
    Unit,
    UnitStatus,
    UnitUnavailableError,
    ValidationError,
)
from lifeline.seed import build_demo_registry

START = datetime(2026, 11, 8, 14, 0, tzinfo=timezone.utc)


class LifeLineTestCase(unittest.TestCase):
    def setUp(self) -> None:
        self.clock = ManualClock(START)
        self.notifier = RecordingNotifier()
        self.registry = build_demo_registry(clock=self.clock, notifier=self.notifier)

    def tearDown(self) -> None:
        # Whatever a test did, the invariant must still hold afterwards.
        self.registry.check_invariants()


class TestAbstractClasses(LifeLineTestCase):
    def test_abstract_base_classes_cannot_be_created(self) -> None:
        for abstract in (Unit, Account, Notifier, Clock):
            with self.subTest(abstract=abstract.__name__):
                with self.assertRaises(TypeError):
                    abstract()  # type: ignore[abstract]

    def test_each_unit_kind_sets_its_own_hold_time(self) -> None:
        minutes = {u.unit_id: u.hold_minutes() for u in self.registry.get_hospital("TRG").units()}
        self.assertEqual(minutes["TRG-ICU-1"], 30)
        self.assertEqual(minutes["TRG-OX-1"], 60)
        self.assertEqual(minutes["TRG-BLD-1"], 20)

    def test_hold_expiry_time_comes_from_the_unit(self) -> None:
        hold = self.registry.place_hold("asha", "TRG-BLD-1")
        self.assertEqual((hold.expires_at - hold.created_at).total_seconds(), 20 * 60)


class TestConflictAndInvariant(LifeLineTestCase):
    def test_second_hold_on_same_unit_is_rejected(self) -> None:
        self.registry.place_hold("asha", "TRG-BLD-1")
        with self.assertRaises(UnitUnavailableError):
            self.registry.place_hold("ben", "TRG-BLD-1")

    def test_many_requesters_one_unit_exactly_one_wins(self) -> None:
        """TA's suggested test (sequential version; M3 repeats it with threads)."""
        for i in range(20):
            self.registry.register_requester(f"family{i:02d}", "secret-pass")
        wins, rejected = 0, 0
        for i in range(20):
            try:
                self.registry.place_hold(f"family{i:02d}", "TRG-BLD-1")
                wins += 1
            except UnitUnavailableError:
                rejected += 1
        self.assertEqual((wins, rejected), (1, 19))
        self.assertEqual(self.registry.get_hospital("TRG").get_unit("TRG-BLD-1").status,
                         UnitStatus.HELD)

    def test_invariant_check_catches_a_broken_unit(self) -> None:
        unit = self.registry.get_hospital("TRG").get_unit("TRG-ICU-2")
        unit.mark_held()  # bypassing the Registry on purpose
        with self.assertRaises(InvariantViolationError):
            self.registry.check_invariants()
        unit.mark_available()  # restore so tearDown passes

    def test_unit_refuses_illegal_status_moves(self) -> None:
        unit = BloodUnit("X-1", "TRG", blood_group="O-")
        unit.mark_held()
        with self.assertRaises(InvalidStateError):
            unit.mark_held()


class TestHoldLimit(LifeLineTestCase):
    def test_third_active_hold_is_rejected(self) -> None:
        self.registry.place_hold("asha", "TRG-BLD-1")
        self.registry.place_hold("asha", "TRG-ICU-1")
        with self.assertRaises(HoldLimitError):
            self.registry.place_hold("asha", "OMC-ICU-1")

    def test_confirmed_holds_do_not_count_toward_the_limit(self) -> None:
        first = self.registry.place_hold("asha", "TRG-BLD-1")
        self.registry.place_hold("asha", "TRG-ICU-1")
        self.registry.confirm_hold("trg_staff", first.hold_id)
        self.registry.place_hold("asha", "OMC-ICU-1")  # allowed now
        self.assertEqual(self.registry.active_hold_count("asha"), 2)

    def test_staff_cannot_place_holds(self) -> None:
        with self.assertRaises(PermissionDeniedError):
            self.registry.place_hold("trg_staff", "TRG-ICU-1")


class TestReleaseConfirmCancel(LifeLineTestCase):
    def test_only_owner_can_release(self) -> None:
        hold = self.registry.place_hold("asha", "TRG-BLD-1")
        with self.assertRaises(PermissionDeniedError):
            self.registry.release_hold("ben", hold.hold_id)
        self.registry.release_hold("asha", hold.hold_id)
        self.assertTrue(self.registry.get_hospital("TRG").get_unit("TRG-BLD-1").is_available)

    def test_only_staff_of_that_hospital_can_confirm(self) -> None:
        hold = self.registry.place_hold("asha", "TRG-BLD-1")
        for wrong in ("omc_staff", "ben", "asha"):
            with self.subTest(caller=wrong), self.assertRaises(PermissionDeniedError):
                self.registry.confirm_hold(wrong, hold.hold_id)
        self.registry.confirm_hold("trg_staff", hold.hold_id)
        self.assertEqual(self.registry.get_hold(hold.hold_id).status, HoldStatus.CONFIRMED)

    def test_requester_cannot_release_or_cancel_a_confirmation(self) -> None:
        hold = self.registry.place_hold("asha", "TRG-BLD-1")
        self.registry.confirm_hold("trg_staff", hold.hold_id)
        with self.assertRaises(InvalidStateError):
            self.registry.release_hold("asha", hold.hold_id)
        with self.assertRaises(PermissionDeniedError):
            self.registry.cancel("asha", hold.hold_id)

    def test_hospital_can_cancel_a_confirmation(self) -> None:
        hold = self.registry.place_hold("asha", "TRG-BLD-1")
        self.registry.confirm_hold("trg_staff", hold.hold_id)
        self.registry.cancel("trg_staff", hold.hold_id, "patient transferred")
        self.assertEqual(self.registry.get_hold(hold.hold_id).status, HoldStatus.CANCELLED)
        self.assertEqual(len(self.registry.search("blood_unit", "Pittsburgh", blood_group="O+")), 1)
        self.assertIn("patient transferred", self.notifier.messages_for("asha")[-1])

    def test_released_hold_cannot_be_confirmed(self) -> None:
        hold = self.registry.place_hold("asha", "TRG-BLD-1")
        self.registry.release_hold("asha", hold.hold_id)
        with self.assertRaises(InvalidStateError):
            self.registry.confirm_hold("trg_staff", hold.hold_id)


class TestExpiry(LifeLineTestCase):
    def test_confirm_one_second_before_expiry_succeeds(self) -> None:
        hold = self.registry.place_hold("asha", "TRG-BLD-1")  # 20 min
        self.clock.advance(minutes=19, seconds=59)
        self.registry.confirm_hold("trg_staff", hold.hold_id)

    def test_confirm_at_exact_expiry_loses_to_expiry(self) -> None:
        hold = self.registry.place_hold("asha", "TRG-BLD-1")
        self.clock.advance(minutes=20)
        with self.assertRaises(HoldExpiredError):
            self.registry.confirm_hold("trg_staff", hold.hold_id)
        self.assertEqual(self.registry.get_hold(hold.hold_id).status, HoldStatus.EXPIRED)

    def test_expired_unit_returns_to_pool_and_frees_the_slot(self) -> None:
        self.registry.place_hold("asha", "TRG-BLD-1")
        self.registry.place_hold("asha", "TRG-ICU-1")
        self.clock.advance(minutes=31)  # both expired
        self.assertEqual(self.registry.active_hold_count("asha"), 0)
        self.registry.place_hold("ben", "TRG-BLD-1")

    def test_any_call_triggers_expiry(self) -> None:
        hold = self.registry.place_hold("asha", "TRG-BLD-1")
        self.clock.advance(minutes=25)
        self.registry.search("icu_bed")  # unrelated read
        self.assertEqual(self.registry.get_hold(hold.hold_id).status, HoldStatus.EXPIRED)


class TestStockAndSearch(LifeLineTestCase):
    def test_cannot_remove_a_held_unit(self) -> None:
        self.registry.place_hold("asha", "TRG-OX-1")
        with self.assertRaises(UnitUnavailableError):
            self.registry.remove_unit("trg_staff", "TRG-OX-1")

    def test_blood_compatibility(self) -> None:
        self.assertTrue(BloodGroup.O_NEG.can_donate_to(BloodGroup.AB_POS))
        self.assertTrue(BloodGroup.A_NEG.can_donate_to(BloodGroup.A_POS))
        self.assertFalse(BloodGroup.A_POS.can_donate_to(BloodGroup.A_NEG))
        self.assertFalse(BloodGroup.B_POS.can_donate_to(BloodGroup.A_POS))
        self.assertTrue(all(g.can_donate_to(BloodGroup.AB_POS) for g in BloodGroup))

    def test_bad_input_raises_validation_error(self) -> None:
        with self.assertRaises(ValidationError):
            self.registry.search("helicopter")
        with self.assertRaises(ValidationError):
            BloodUnit("X", "TRG", blood_group="Z+")
        with self.assertRaises(ValidationError):
            self.registry.register_requester("eve", "123")

    def test_all_errors_share_one_base_class(self) -> None:
        with self.assertRaises(LifeLineError):
            self.registry.place_hold("nobody", "TRG-BLD-1")


class TestAccounts(LifeLineTestCase):
    def test_password_is_not_stored_in_plain_text(self) -> None:
        saved = json.dumps(self.registry.to_dict())
        self.assertNotIn("asha-pass", saved)

    def test_authentication(self) -> None:
        self.assertEqual(self.registry.authenticate("asha", "asha-pass").username, "asha")
        with self.assertRaises(AuthenticationError):
            self.registry.authenticate("asha", "wrong-pass")
        with self.assertRaises(AuthenticationError):
            self.registry.authenticate("nobody", "whatever")


class TestPersistence(LifeLineTestCase):
    def setUp(self) -> None:
        super().setUp()
        self.tmp = tempfile.TemporaryDirectory()
        self.path = Path(self.tmp.name) / "state.json"

    def tearDown(self) -> None:
        super().tearDown()
        self.tmp.cleanup()

    def test_round_trip_keeps_everything(self) -> None:
        held = self.registry.place_hold("asha", "TRG-BLD-1")
        confirmed = self.registry.place_hold("ben", "OMC-ICU-1")
        self.registry.confirm_hold("omc_staff", confirmed.hold_id)
        JsonStore(self.path).save(self.registry)
        loaded = JsonStore(self.path).load(clock=self.clock)
        self.assertEqual(loaded, self.registry)
        with self.assertRaises(UnitUnavailableError):  # rules still apply after loading
            loaded.place_hold("chen", held.unit_id)
        self.assertEqual(loaded.place_hold("chen", "TRG-ICU-1").hold_id, "H0003")

    def test_tampered_file_breaking_the_invariant_is_refused(self) -> None:
        hold = self.registry.place_hold("asha", "TRG-BLD-1")
        data = self.registry.to_dict()
        twin = dict(data["holds"][0], hold_id="H9999", requester="ben")
        data["holds"].append(twin)  # the same unit promised twice
        self.path.write_text(json.dumps(data))
        with self.assertRaises(InvariantViolationError):
            JsonStore(self.path).load()
        self.assertEqual(hold.unit_id, twin["unit_id"])

    def test_missing_or_corrupt_file(self) -> None:
        with self.assertRaises(PersistenceError):
            JsonStore(self.path).load()
        self.path.write_text("not json")
        with self.assertRaises(PersistenceError):
            JsonStore(self.path).load()


if __name__ == "__main__":
    unittest.main()
