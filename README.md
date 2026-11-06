# LifeLine: an emergency resource finder

**95-895 Python Systems Fundamentals · Fall 2026, Mini 2 · Group Project · M1 Domain Model**

| Member | Andrew ID |
|---|---|
| Garima Sethi | gssethi |
| Saumya Rani | srani |
| Marco Li | yongjial |
| Pengfei Xie | pxie2 |

LifeLine lets hospitals publish live stock of ICU beds, oxygen cylinders and blood units (by group). Families search by type and city and place a short **hold** on one specific unit. Hospital staff **confirm** the hold when the patient arrives. If nobody confirms in time, the hold **expires** and the unit returns to the pool.

M1 is the domain model only: plain Python objects, no networking, run from the command line on one machine.

---

## 1. How to run

Requirements: **Python 3.10 or newer**. Only the standard library is used, so there is nothing to install.

```bash
cd LifeLine
python demo.py              # runs the whole story; writes lifeline_state.json
python -m unittest -v       # 28 tests
```

No terminal? In Colab, upload the `LifeLine` folder and run `!cd LifeLine && python demo.py`.

`demo.py` uses a `ManualClock`, so it shows a hold expiring by moving time forward instead of sleeping. Its output is the same every time it runs.

## 2. Project layout

```
LifeLine/
├── demo.py                 runs the story (python demo.py)
├── README.md               this file
├── lifeline/               the domain package (M2 will add server/ and client/ beside it)
│   ├── exceptions.py       LifeLineError and its 15 subclasses
│   ├── resources.py        Unit (ABC) -> ICUBed, OxygenCylinder, BloodUnit; enums
│   ├── hold.py             Hold (@dataclass) and its status life cycle
│   ├── hospital.py         Hospital (owns Units), Location (@dataclass)
│   ├── accounts.py         Account (ABC) -> Requester, HospitalStaff; password hashing
│   ├── registry.py         Registry: owns everything, enforces every rule
│   ├── clock.py            Clock (ABC) -> SystemClock, ManualClock
│   ├── notifier.py         Notifier (ABC) -> PrintNotifier, RecordingNotifier, SilentNotifier
│   ├── events.py           AuditEvent (@dataclass): append-only log
│   ├── storage.py          JsonStore: save/load with atomic os.replace
│   └── seed.py             sample hospitals and accounts for demo and tests
└── tests/
    └── test_lifeline.py    unittest suite
```

## 3. Design

### Shared state and the invariant

The `Registry` holds everything multiple clients will touch in M2 and M3:

- hospitals and their units, each unit `available`, `held` or `confirmed`
- every hold ever made: who, which unit, when it expires, and its status
- accounts, which include each requester's limit of 2 active holds
- an audit log

> **Invariant (from M0):** at any moment, each unit has at most one active hold or confirmation. One unit is never promised to two people.

The invariant is enforced in three places:

1. **`Unit._move_to`** only allows the moves in `_ALLOWED_MOVES`. A unit that is already `held` cannot be marked `held` again.
2. **`Registry._open_hold_by_unit`** maps `unit_id` to the id of its one open hold. A dict key can only appear once.
3. **`Registry.check_invariants()`** checks the whole state: one open hold per unit, unit status matches its holds, the index is in sync, no duplicate unit ids, and nobody is over their hold limit. It runs after every test, after every load (a tampered file is refused), and at the end of the demo. The M3 load script will call it after 20+ concurrent clients.

### The five operations, and who may call them

| # | Operation | Method | Who | Allowed from | Unit becomes |
|---|---|---|---|---|---|
| 1 | Search | `search(type, city, **criteria)` | anyone | — | (no change) |
| 2 | Hold | `place_hold(user, unit_id)` | requester (max 2 active) | unit `available` | `held` |
| 3 | Release | `release_hold(user, hold_id)` | **only the requester who placed it** | hold `active` | `available` |
| 4 | Confirm | `confirm_hold(staff, hold_id)` | **staff of that unit's hospital** | hold `active`, not expired | `confirmed` |
| 5 | Cancel | `cancel(staff, hold_id, reason)` | **staff of that unit's hospital** | hold `active` or `confirmed` | `available` |

Hospital staff also change stock with `add_unit` and `remove_unit`. A unit that is held or confirmed cannot be removed.

### Answers to the TA's M0 feedback

**Release vs Cancel.** These are now two different roles:

- **Release** is the requester saying "we don't need it." It applies only to their own **active** hold.
- **Cancel** is hospital-only. It covers both an active hold (for example, the unit became unusable) and a **confirmation** (for example, the patient was transferred).

A requester cannot cancel and cannot release a confirmed unit. `release_hold` raises `InvalidStateError` ("only the hospital can cancel a confirmation") and `cancel` raises `PermissionDeniedError`. Permission is decided polymorphically: `Account.can_manage(hospital_id)` is always `False` for a `Requester` and `True` only for a `HospitalStaff` member at their own hospital. The Registry never checks which class an account is.

**What expires a hold?** Expiry is checked on access. There is no background thread. Every public Registry method, reads included, does the same four steps:

1. Read the clock **once**.
2. **Expire** every active hold where `now >= expires_at`.
3. Check every rule and raise before changing anything.
4. Change state.

So a hold expires the first time anyone touches the Registry after its deadline. Expiry is treated as another writer, as the TA pointed out. It runs inside the same method call as the operation, so in M3 the same lock covers both.

**What if a hospital confirms at the moment the hold expires?** Expiry and confirm share one `now` inside one call, so this is decided by a rule rather than by timing. The boundary is inclusive: at exactly `expires_at` the hold has expired. Step 2 expires it, and then `confirm_hold` raises `HoldExpiredError`. One second earlier, confirm succeeds. Both cases are tested (`test_confirm_at_exact_expiry_loses_to_expiry` and `test_confirm_one_second_before_expiry_succeeds`), and demo section 9 shows the first. If we add a background sweeper in M3, it must take the same lock as confirm.

### Hold durations (polymorphic)

Each `Unit` subclass decides its own `hold_minutes()`, and the Registry uses `unit.hold_minutes()` without knowing the type:

| Unit | Hold | Reason (our design choice) |
|---|---|---|
| `ICUBed` | 30 min | time to move a critical patient |
| `OxygenCylinder` | 60 min | families often collect cylinders themselves |
| `BloodUnit` | 20 min | the scarcest resource per group; short holds stop hoarding |

Search criteria are polymorphic too. `matches()` understands `ventilator=True` for ICU beds, `min_litres=` for oxygen, and `blood_group=` (exact) or `for_patient=` (red-cell compatible, e.g. O- for anyone) for blood.

### Life cycles

```
Unit:  available ──hold──> held ──confirm──> confirmed
           ^                 │                   │
           └── release / expire / cancel ────────┘ (cancel only)

Hold:  active ──confirm──> confirmed ──cancel (hospital)──> cancelled
         ├──release (requester)──> released
         ├──cancel  (hospital) ──> cancelled
         └──time runs out ───────> expired        (released/cancelled/expired are final)
```

## 4. Class diagram

```
Registry   owns all shared state; every change goes through one of its methods
│          search · place_hold · release_hold · confirm_hold · cancel
│          add_unit · remove_unit · expire_due_holds · check_invariants
│
├─ has many ─► Hospital ─ has a ─► Location            @dataclass(frozen)
│              │  add_unit · remove_unit · find_available · stock_summary
│              └─ has many ─► «ABC» Unit                hold_minutes() · matches() · label()
│                             │                         mark_held/confirmed/available()
│                             ├── ICUBed                30 min · ventilator
│                             ├── OxygenCylinder        60 min · capacity_litres
│                             └── BloodUnit             20 min · BloodGroup (can_donate_to)
│
├─ has many ─► «ABC» Account                            hold_limit() · can_manage()
│              ├── Requester                            limit 2 · manages nothing
│              └── HospitalStaff                        limit 0 · manages own hospital
│
├─ has many ─► Hold                                     @dataclass · move_to() · is_due_to_expire()
├─ has many ─► AuditEvent                               @dataclass(frozen)
├─ has a ────► «ABC» Clock                              now()
│              ├── SystemClock
│              └── ManualClock                          advance() for demo and tests
└─ has a ────► «ABC» Notifier                           notify()
               ├── PrintNotifier
               ├── RecordingNotifier
               └── SilentNotifier

JsonStore ── save(registry) / load() ──► Registry       temp file + os.replace
```

### Exceptions

Every invalid operation raises a subclass of `LifeLineError`. We never return `False` or print an error. `demo.py` catches the base class and prints the specific subclass.

```
LifeLineError
├── NotFoundError ── UnknownHospitalError · UnknownUnitError · UnknownHoldError · UnknownAccountError
├── ValidationError        bad input (unknown blood group, short password, ...)
├── DuplicateError         id / username already taken
├── UnitUnavailableError   unit already held or confirmed   ← the M0 conflict
├── HoldLimitError         requester already has 2 active holds
├── HoldExpiredError       hold timed out before confirm/release
├── InvalidStateError      e.g. releasing a confirmed unit
├── PermissionDeniedError  wrong requester / wrong hospital / requester calling cancel
├── AuthenticationError    same message for unknown user and wrong password
├── PersistenceError       missing or corrupt JSON file
└── InvariantViolationError  state is inconsistent (should never happen)
```

## 5. Concept map

| Concept | Where (file: lines) | Why it is needed here |
|---|---|---|
| **Classes** (3+) | `resources.py`, `hospital.py`, `hold.py`, `accounts.py`, `registry.py`, `clock.py`, `notifier.py`, `events.py`, `storage.py` (20 classes plus enums and exceptions) | Each class is one thing in the domain (a unit, a hospital, a hold, an account) or one job (keeping the rules, telling the time, sending messages, saving). |
| **Data classes** | `hold.py: 46–130` `Hold`; `hospital.py: 19–42` `Location` (frozen); `events.py: 15–47` `AuditEvent` (frozen) | Following "data classes for facts, regular classes for rules": a hold is mostly facts (who, which unit, when it expires). Location and audit events never change after creation, so they are `frozen=True`. Units, hospitals and the Registry are regular classes because they enforce rules. |
| **Abstract base class + polymorphism** | `resources.py: 88–197` `Unit(ABC)` with `@abstractmethod` `resource_type`, `hold_minutes`, `label`, `_extra_fields`, `from_dict`; subclasses `ICUBed 200–248`, `OxygenCylinder 251–300`, `BloodUnit 303–362` | The three resources really behave differently: hold length, search criteria and description. `Registry.place_hold` (`registry.py: 198–238`) sets `expires_at = now + unit.hold_minutes()`, and `Hospital.find_available` (`hospital.py: 107–113`) calls `unit.matches(...)`. Neither checks which subclass it has, so a new resource type is one new subclass with no if/elif to edit. Demo section 1 (`demo.py: 65–69`) loops over all units through the base interface. |
| ABC + polymorphism (2nd family) | `accounts.py: 41–100` `Account(ABC)` → `Requester 103–136`, `HospitalStaff 139–172` | `hold_limit()` (2 vs 0) and `can_manage(hospital_id)` answer the TA's "who may cancel?" question. `Registry._require_manager` (`registry.py: 484–488`) and `place_hold` ask the account and never check its type. |
| ABC + polymorphism (3rd and 4th) | `clock.py: 21–58` `Clock` → `SystemClock`, `ManualClock`; `notifier.py: 19–54` `Notifier` → `PrintNotifier`, `RecordingNotifier`, `SilentNotifier` | The Registry *has a* clock and a notifier and relies only on `now()` and `notify()`. The demo and tests swap in `ManualClock` (expiry without sleeping) and `RecordingNotifier` (no real messages sent during tests), the Lecture 5 Notifier pattern. |
| **Inheritance + `super()`** | `resources.py: 230–240, 283–292, 333–349` each `matches()` override calls `super().matches()`; every subclass `__init__` calls `super().__init__` | Each subclass reuses the shared type check and adds only its own criterion: override one rule and reuse the rest. |
| **Composition** | `registry.py: 68–81` Registry owns `_hospitals`, `_accounts`, `_holds`, `_open_hold_by_unit`, `_events`, plus a `Clock` and a `Notifier`; `hospital.py: 45–156` Hospital owns `_units` | Registry is the single owner of the shared state, and the M2 server will own exactly this object. Hospital is the only place units are added or removed, and it refuses to remove a promised unit (`hospital.py: 83–91`). `units()` returns a copy, so no caller can change the private dict. |
| **Encapsulated state changes (invariant)** | `registry.py: 198–302` (place_hold, release_hold, confirm_hold, cancel); `registry.py: 505–518` `_close_hold`; `resources.py: 133–148` `Unit._move_to`/`mark_*`; `hold.py: 84–99` `Hold.move_to` | Every change goes through a Registry method that expires due holds, checks every rule, and **raises before changing anything** (Lecture 5). Status has no setter: units and holds refuse illegal moves themselves. That is what makes the M0 conflict (two families, last O+ unit) end in a `UnitUnavailableError` for the second family. |
| **Invariant check** | `registry.py: 325–382` `check_invariants()`; called in `registry.py: 436` after loading | Checks the M0 invariant directly, plus everything that must agree with it. The M3 load script will call the same method. |
| **Custom exceptions** | `exceptions.py: 27–92` (`LifeLineError` + 15 subclasses) | A failed hold says *why*: full (`UnitUnavailableError`), limit (`HoldLimitError`), too late (`HoldExpiredError`), not yours (`PermissionDeniedError`). Callers can catch one problem or all of them. In M2 each will map to an error response. |
| **File persistence (JSON)** | `storage.py: 35–60` `JsonStore.save/load`; `to_dict/from_dict` on every class (e.g. `registry.py: 392–437`); `resources.py: 366–379` `UNIT_TYPES` + `unit_from_dict` | Each object saves its own `type` or `role` and loads back through its own `from_dict`. Saves are atomic (temp file + `os.replace`, `storage.py: 44`) so a crash never leaves a half-written file. Loading re-checks the invariant, so a tampered file that promises one unit twice is refused. |
| **Runnable demo** | `demo.py` (`python demo.py`); `attempt()` at `demo.py: 32–40` | 14 sections covering all five operations, expiry, save/load and **15 rejected operations**, each printed with its exception class. |
| *Optional:* properties, `__repr__`, `__eq__` | read-only `@property` throughout (e.g. `resources.py: 115–131`); `__repr__`/`__eq__` on `Unit`, `Hospital`, `Account`, `Registry` | Properties without setters stop outside code from changing status. `__eq__` lets the demo and tests check `loaded == registry` after a JSON round trip. `Account.__repr__` never shows the password hash. |
| *Optional:* type hints | every function and method | They make the interfaces in this README checkable and are easier to read in the check-in. |
| *Optional:* class diagram | section 4 above | |
| Password hashing | `accounts.py: 32–38`, `56–62` | The proposal promised hashed passwords. We use salted PBKDF2-SHA256 and `hmac.compare_digest`. The plain password is never stored (`test_password_is_not_stored_in_plain_text`). |

## 6. What the demo shows

| Section | Shows | Rejected with |
|---|---|---|
| 1 | Polymorphism: 3 unit kinds, 3 hold lengths | — |
| 2 | Log in | `AuthenticationError` |
| 3 | Search by type, city, ventilator, blood group, compatibility | `ValidationError` (unknown type) |
| 4 | **M0 conflict**: asha and ben want the last O+ unit | `UnitUnavailableError` |
| 5 | Max 2 active holds; staff can't hold | `HoldLimitError`, `PermissionDeniedError` |
| 6 | Release (owner only) | `PermissionDeniedError` |
| 7 | Confirm (own hospital only); requester can't undo a confirmation | `PermissionDeniedError` ×2, `InvalidStateError` |
| 8 | Stock changes | `UnitUnavailableError`, `PermissionDeniedError` |
| 9 | Expiry; **confirm at the exact expiry time** | `HoldExpiredError` |
| 10 | Hospital cancels a confirmation | `InvalidStateError` (cancel twice) |
| 11 | `check_invariants()` passes | — |
| 12 | Save, load, `loaded == registry`, rules still apply after load | `UnitUnavailableError`, `PersistenceError` |
| 13 | Live stock counts per hospital (data for the stretch-goal map) | — |
| 14 | Audit log | — |

## 7. Tests

`python -m unittest -v` runs 28 tests. After **every** test, `tearDown` calls `check_invariants()`. Highlights:

- `test_many_requesters_one_unit_exactly_one_wins`: the TA's suggested test. 20 requesters try to hold one unit: exactly 1 succeeds and 19 get `UnitUnavailableError`. This is the sequential version; M3 runs it with 20+ threads.
- Expiry boundary tests (see section 3).
- `test_tampered_file_breaking_the_invariant_is_refused`
- Abstract classes can't be created; each role's permissions; blood compatibility; password not stored.

## 8. Looking ahead

- **M2:** the `Registry` moves onto the server unchanged. Each request becomes one Registry call. Every `LifeLineError` subclass becomes an error response, and `to_dict()` is already the JSON format. `JsonStore.save` runs after every successful change.
- **M3:** one `threading.Lock` wraps each public Registry method. Because every method already does "expire, check, change" in one call, the lock makes all three atomic, including expiry against confirm. `save()` already uses a temp file and `os.replace`.

## 9. Known limitations

- Single process, no concurrency yet. That is M3's job, and nothing is thread-safe until then.
- Expiry only happens when someone calls the Registry. That is correct for every reader, but notifications for expired holds are delayed until the next call.
- `Registry.hospitals()` returns the real Hospital objects, so code that bypasses the Registry could still call `hospital.add_unit`. `check_invariants()` would catch the inconsistency.
- Hold lengths are design choices, not clinical guidance. Hospital names in `seed.py` are made up.

## 10. Who built what

| Member | Built | Concepts they explain at check-in |
|---|---|---|
| Garima Sethi | `resources.py` (Unit ABC, 3 subclasses, BloodGroup) | ABC + polymorphism, inheritance/`super()` |
| Saumya Rani | `registry.py`, `exceptions.py` | composition, encapsulated state changes, invariant, custom exceptions, expiry |
| Marco Li | `hospital.py`, `hold.py`, `accounts.py`, `events.py` | data classes, Hospital composition, permissions, password hashing |
| Pengfei Xie | `storage.py`, `clock.py`, `notifier.py`, `seed.py`, `demo.py`, `tests/`, README | JSON persistence, runnable demo, tests, concept map |

## 11. Use of AI tools

An initial version of this code and README was drafted with Claude (Anthropic), following the course policy. The team reviewed, ran and tested every file, and each member can explain the parts listed in section 10.
