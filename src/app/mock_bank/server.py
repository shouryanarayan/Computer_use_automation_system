"""
Mock "CoreBank" servicing application.

Stand-in for a legacy core-banking back-office screen: server-rendered
HTML, table-based layout, no test IDs, no ARIA labels beyond native
<label for>. This is deliberately the kind of surface described in the
assignment brief - the only reliable signal is what a human operator
would see (accessible role + name), not a clean DOM with stable
selectors.

Also encodes the runtime conditions a replay has to handle:
  - a dismissible interstitial on first load (RECOVERABLE)
  - an invalid member ID format (BUSINESS_OUTCOME / validation)
  - a member ID with no matching record (BUSINESS_OUTCOME / not found)
  - a locked account (BUSINESS_OUTCOME / permission denied)
  - a member requiring supervisor step-up verification, which is not
    part of the recorded capability's known steps (triggers HITL)
"""
import json
import re
from pathlib import Path

from flask import Flask, redirect, render_template, request, session

app = Flask(__name__)
app.secret_key = "dev-only-not-a-real-secret"

DATA_PATH = Path(__file__).parent / "synthetic_data.json"
MEMBER_ID_RE = re.compile(r"^\d{5}$")


def _load_members():
    with open(DATA_PATH) as f:
        return {m["member_id"]: m for m in json.load(f)["members"]}


@app.route("/", methods=["GET"])
def home():
    if not session.get("notice_dismissed"):
        return render_template("notice.html")
    return render_template("search.html", error=None)


@app.route("/notice/dismiss", methods=["POST"])
def dismiss_notice():
    session["notice_dismissed"] = True
    return redirect("/")


@app.route("/search", methods=["POST"])
def search():
    member_id = (request.form.get("member_id") or "").strip()
    members = _load_members()

    if not MEMBER_ID_RE.match(member_id):
        return render_template(
            "search.html",
            error="Invalid Member ID. Enter a 5-digit numeric ID.",
        )

    member = members.get(member_id)
    if member is None:
        return render_template("not_found.html", member_id=member_id)

    if member["status"] == "locked":
        return render_template("access_denied.html")

    if member["status"] == "step_up_required" and not session.get(
        f"verified_{member_id}"
    ):
        return render_template("verify.html", member_id=member_id)

    return redirect(f"/member/{member_id}")


@app.route("/verify/approve/<member_id>", methods=["POST"])
def approve_verification(member_id):
    session[f"verified_{member_id}"] = True
    return redirect(f"/member/{member_id}")


@app.route("/member/<member_id>", methods=["GET"])
def member_detail(member_id):
    members = _load_members()
    member = members.get(member_id)
    if member is None:
        return render_template("not_found.html", member_id=member_id)
    return render_template("member.html", member=member)


@app.route("/reset", methods=["GET"])
def reset():
    session.clear()
    return redirect("/")


if __name__ == "__main__":
    app.run(host="127.0.0.1", port=8000, debug=False)
