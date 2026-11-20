"""Step 1 access control: a dev role switcher (X-User-Id header) and a permission table.

Real authentication (passwords/sessions) is a later step; the DB audit trail is already attributed to
the acting user, so swapping this module out does not change the rest of the app.
"""
from fastapi import Header, HTTPException

from app.db import tx

PERMISSIONS: dict[str, set[str]] = {
    "tb.import": {"MAKER", "PARTNER"},
    "mapping.edit": {"MAKER", "PARTNER"},
    "adjustments.create": {"MAKER", "PARTNER"},          # drafts and reversal drafts
    "adjustments.approve": {"CHECKER", "PARTNER"},       # plus the rule that approver != creator (adjustments_service)
    "issues.create": {"MAKER", "CHECKER", "PARTNER"},
    "issues.clear": {"MAKER", "CHECKER", "PARTNER"},     # manual issues only; system issues clear themselves
    "wp.attach": {"MAKER", "CHECKER", "PARTNER"},        # attach or remove an UNREVIEWED link
    "wp.review": {"CHECKER", "PARTNER"},                 # plus the rule reviewer != attacher (working_papers_service)
    "pack.export": {"MAKER", "CHECKER", "PARTNER"},      # printing / downloading the pack changes no data; it is only logged
    "materiality.set": {"CHECKER", "PARTNER"},           # a reviewer-level judgement: Makers can view it but not set it
    "tds.edit": {"MAKER", "CHECKER", "PARTNER"},         # add / import / delete DRAFT TDS-TCS rows
    "tds.post": {"CHECKER", "PARTNER"},                  # post rows to the adjusted TB (plus: not the person who entered them)
    "fd.edit": {"MAKER", "CHECKER", "PARTNER"},
    "fd.post": {"CHECKER", "PARTNER"},
    "recon.edit": {"MAKER", "CHECKER", "PARTNER"},       # add / import / delete reconciliation items
    "recon.propose": {"MAKER", "CHECKER", "PARTNER"},    # creates DRAFT journals only; approval stays maker-checker on the Adjustments tab
    "gstr9c.edit": {"MAKER", "CHECKER", "PARTNER"},
    "lfar.edit": {"MAKER", "CHECKER", "PARTNER"},        # loan schedule and checklist remarks (frozen at sign-off)
    "issues.assign": {"MAKER", "CHECKER", "PARTNER"},    # give an issue an owner
    "issues.approve": {"CHECKER", "PARTNER"},            # accept an open issue with a reason (the Maker cannot approve)
    "prior_year.edit": {"MAKER", "CHECKER", "PARTNER"},  # prior-year comparatives (frozen at sign-off)
    "signoff.perform": {"PARTNER"},
    "signoff.reopen": {"PARTNER"},                       # formal re-opening of a signed-off engagement (needs a reason)
    "udin.manage": {"PARTNER"},                          # record / revoke the ICAI UDIN of a signed version
    "checks.run": {"MAKER", "CHECKER", "PARTNER"},       # re-running is read-only on business data, so reviewers may do it
}


def current_user(x_user_id: int | None = Header(default=None)) -> dict:
    if x_user_id is None:
        raise HTTPException(401, "Select a user (X-User-Id header missing).")
    with tx() as cur:
        cur.execute("""SELECT u.id, u.full_name, coalesce(array_agg(ur.role_code) FILTER (WHERE ur.role_code IS NOT NULL), '{}') AS roles
                         FROM users u LEFT JOIN user_roles ur ON ur.user_id = u.id
                        WHERE u.id = %s AND u.is_active AND u.id <> 1 GROUP BY u.id""", (x_user_id,))
        user = cur.fetchone()
    if not user:
        raise HTTPException(401, "Unknown or inactive user.")
    return user


def require(permission: str):
    def dep(x_user_id: int | None = Header(default=None)) -> dict:
        user = current_user(x_user_id)
        if not PERMISSIONS[permission] & set(user["roles"]):
            raise HTTPException(403, f"Your role ({', '.join(user['roles'])}) cannot perform '{permission}'.")
        return user
    return dep
