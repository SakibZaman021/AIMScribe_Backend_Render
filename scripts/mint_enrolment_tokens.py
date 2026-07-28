"""
Mint enrolment tokens for a fleet of doctor PCs.

    python scripts/mint_enrolment_tokens.py doctors.csv

Reads a CSV of the machines to enrol, creates any hospital that does not exist,
mints one single-use token per PC, and writes a folder of one-page instructions
- one file per machine, plus a register of what was issued.

    hospital_id,hospital_name,doctor_id,doctor_name,room
    HOSP001,Square Hospital,DR001,Dr Sakib Zaman,Room 3
    HOSP001,Square Hospital,DR002,Dr Ayesha Rahman,Room 4
    HOSP002,United Hospital,DR003,Dr Kamrul Hasan,OPD 2

One token per PC, not per doctor: the token is consumed at enrolment, and it is
what binds that machine to a hospital. A doctor with two machines needs two rows.

Every doctor named in the CSV is also added to their hospital's register, which
is what lets them be picked in CMED. A consulting room is shared, so any doctor
registered at that hospital can record on any of its PCs - the doctor named on
the row is only that machine's default.

The tokens are credentials. The output folder is written with no world access
and should be deleted once the machines are installed; a token is useless after
it is used, and expires anyway.

Environment:
    AIMS_BACKEND_URL   defaults to the production backend
    AIMS_ADMIN_KEY     required; the same value set in Render
"""
from __future__ import annotations

import csv
import json
import os
import sys
import urllib.error
import urllib.request
from datetime import datetime, timedelta, timezone
from pathlib import Path

BACKEND = os.getenv("AIMS_BACKEND_URL",
                    "https://aimscribe-backend-render.onrender.com").rstrip("/")
TTL_HOURS = int(os.getenv("AIMS_TOKEN_TTL_HOURS", "72"))
CREATED_BY = os.getenv("AIMS_CREATED_BY", "Team_AIMScribe")


def admin_key() -> str:
    key = os.getenv("AIMS_ADMIN_KEY", "")
    if key:
        return key
    # Fall back to the local .env so this can be run from a checkout without
    # exporting the key into a shell history.
    env_file = Path(__file__).resolve().parent.parent / ".env"
    if env_file.is_file():
        for line in env_file.read_text(encoding="utf-8").splitlines():
            if line.startswith("AIMS_ADMIN_KEY="):
                return line.split("=", 1)[1].strip()
    return ""


def call(path: str, body: dict, key: str):
    request = urllib.request.Request(
        BACKEND + path, data=json.dumps(body).encode(),
        headers={"Content-Type": "application/json", "X-Admin-Key": key})
    try:
        with urllib.request.urlopen(request, timeout=60) as response:
            return response.status, json.loads(response.read())
    except urllib.error.HTTPError as exc:
        return exc.code, exc.read().decode(errors="replace")
    except Exception as exc:                      # network, DNS, TLS
        return 0, str(exc)


def instructions(row: dict, token: str, expires: datetime) -> str:
    return f"""AIMScribe installation - {row['doctor_name']} ({row['doctor_id']})
{'=' * 62}

  Hospital   {row['hospital_name']} ({row['hospital_id']})
  Doctor     {row['doctor_name']} ({row['doctor_id']})
  Room       {row.get('room') or '-'}

  This token is for ONE PC. It is consumed on first use, so it cannot be
  used to set up a second machine.

  Valid until {expires:%d %B %Y, %H:%M} UTC.


ON THAT DOCTOR'S PC
{'-' * 62}

  1. Copy AIMScribeSetup.exe to the machine and run it.
     Windows will ask for administrator permission - that is expected;
     the agent installs to Program Files and registers a startup task.

  2. Three fields appear. The first two are already filled in:

        Backend URL        {BACKEND}
        CMED web address   https://aim-scribe-exe.vercel.app

     Paste the token into the third:

        Enrolment token    {token}

  3. Install. When it finishes it should say the PC is ready to record.

  4. Open https://aim-scribe-exe.vercel.app in the browser on that PC.
     The page should show {row['hospital_id']}, and offer {row['doctor_name']}
     in the doctor list with {row['doctor_id']} already selected.


WHEN A DIFFERENT DOCTOR USES THIS PC
{'-' * 62}

  Nothing needs reinstalling. The consulting room belongs to the hospital,
  not to one doctor - so whoever is seeing the patient picks their own name
  from the list on the page before pressing Start, and the consultation is
  filed under them.

  A doctor missing from that list has not been registered at
  {row['hospital_id']} yet. Ask for them to be added; it takes a moment and
  needs no change on this PC.


IF SOMETHING IS WRONG
{'-' * 62}

  "This PC is not enrolled"
      The token was not accepted - most often it was mistyped, already
      used on another machine, or has expired. Ask for a new one.

  The page loads but Start does nothing
      Check the address is exactly https://aim-scribe-exe.vercel.app.
      The agent refuses any other address, including Vercel preview
      links, which is what stops a stray web page recording a patient.

  Recording works but nothing uploads
      Normal on a poor connection. Audio is held encrypted on the PC -
      about 135 hours of it - and uploads when the network recovers.
      Nothing is deleted until the hospital's archive has a verified copy.
"""


def main() -> int:
    if len(sys.argv) < 2:
        print(__doc__)
        return 1

    source = Path(sys.argv[1])
    if not source.is_file():
        print(f"No such file: {source}")
        return 1

    key = admin_key()
    if not key:
        print("AIMS_ADMIN_KEY is not set, and no .env was found beside the backend.")
        return 1

    rows = list(csv.DictReader(source.open(encoding="utf-8-sig")))
    if not rows:
        print("The CSV has no rows.")
        return 1

    out = source.parent / f"enrolment_{datetime.now():%Y%m%d_%H%M}"
    out.mkdir(parents=True, exist_ok=True)
    expires = datetime.now(timezone.utc) + timedelta(hours=TTL_HOURS)

    print(f"backend  {BACKEND}")
    print(f"machines {len(rows)}")
    print(f"output   {out}\n")

    hospitals_done, register, failures = set(), [], 0

    for row in rows:
        hospital = (row.get("hospital_id") or "").strip()
        doctor = (row.get("doctor_id") or "").strip()
        if not hospital or not doctor:
            print(f"  skipped: a row is missing hospital_id or doctor_id: {row}")
            failures += 1
            continue

        if hospital not in hospitals_done:
            status, body = call("/api/v2/admin/hospital", {
                "hospital_id": hospital,
                "name": (row.get("hospital_name") or hospital).strip(),
                "timezone": os.getenv("AIMS_TIMEZONE", "Asia/Dhaka"),
            }, key)
            if status != 200:
                print(f"  hospital {hospital} failed: {status} {body}")
                failures += 1
                continue
            hospitals_done.add(hospital)
            print(f"  hospital {hospital} ready")

        # Register the doctor before minting the token. Enrolling a PC whose
        # doctor cannot be picked in CMED produces a machine that installs
        # cleanly and then refuses every consultation.
        status, body = call("/api/v2/admin/doctor", {
            "doctor_id": doctor, "hospital_id": hospital,
            "full_name": (row.get("doctor_name") or doctor).strip(),
            "active": True,
        }, key)
        if status != 200:
            print(f"  {doctor}: register failed: {status} {body}")
            failures += 1
            continue

        status, body = call("/api/v2/admin/enrollment-token", {
            "hospital_id": hospital, "doctor_id": doctor,
            "created_by": CREATED_BY, "ttl_hours": TTL_HOURS,
        }, key)
        if status != 200:
            print(f"  {doctor}: token failed: {status} {body}")
            failures += 1
            continue

        token = body["enrollment_token"]
        row.setdefault("doctor_name", doctor)
        row.setdefault("hospital_name", hospital)
        (out / f"{hospital}_{doctor}.txt").write_text(
            instructions(row, token, expires), encoding="utf-8", newline="\r\n")

        register.append({
            "hospital_id": hospital, "doctor_id": doctor,
            "doctor_name": row.get("doctor_name", ""), "room": row.get("room", ""),
            "issued_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
            "expires_at": expires.isoformat(timespec="seconds"),
            "instructions_file": f"{hospital}_{doctor}.txt",
        })
        print(f"  {hospital}/{doctor}: token issued")

    if register:
        # Deliberately no token column: the register says what was issued and to
        # whom, and is safe to keep. The tokens live only in the per-PC files.
        with (out / "register.csv").open("w", newline="", encoding="utf-8") as handle:
            writer = csv.DictWriter(handle, fieldnames=list(register[0]))
            writer.writeheader()
            writer.writerows(register)

    print(f"\n{len(register)} token(s) issued, {failures} failure(s)")
    print(f"one instruction sheet per PC in {out}")
    print("register.csv records what was issued; it contains no tokens.")
    print("\nDelete that folder once the machines are installed.")
    return 0 if failures == 0 else 2


if __name__ == "__main__":
    sys.exit(main())
