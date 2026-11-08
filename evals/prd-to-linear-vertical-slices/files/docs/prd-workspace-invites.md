# PRD: Invite teammates to a workspace

**Owner:** Priya (PM) · **Status:** Approved · **Target:** Q1 release

## Problem

Today a workspace admin has to email support to add a teammate. Support tickets about access are 18% of
volume, and trial workspaces that never add a second member convert at 1/4 the rate of those that do.

## Goals

- Admins can invite teammates themselves, by email, in under a minute.
- Invitees can join with one click, whether or not they already have an account.

## Users

- **Workspace admin**: invites, resends, and revokes invites; sees who is pending.
- **Invitee**: receives an email, accepts, lands in the workspace.
- **Member** (non-admin): cannot invite (for now).

## Requirements

- **R1** – Admins can invite one or more people by email from *Settings → Members* (up to 10 addresses per submit). Each invite picks a role: Member or Admin (default Member).
- **R2** – Each invitee receives an email (via our existing transactional mailer) with a unique, single-use accept link.
- **R3** – Invites expire after **7 days**. Expired links show a friendly "ask your admin for a new invite" page.
- **R4** – Accepting while logged in to an account with the invited email adds the user to the workspace with the chosen role and redirects to the workspace home.
- **R5** – Accepting without an account starts sign-up with the email pre-filled and locked; after sign-up the user joins the workspace automatically.
- **R6** – The Members page lists pending invites (email, role, invited by, sent date, expiry) with **Resend** (new link, resets the 7 days, old link stops working) and **Revoke** actions.
- **R7** – Inviting an email that is already a member or already has a pending invite shows an inline error for that address and does not send.
- **R8** – Abuse protection: max **20 invites per workspace per hour**; over the limit the form shows how long to wait.

## Non-functional

- Accept-link tokens are random (≥128 bits), stored hashed, and never logged.
- All invite actions are written to the existing workspace audit log.
- Invite emails must render in dark mode and pass our email accessibility checklist.

## Non-goals (this release)

- SSO / SCIM provisioning.
- Bulk invite by CSV upload.
- Invite links that anyone can use (shareable join links).
- Letting non-admin members invite.

## Success metrics

- 50% fewer access-related support tickets within 6 weeks.
- ≥ 30% of trial workspaces have 2+ members by day 7.

## Open questions

- Should revoking an invite notify the invitee? (Leaning no.)
